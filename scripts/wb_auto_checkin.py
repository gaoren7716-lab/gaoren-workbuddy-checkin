#!/usr/bin/env python3
"""gaoren-workbuddy-checkin · WorkBuddy 加油站每日签到（自研）

作者：gaoren ｜ MIT ｜ 零第三方依赖，仅用 Python 标准库

四条凭据/传输通道（自动择一，优先级从高到低）：
  D 客户端本机 RPC（wbipc 命名管道）—— 零凭据，鉴权由客户端附加，不受登录态格式影响
  A 本机登录态解密（Windows）        —— 自研 AES-256-GCM，密钥取自客户端进程内存
  C 环境变量 GAOREN_ACCESS_TOKEN
  B 令牌文件 ~/.workbuddy/gaoren-checkin/token.txt

三步业务（全部幂等，可重复执行）：
  ① checkin 每日签到        ② streak 每月连签档位（兑换需 --redeem）
  ③ travel 猫猫旅行先领后派（抽奖需 --lottery）

用法：
  wb_auto_checkin.py                 签到 + 连签查看 + 旅行
  wb_auto_checkin.py --probe         只读探测，绝不发起任何写请求
  wb_auto_checkin.py --doctor        环境自检（改版后第一件事）
  wb_auto_checkin.py --redeem        额外执行连签档位兑换（消费型，默认不做）
  wb_auto_checkin.py --lottery       额外执行连登抽奖（有随机性，默认不做）
  wb_auto_checkin.py --json          结构化输出
  wb_auto_checkin.py --self-test     离线加密自测

安全约定：任何通道都不在日志/输出里出现 token 明文或前后缀。
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import ssl
import sys
import time
import urllib.parse
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wbipc_client import (            # noqa: E402
    WbipcClient, WbipcError, is_available, load_endpoint)

APP_VERSION = "1.2.1"   # 单一事实来源：SKILL.md 的 version 与握手上报的客户端版本都取这里
TRAVEL_HOST_PATH = "/activity/growth/buddy/travel"
# 结果契约：每一步的合法状态集合（agent 只按这张表判断，不要自行推理）
STATUS_SET = {
    "checkin": ("success", "already_checked", "skipped"),
    "streak": ("ready", "redeemed", "locked", "skipped"),
    "lottery": ("drawn", "no_chance", "skipped"),
    "travel": ("departed", "arrived_claimed", "claimed", "traveling",
               "idle_limit", "skipped"),
}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def home_dir() -> str:
    return os.environ.get("GAOREN_HOME") or os.path.join(
        os.path.expanduser("~"), ".workbuddy", "gaoren-checkin")


def log_path() -> str | None:
    """日志默认**不落盘**；只有显式设置 GAOREN_CHECKIN_LOG 才写文件。"""
    return os.environ.get("GAOREN_CHECKIN_LOG") or None


def write_log(line: str) -> None:
    """追加一行结果到日志。默认无操作 —— 本技能默认不写任何文件。"""
    path = log_path()
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 传输层
# ---------------------------------------------------------------------------

class Transport:
    name = "?"

    def request(self, method: str, path: str, body: dict | None = None,
                query: dict | None = None) -> tuple[int, dict]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class WbipcTransport(Transport):
    """通道 D：走客户端本机 RPC，鉴权由客户端附加。"""

    name = "wbipc"

    def __init__(self) -> None:
        endpoint, ticket = load_endpoint()
        self.c = WbipcClient(endpoint, ticket, client_version=APP_VERSION)
        self.c.handshake()
        self.channel = self.c.get_pipe()

    def request(self, method, path, body=None, query=None):
        res = self.c.http(method, path, body, query, channel=self.channel)
        raw = res.get("body_b64") or ""
        try:
            data = json.loads(base64.b64decode(raw).decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            data = {}
        return int(res.get("status") or 0), data

    def close(self) -> None:
        self.c.close()


def _load_credentials_module():
    """懒加载直连凭据模块。

    **市场分发版不包含该模块** —— 默认通道（客户端 RPC）零凭据，
    不读登录态、不解密 token、不碰任何凭据。
    只有显式指定 `--channel A/C/B` 时才会用到它。
    """
    try:
        import wb_credentials
        return wb_credentials
    except ImportError:
        return None


def open_transport(prefer: str = "auto") -> tuple[Transport | None, str]:
    """选通道。默认只用 D（零凭据）；显式指定 A/C/B 时才加载凭据模块。"""
    if prefer in ("A", "C", "B"):
        cred = _load_credentials_module()
        if cred is None:
            return None, "本发行版不含直连凭据模块（仅客户端 RPC 通道）"
        return cred.open_direct()
    try:
        return WbipcTransport(), "客户端本机 RPC（零凭据）"
    except (WbipcError, OSError, ValueError, KeyError) as exc:
        return None, "客户端 RPC 不可用：%s" % exc


# ---------------------------------------------------------------------------
# 业务
# ---------------------------------------------------------------------------

def api(t: Transport, method: str, path: str, body: dict | None = None) -> dict:
    status, data = t.request(method, path, body)
    if status == 0:
        raise RuntimeError(data.get("msg") or "请求失败")
    if status == 401:
        raise RuntimeError("登录态失效（HTTP 401），请打开 WorkBuddy 客户端刷新后重试")
    return data


def pick(d: dict, *names, default=None):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return default


def step_checkin(t: Transport) -> dict:
    # 先直接领（省一次请求）；只有拿到非预期错误时才回查状态判断原因。
    res = api(t, "POST", "/v2/billing/meter/daily-checkin")
    code = res.get("code")
    msg = str(res.get("msg") or "")
    if code == 0:
        data = res.get("data") or {}
        return {"name": "checkin", "status": "success",
                "credit": pick(data, "credit") or res.get("credit"),
                "streak_days": pick(data, "streak_days") or res.get("streak_days")}
    if code == 10001 or "已签到" in msg:
        return {"name": "checkin", "status": "already_checked", "detail": msg or "今日已签到"}
    # 异常路径：回查一次，确认是活动结束还是别的问题
    try:
        st = api(t, "POST", "/v2/billing/meter/checkin-activity-status")
        d = st.get("data") or {}
        if d.get("active") is False:
            return {"name": "checkin", "status": "skipped",
                    "detail": "本期活动已结束（active=false）"}
    except RuntimeError:
        pass
    return {"name": "checkin", "status": "error", "code": code, "detail": msg}


def step_streak(t: Transport, do_redeem: bool = False) -> dict:
    res = api(t, "GET", "/activity/growth/streak")
    d = res.get("data") or {}
    s = d.get("streak") or {}
    rs = d.get("redemption_status") or {}
    out = {"name": "streak", "status": "ready", "days": s.get("days"),
           "next_tier": s.get("next_tier"), "remaining": s.get("next_tier_remaining"),
           "makeup_cards": pick(d, "makeup_cards", default={}).get("balance")
           if isinstance(d.get("makeup_cards"), dict) else None}
    if not do_redeem:
        ready = [k[5:-7] for k in rs
                 if k.endswith("_status") and rs.get(k) == "available"
                 and rs.get(k.replace("_status", "_count"), 0) == 0]
        out["redeemable"] = ready
        return out
    redeemed = []
    for tier in ("7d", "14d", "28d"):
        if rs.get("tier_%s_status" % tier) == "available" and \
                not rs.get("tier_%s_count" % tier):
            r = api(t, "POST", "/activity/growth/redeem",
                    {"tier": tier, "client_token": str(uuid.uuid4())})
            if r.get("code") == 0:
                redeemed.append({"tier": tier,
                                 "credit": pick(r.get("data") or {}, "credit")})
    out["redeemed"] = redeemed
    out["status"] = "redeemed" if redeemed else "locked"
    return out


def step_lottery(t: Transport, do_draw: bool = False) -> dict:
    if not do_draw:
        return {"name": "lottery", "status": "skipped", "detail": "未加 --lottery"}
    res = api(t, "GET", "/activity/growth/lottery/summary")
    chances = pick(res.get("data") or {}, "chances", default=0)
    if not chances:
        return {"name": "lottery", "status": "no_chance", "chances": 0}
    r = api(t, "POST", "/activity/growth/lottery/draw",
            {"client_token": str(uuid.uuid4())})
    if r.get("code") == 0:
        return {"name": "lottery", "status": "drawn",
                "prize": pick(r.get("data") or {}, "prize_name")}
    return {"name": "lottery", "status": "error", "detail": str(r.get("msg") or "")}


def step_travel(t: Transport, do_depart: bool = True) -> dict:
    res = api(t, "GET", TRAVEL_HOST_PATH + "/status")
    d = res.get("data") or {}
    state = d.get("state")
    out = {"name": "travel", "status": "skipped", "state": state,
           "reward_credit": d.get("reward_credit"),
           "daily_limit_reached": d.get("daily_limit_reached")}
    if state == "arrived":
        r = api(t, "POST", TRAVEL_HOST_PATH + "/claim")
        out["status"] = "claimed" if r.get("code") == 0 else "error"
        if r.get("code") != 0:
            out["detail"] = str(r.get("msg") or "")
        return out
    if state == "traveling":
        out["status"] = "traveling"
        out["arrive_at"] = d.get("arrive_at")
        return out
    if state == "idle":
        if not do_depart:
            out["detail"] = "未启用派遣"
            return out
        if d.get("daily_limit_reached"):
            out["status"] = "idle_limit"
            return out
        r = api(t, "POST", TRAVEL_HOST_PATH + "/depart", {"location_id": 1})
        out["status"] = "departed" if r.get("code") == 0 else "error"
        if r.get("code") != 0:
            out["detail"] = str(r.get("msg") or "")
        return out
    return out


def read_balance(t: Transport, with_resource: bool = True) -> dict:
    """两个口径分开报：加油站积分（total_credits）与资源额度（Packages 剩余）。

    with_resource=False 时跳过资源额度查询（省一次请求，输出里就没有该字段）。
    """
    out = {}
    try:
        st = api(t, "POST", "/v2/billing/meter/checkin-activity-status")
        d = st.get("data") or {}
        out["checkin_balance"] = pick(d, "total_credits", "total_credit", "balance")
        out["streak_days"] = d.get("streak_days")
        out["end_time"] = d.get("end_time")
        out["active"] = d.get("active")
    except RuntimeError:
        pass
    if not with_resource:
        return out
    try:
        rs = api(t, "POST", "/billing/meter/get-user-resource-summary")
        pk = (rs.get("data") or {}).get("Packages") or []
        total = 0.0
        for p in pk:
            try:
                total += float(p.get("CycleRemainCapacity") or 0)
            except (TypeError, ValueError):
                pass
        out["resource_credits"] = round(total, 2)
    except RuntimeError:
        pass
    return out


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def doctor() -> int:
    checks: list[tuple[str, bool, str]] = []
    print("gaoren-workbuddy-checkin %s ｜ 环境自检 %s\n" % (APP_VERSION, now()))

    ok, msg = is_available(client_version=APP_VERSION)
    checks.append(("客户端本机 RPC 通道", ok, msg))
    print("  %s 通道 D（客户端 RPC）：%s" % ("✅" if ok else "❌", msg))

    _cred = _load_credentials_module()
    path = _cred.login_state_path() if _cred else ""
    exists = bool(path) and os.path.isfile(path)
    detail = path if exists else "未找到（通道 D 可用时不影响）"
    if exists:
        try:
            state = json.load(open(path, encoding="utf-8"))
            raw = (state.get("auth") or {}).get("accessToken")
            if isinstance(raw, dict) and raw.get("envelope"):
                env = json.loads(base64.b64decode(raw["envelope"]))
                detail = "信封 suite=%s keyId=%s" % (env.get("suite"), env.get("keyId"))
                checks.append(("登录态信封格式", env.get("suite") == 1, detail))
                print("  ✅ 登录态信封：suite=%s（AES-256-GCM）" % env.get("suite"))
            else:
                detail = "明文登录态（客户端 < 5.6.2）"
                print("  ⚠️ 登录态：明文（客户端可能低于 5.6.2）")
        except (OSError, ValueError) as exc:
            detail = "解析失败：%s" % exc
            print("  ❌ 登录态解析失败：%s" % exc)
    else:
        print("  ⚠️ 登录态：%s" % detail)

    t, label = open_transport()
    if not t:
        print("\n结论：❌ 四条通道都不可用，无法执行。")
        return 3
    try:
        print("\n  使用通道：%s（%s）" % (t.name, label))
        bal = read_balance(t)
        checks.append(("接口连通", True, label))
        print("  ✅ 状态接口可达：连续 %s 天｜加油站余额 %s｜资源额度 %s"
              % (bal.get("streak_days"), bal.get("checkin_balance"),
                 bal.get("resource_credits")))
        if bal.get("active") is False:
            print("  ⚠️ 活动已结束（active=false），签到接口会跳过领取")
        elif bal.get("end_time"):
            print("  ℹ️ 活动截止：%s" % bal["end_time"])
        try:
            s = step_streak(t)
            print("  ✅ 连签接口可达：已连 %s 天，下一档 %s（还差 %s 天）"
                  % (s.get("days"), s.get("next_tier"), s.get("remaining")))
            if s.get("redeemable"):
                print("     ℹ️ 当前可兑换档位：%s（加 --redeem 才会兑换）"
                      % ",".join(s["redeemable"]))
        except RuntimeError as exc:
            print("  ⚠️ 连签接口：%s" % exc)
    finally:
        t.close()

    print("\n结论：%s" % ("✅ 环境正常" if any(c[1] for c in checks) else "❌ 不可用"))
    return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(args) -> int:
    t0 = time.time()
    t, label = open_transport(args.channel)
    if not t:
        print("❌ 四条通道都不可用。\n"
              "   通道 D 需要 WorkBuddy 客户端在运行；否则请把 accessToken 写入\n"
              "   %s 或设置 GAOREN_ACCESS_TOKEN。\n"
              "   先跑 --doctor 看诊断。" % os.path.join(home_dir(), "token.txt"))
        write_log("%s | status=error | msg=no_channel" % now())
        return 3
    anomalies: list[str] = []
    try:
        if args.probe:
            bal = read_balance(t)
            out = {"mode": "probe", "ok": True, "channel": t.name, "source": label,
                   **bal, "version": APP_VERSION}
            print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else
                  "✅ 探测通过｜通道 %s｜连续 %s 天｜加油站余额 %s｜资源额度 %s"
                  % (t.name, bal.get("streak_days"), bal.get("checkin_balance"),
                     bal.get("resource_credits")))
            write_log("%s | status=ok | action=probe | channel=%s | streak=%s | balance=%s"
                      % (now(), t.name, bal.get("streak_days"), bal.get("checkin_balance")))
            return 0

        steps: list[dict] = []
        try:
            steps.append(step_checkin(t))
        except RuntimeError as exc:
            steps.append({"name": "checkin", "status": "error", "detail": str(exc)})
        if not args.no_streak:
            try:
                steps.append(step_streak(t, args.redeem))
            except RuntimeError as exc:
                steps.append({"name": "streak", "status": "error", "detail": str(exc)})
        else:
            steps.append({"name": "streak", "status": "skipped", "detail": "未禁用"})
        try:
            steps.append(step_lottery(t, args.lottery))
        except RuntimeError as exc:
            steps.append({"name": "lottery", "status": "error", "detail": str(exc)})
        if not args.no_travel:
            try:
                steps.append(step_travel(t, not args.no_depart))
            except RuntimeError as exc:
                steps.append({"name": "travel", "status": "error", "detail": str(exc)})
        else:
            steps.append({"name": "travel", "status": "skipped", "detail": "未禁用"})

        for s in steps:
            legal = STATUS_SET.get(s["name"], ())
            if s.get("status") not in legal:
                s["status"] = "error"
                anomalies.append("%s 状态不在合法集合：%r" % (s["name"], s.get("status")))
            elif s["status"] == "error":
                anomalies.append("%s 失败：%s" % (s["name"], s.get("detail") or s.get("code")))

        bal = read_balance(t, with_resource=args.json)
        ok = not anomalies
        result = {"ok": ok, "version": APP_VERSION, "channel": t.name, "source": label,
                  "steps": steps, "anomalies": anomalies,
                  "balance": bal.get("checkin_balance"),
                  "resource_credits": bal.get("resource_credits"),
                  "streak_days": bal.get("streak_days"),
                  "elapsed": round(time.time() - t0, 2)}

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for s in steps:
                extra = " ".join("%s=%s" % (k, v) for k, v in s.items()
                                 if k not in ("name", "status"))
                print("  %-8s %-16s %s" % (s["name"], s["status"], extra))
            print("通道 %s｜连续 %s 天｜余额 %s｜%.2fs"
                  % (t.name, result["streak_days"], result["balance"],
                     result["elapsed"]))
            for a in anomalies:
                print("  ⚠️ %s" % a)

        write_log("%s | status=%s | channel=%s | steps=%s | balance=%s | %.2fs"
                  % (now(), "ok" if ok else "warn", t.name,
                     ",".join("%s:%s" % (s["name"], s["status"]) for s in steps),
                     bal.get("checkin_balance"), time.time() - t0))
        return 0
    finally:
        t.close()


def main() -> int:
    p = argparse.ArgumentParser(description="WorkBuddy 加油站自动签到（自研）")
    p.add_argument("--probe", action="store_true", help="只读探测，不发写请求")
    p.add_argument("--doctor", action="store_true", help="环境自检")
    p.add_argument("--channel", default="auto", choices=["auto", "D", "A", "C", "B"],
                   help="指定通道，默认自动")
    p.add_argument("--redeem", action="store_true", help="执行连签档位兑换（消费型）")
    p.add_argument("--lottery", action="store_true", help="执行连登抽奖（有随机性）")
    p.add_argument("--no-streak", action="store_true")
    p.add_argument("--no-travel", action="store_true")
    p.add_argument("--no-depart", action="store_true", help="不派遣，只领已到达的")
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        if _load_credentials_module() is None:
            print("本发行版不含加密模块（默认通道零凭据，无需加密自测）")
            return 0
        import wb_crypto
        return wb_crypto.self_test()
    if args.doctor:
        return doctor()
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
