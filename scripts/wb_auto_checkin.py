#!/usr/bin/env python3
"""gaoren-workbuddy-checkin · WorkBuddy Buddy 加油站每日签到（自研实现）

作者：gaoren ｜ 许可证：MIT ｜ 零第三方依赖，仅用 Python 标准库

三条工作通道（自动择一）：
  A 本地登录态：解密本机登录态里的 accessToken（Windows，需客户端在运行）
  B 令牌文件  ：读 ~/.workbuddy/gaoren-checkin/token.txt（客户端不可用时）
  C 环境变量  ：读 GAOREN_ACCESS_TOKEN

用法：
  wb_auto_checkin.py            签到（幂等，已签则跳过），可选处理猫猫旅行
  wb_auto_checkin.py --probe    只读探测：只查状态，绝不发起任何写请求
  wb_auto_checkin.py --no-travel 只签到，不碰旅行
  wb_auto_checkin.py --json     输出 JSON（给 agent 解析）
  wb_auto_checkin.py --self-test 跑加密自测（离线）

安全约定：token 只在内存中使用，日志与输出里永不出现明文与前后缀。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wb_crypto as crypto  # noqa: E402

APP_NAME = "gaoren-checkin"
TRAVEL_HOST = "https://www.workbuddy.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 WorkBuddy/%s"
      % os.environ.get("GAOREN_CLIENT_VER", "5.7.6"))

# 余额字段候选名（接口在不同版本用过单数/复数）
BALANCE_KEYS = ("total_credits", "total_credit", "balance", "credits", "credit")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log_line(path: str, line: str) -> None:
    """追加一行日志（默认脱敏，绝不写 token）。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


def default_log() -> str:
    env = os.environ.get("GAOREN_CHECKIN_LOG")
    if env:
        return env
    home = os.environ.get("GAOREN_HOME") or os.path.join(
        os.path.expanduser("~"), ".workbuddy", "gaoren-checkin")
    return os.path.join(home, "checkin.log")


def login_state_path() -> str:
    env = os.environ.get("GAOREN_LOGIN_STATE")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "CodeBuddyExtension", "Data", "Public",
                            "auth", "workbuddy-desktop.info")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/"
                                  "CodeBuddyExtension/Data/Public/auth/"
                                  "workbuddy-desktop.info")
    return os.path.expanduser("~/.config/CodeBuddyExtension/Data/Public/"
                              "auth/workbuddy-desktop.info")


