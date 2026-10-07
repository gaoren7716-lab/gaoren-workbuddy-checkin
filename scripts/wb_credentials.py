"""gaoren-workbuddy-checkin · 直连通道（备用路径，需要接触登录凭据）

**为什么单独成文件**：凭据相关代码（读登录态、解密 token）在安全审查里属于高敏感面。
本模块只在用户显式要求直连通道（`--channel A/C/B`）时才会被导入；
**客户端 RPC 通道（默认通道 D）完全不需要它，市场分发版不包含本文件。**

本模块的自我约束：
  · 只**读**登录态，不写、不改、不上传
  · token 只在内存中使用，日志与输出里不出现明文与前后缀
  · 不做任何认证解析之外的凭据处理
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import ssl
import sys

import wb_crypto as crypto


def home_dir() -> str:
    return os.environ.get("GAOREN_HOME") or os.path.join(
        os.path.expanduser("~"), ".workbuddy", "gaoren-checkin")


def login_state_path() -> str:
    env = os.environ.get("GAOREN_LOGIN_STATE")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "CodeBuddyExtension", "Data", "Public", "auth",
                            "workbuddy-desktop.info")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/CodeBuddyExtension"
                                  "/Data/Public/auth/workbuddy-desktop.info")
    return os.path.expanduser("~/.config/CodeBuddyExtension/Data/Public/auth/"
                              "workbuddy-desktop.info")


class HttpTransport:
    """直连官方接口：连接复用省 TLS 握手。"""

    name = "direct"

    def __init__(self, token: str, domain: str, label: str) -> None:
        self.name = "direct"
        self.token = token
        self.domain = domain
        self.label = label
        self._conn: http.client.HTTPSConnection | None = None

    def _connection(self) -> http.client.HTTPSConnection:
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(
                self.domain, timeout=20, context=ssl.create_default_context())
        return self._conn

    def request(self, method: str, path: str, body: dict | None = None,
                query: dict | None = None) -> tuple[int, dict]:
        import urllib.parse
        url = path + ("?" + urllib.parse.urlencode(
            {k: str(v) for k, v in query.items()}) if query else "")
        payload = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
        headers = {
            "Authorization": "Bearer %s" % self.token,
            "Accept": "application/json",
            "User-Agent": os.environ.get("GAOREN_UA", "gaoren-checkin"),
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        for attempt in (1, 2):
            try:
                conn = self._connection()
                conn.request(method, url, body=payload, headers=headers)
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else {})
            except (http.client.HTTPException, OSError) as exc:
                self.close()
                if attempt == 2:
                    return 0, {"msg": "网络错误：%s" % exc}
        return 0, {"msg": "未知网络错误"}

    def close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except OSError:
            pass
        finally:
            self._conn = None


def _from_login_state() -> tuple[str | None, str, str]:
    path = login_state_path()
    if not os.path.isfile(path):
        return None, "", "找不到登录态"
    state = json.load(open(path, encoding="utf-8"))
    auth = state.get("auth") or {}
    domain = auth.get("domain") or "www.codebuddy.cn"
    raw = auth.get("accessToken")
    if isinstance(raw, str) and raw:
        return raw, domain, "明文"
    if not isinstance(raw, dict) or not raw.get("envelope"):
        return None, "", "登录态无 accessToken"
    env = json.loads(base64.b64decode(raw["envelope"]))
    key, source = crypto.obtain_key(env["keyId"], data_dir=os.path.dirname(path),
                                    verbose=bool(os.environ.get("GAOREN_VERBOSE")))
    if not key:
        return None, "", "取不到运行时密钥"
    try:
        return crypto.open_envelope(raw["envelope"], key), domain, "解密/密钥来自%s" % source
    except (ValueError, KeyError) as exc:
        return None, "", "解密失败：%s" % exc


def open_direct() -> tuple["HttpTransport | None", str]:
    """按 A → C → B 顺序尝试直连通道。"""
    try:
        tok, dom, src = _from_login_state()
        if tok:
            return HttpTransport(tok, dom, "登录态解密（%s）" % src), ""
    except (OSError, ValueError, KeyError):
        pass
    tok = os.environ.get("GAOREN_ACCESS_TOKEN", "").strip()
    if tok.count(".") == 2:
        return HttpTransport(tok, os.environ.get("GAOREN_AUTH_DOMAIN",
                              "www.codebuddy.cn"), "环境变量"), ""
    f = os.path.join(home_dir(), "token.txt")
    if os.path.isfile(f):
        try:
            tok = open(f, encoding="utf-8").read().strip()
            if tok.count(".") == 2:
                dom = "www.codebuddy.cn"
                df = os.path.join(home_dir(), "domain.txt")
                if os.path.isfile(df):
                    dom = open(df, encoding="utf-8").read().strip() or dom
                return HttpTransport(tok, dom, "令牌文件"), ""
        except OSError:
            pass
    return None, "无可用直连凭据"