def _unprotected(value):
    """兼容 5.6.2 前后的两种登录态：明文字符串 / 加密信封。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("envelope"):
        return None      # 需要密钥，交给调用方解密
    return None


def read_local_credentials() -> tuple[str | None, str | None, str]:
    """返回 (access_token, domain, 来源说明)。拿不到就返回 (None, None, 原因)。"""
    # 通道 C：环境变量
    env_token = os.environ.get("GAOREN_ACCESS_TOKEN")
    if env_token and env_token.count(".") == 2:
        return env_token, os.environ.get("GAOREN_AUTH_DOMAIN", "www.codebuddy.cn"), "环境变量"

    # 通道 B：令牌文件
    token_file = os.path.join(os.environ.get("GAOREN_HOME") or os.path.join(
        os.path.expanduser("~"), ".workbuddy", "gaoren-checkin"), "token.txt")
    if os.path.isfile(token_file):
        try:
            tok = open(token_file, encoding="utf-8").read().strip()
            if tok.count(".") == 2:
                dom = ""
                dom_file = os.path.join(os.path.dirname(token_file), "domain.txt")
                if os.path.isfile(dom_file):
                    dom = open(dom_file, encoding="utf-8").read().strip()
                return tok, dom or "www.codebuddy.cn", "令牌文件"
        except OSError:
            pass

    # 通道 A：本机登录态
    path = login_state_path()
    if not os.path.isfile(path):
        return None, None, "找不到登录态文件"
    try:
        state = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, None, "登录态读取失败：%s" % exc

    auth = state.get("auth") or {}
    domain = auth.get("domain") or "www.codebuddy.cn"
    raw = auth.get("accessToken")
    plain = _unprotected(raw)
    if plain:
        return plain, domain, "登录态（明文）"

    if not isinstance(raw, dict) or not raw.get("envelope"):
        return None, None, "登录态里没有 accessToken"

    try:
        env = json.loads(base64.b64decode(raw["envelope"]))
    except (ValueError, TypeError):
        return None, None, "信封解析失败"
    key_id = env.get("keyId")
    if not key_id:
        return None, None, "信封缺少 keyId"

    key, source = crypto.obtain_key(key_id, data_dir=os.path.dirname(path),
                                    verbose=bool(os.environ.get("GAOREN_VERBOSE")))
    if not key:
        return None, None, ("取不到运行时密钥（客户端是否在运行？"
                            "或用 GAOREN_ACCESS_TOKEN / token.txt 提供令牌）")
    try:
        token = crypto.open_envelope(raw["envelope"], key)
    except (ValueError, KeyError) as exc:
        return None, None, "登录态解密失败：%s" % exc
    return token, domain, "登录态（解密，密钥来自%s）" % source


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_json(url: str, token: str, method: str = "POST", body: dict | None = None,
              timeout: int = 20) -> tuple[int, dict]:
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw) if raw.strip() else {}
        except ValueError:
            return exc.code, {"msg": raw[:200]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"msg": "网络错误：%s" % exc}


def pick(data: dict, *names, default=None):
    for n in names:
        if isinstance(data, dict) and data.get(n) is not None:
            return data[n]
    return default


# ---------------------------------------------------------------------------
# 业务动作
# ---------------------------------------------------------------------------

def fetch_status(token: str, domain: str) -> dict:
    url = "https://%s/v2/billing/meter/checkin-activity-status" % domain
    code, body = http_json(url, token)
    if code != 200:
        raise RuntimeError("状态查询失败 HTTP %s：%s" % (code, body.get("msg", "")))
    return body


def do_claim(token: str, domain: str) -> dict:
    url = "https://%s/v2/billing/meter/daily-checkin" % domain
    http, body = http_json(url, token)
    code = body.get("code")
    msg = str(body.get("msg") or "")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    already = (code == 10001 or "已签到" in msg
               or (http == 400 and "已签到" in msg))
    if http == 200 and code == 0:
        return {"action": "claimed",
                "credit": pick(data, "credit") or body.get("credit"),
                "streak": pick(data, "streak_days") or body.get("streak_days")}
    if already:
        return {"action": "skip_already_signed", "credit": 0,
                "streak": pick(data, "streak_days"), "msg": msg}
    raise RuntimeError("领取失败 HTTP %s code=%s：%s" % (http, code, msg or body))


def handle_travel(token: str, allow_depart: bool = True) -> dict:
    """先领后派：已到达先领取；空闲且未达上限才派遣（不满足则完全不发写请求）。"""
    out: dict = {"note": ""}
    http, body = http_json(TRAVEL_HOST + "/activity/growth/buddy/travel/status",
                           token, method="GET")
    if http != 200 or body.get("code") != 0:
        out["note"] = "旅行状态查询失败"
        return out
    d = body.get("data") or {}
    state = d.get("state")
    out["state"] = state
    out["reward_credit"] = d.get("reward_credit")
    out["daily_limit_reached"] = d.get("daily_limit_reached")

    if state == "arrived":
        hc, bc = http_json(TRAVEL_HOST + "/activity/growth/buddy/travel/claim", token)
        out["claim"] = "ok" if hc == 200 and bc.get("code") == 0 else \
            "failed:%s" % (bc.get("msg") or hc)
    if state == "idle" and allow_depart and not d.get("daily_limit_reached"):
        hc, bc = http_json(TRAVEL_HOST + "/activity/growth/buddy/travel/depart",
                           token, body={"location_id": 1})
        out["depart"] = "ok" if hc == 200 and bc.get("code") == 0 else \
            "failed:%s" % (bc.get("msg") or hc)
    elif state == "idle":
        out["note"] = "今日派遣次数已用完，未发派遣请求"
    return out


def summarize(status_body: dict) -> dict:
    data = status_body.get("data") or {}
    return {
        "active": data.get("active"),
        "today_checked_in": data.get("today_checked_in"),
        "streak_days": data.get("streak_days"),
        "daily_credit": data.get("daily_credit"),
        "today_credit": data.get("today_credit"),
        "balance": pick(data, *BALANCE_KEYS),
        "end_time": data.get("end_time"),
        "is_streak_day": data.get("is_streak_day"),
        "streak_bonus_credit": data.get("streak_bonus_credit"),
        "streak_bonus_days": data.get("streak_bonus_days"),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(args) -> int:
    t0 = time.time()
    log = default_log()
    token, domain, source = read_local_credentials()
    if not token:
        print("❌ 无法取得凭据：%s" % source)
        print("   客户端需处于运行登录状态；也可把 accessToken 写入 "
              "~/.workbuddy/gaoren-checkin/token.txt")
        log_line(log, "%s | status=error | msg=%s" % (now(), source))
        return 3

    try:
        status_body = fetch_status(token, domain)
    except RuntimeError as exc:
        print("❌ %s" % exc)
        log_line(log, "%s | status=error | msg=%s" % (now(), exc))
        return 1

    info = summarize(status_body)

    # --probe：只读，到此为止
    if args.probe:
        result = {"mode": "probe", "status": "ok", "source": source,
                  "domain": domain, "token_len": len(token), **info}
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else
              "✅ 探测通过｜来源 %s｜域名 %s｜今日已签 %s｜连续 %s 天｜余额 %s"
              % (source, domain, info["today_checked_in"],
                 info["streak_days"], info["balance"]))
        log_line(log, "%s | status=ok | action=probe | streak=%s | balance=%s | src=%s"
                 % (now(), info["streak_days"], info["balance"], source))
        return 0

    try:
        claim = do_claim(token, domain)
    except RuntimeError as exc:
        print("❌ %s" % exc)
        log_line(log, "%s | status=error | action=claim_failed | msg=%s"
                 % (now(), exc))
        return 1

    travel = {} if args.no_travel else handle_travel(token, allow_depart=not args.no_depart)

    # 领取后再查一次状态，拿权威余额（领取成功时打印的余额是领取前的值）
    try:
        after = summarize(fetch_status(token, domain))
    except RuntimeError:
        after = {}
    balance = after.get("balance", info.get("balance"))
    streak = after.get("streak_days") or claim.get("streak") or info["streak_days"]

    result = {"mode": "run", "status": "ok", "action": claim["action"],
              "credit": claim.get("credit"), "streak_days": streak,
              "balance": balance, "source": source, "domain": domain,
              "before": info, "travel": travel,
              "elapsed": round(time.time() - t0, 2)}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        head = {"claimed": "✅ 领取成功", "skip_already_signed": "☑️ 今日已签到，跳过"}[
            claim["action"]]
        print("%s +%s 分｜连续 %s 天｜余额 %s" %
              (head, claim.get("credit") or 0, streak, balance))
        if travel:
            print("   猫猫旅行：%s" % json.dumps(travel, ensure_ascii=False))

    log_line(log, "%s | status=ok | action=%s | credit=%s | streak=%s | balance=%s "
                  "| src=%s | travel=%s | %.1fs"
             % (now(), claim["action"], claim.get("credit"), streak, balance,
                source, json.dumps(travel, ensure_ascii=False) if travel else "-",
                time.time() - t0))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="WorkBuddy 加油站自动签到（自研）")
    p.add_argument("--probe", action="store_true", help="只读探测，不发任何写请求")
    p.add_argument("--no-travel", action="store_true", help="不处理猫猫旅行")
    p.add_argument("--no-depart", action="store_true", help="不派遣，只领已达成的奖励")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.add_argument("--self-test", action="store_true", help="跑加密自测（离线）")
    args = p.parse_args()
    if args.self_test:
        return crypto.self_test()
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
