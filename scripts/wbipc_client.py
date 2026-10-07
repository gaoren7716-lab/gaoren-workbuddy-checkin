r"""gaoren-workbuddy-checkin · WorkBuddy 客户端本机 RPC 客户端（自研实现）

通道 D：通过 WorkBuddy 桌面端在本机开的 IPC 服务代发 HTTP 请求，
**完全不接触任何凭据**——鉴权由客户端自己附加。

协议要点（逆向自客户端自身实现，未参考任何第三方代码）：
  · 传输：Windows 命名管道 `\\.\pipe\\wbipc-<id>`；POSIX 为 unix socket
  · 帧：换行分隔的 UTF-8 JSON（无长度前缀）
  · 握手：session_hello → session_challenge → session_prove → session_hello_ack
    证明值 = HMAC-SHA256(key=ticket, 长度前缀拼接('wbipc-c'|'wbipc-s', 1,
             endpoint, client_nonce, server_nonce))，base64url
    ticket_id = sha256(ticket)[:16]
  · 调用：{id, method, params, mode}；通道方法名形如 `c:wb.request/http.fetch`
  · 回复：{jsonrpc:'2.0', id, result} 或 {jsonrpc:'2.0', id, error:{code,message}}
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import time

PROTOCOL = 1
PIPE_REQUEST = "wb.request"
MAX_FRAME = 4 * 1024 * 1024


class WbipcError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message


def default_dir() -> str:
    return os.environ.get("WORKBUDDY_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".workbuddy")


def load_endpoint(path: str | None = None) -> tuple[str, str]:
    """读取 endpoint.json，返回 (endpoint, ticket)。"""
    p = path or os.path.join(default_dir(), "wbipc", "endpoint.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return d["endpoint"], d["ticket"]


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _proof(ticket: str, label: str, endpoint: str, client_nonce: str,
           server_nonce: str) -> str:
    """HMAC-SHA256(ticket, 长度前缀拼接的转录字段)，base64url。"""
    parts = ["wbipc-c" if label == "client" else "wbipc-s",
             str(PROTOCOL), endpoint, client_nonce, server_nonce]
    buf = b""
    for part in parts:
        b = part.encode("utf-8")
        buf += struct.pack(">I", len(b)) + b
    return _b64u(hmac.new(ticket.encode("utf-8"), buf, hashlib.sha256).digest())


def ticket_id(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()[:16]


class WbipcClient:
    def __init__(self, endpoint: str, ticket: str, timeout: float = 20.0):
        self.endpoint = endpoint
        self.ticket = ticket
        self.timeout = timeout
        self._io = None
        self._next_id = 0
        self.epoch = ""

    # -- 传输 -------------------------------------------------------------
    def _open(self):
        if self._io is not None:
            return self._io
        if sys.platform == "win32":
            self._io = open(self.endpoint, "r+b", buffering=0)
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect(self.endpoint)
            self._io = s.makefile("rwb")
        return self._io

    def close(self) -> None:
        try:
            if self._io is not None:
                self._io.close()
        except OSError:
            pass
        finally:
            self._io = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- 帧 ---------------------------------------------------------------
    # wbipc 用换行分隔的 JSON（\n），没有长度前缀——与浏览器桥那套不同。
    def _write(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self._open().write(data)
        flush = getattr(self._io, "flush", None)
        if flush:
            flush()

    def _read_line(self) -> bytes:
        """按 \\n 切分读取，跨 chunk 拼接。"""
        buf = b""
        while True:
            nl = buf.find(b"\n")
            if nl >= 0:
                return buf[:nl]
            chunk = self._io.read(65536)
            if not chunk:
                raise WbipcError("E_EOF", "连接被对端关闭")
            buf += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
            if len(buf) > MAX_FRAME:
                raise WbipcError("E_PROTOCOL_MISMATCH", "单帧过大")

    def _read(self) -> dict:
        while True:
            line = self._read_line()
            if not line.strip():
                continue
            return json.loads(line.decode("utf-8"))

    # -- 握手 -------------------------------------------------------------
    def handshake(self) -> dict:
        client_nonce = _b64u(os.urandom(16))
        self._write({
            "type": "session_hello",
            "protocol_min": PROTOCOL, "protocol_max": PROTOCOL,
            "client_nonce": client_nonce,
            "ticket_id": ticket_id(self.ticket),
            "client": {"kind": "cli", "id": "gaoren-checkin",
                       "version": _client_version()},
        })
        frame = self._read()
        if frame.get("type") == "session_hello_error":
            raise WbipcError("E_TICKET_INVALID", str(frame.get("code")))
        if frame.get("type") != "session_challenge":
            raise WbipcError("E_PROTOCOL_MISMATCH",
                             "期望 session_challenge，收到 %r" % frame.get("type"))
        server_nonce = frame["server_nonce"]
        want = frame.get("server_proof", "")
        got = _proof(self.ticket, "server", self.endpoint,
                     client_nonce, server_nonce)
        if not hmac.compare_digest(want, got):
            raise WbipcError("E_TICKET_INVALID", "服务端证明校验失败")
        self._write({
            "type": "session_prove",
            "client_proof": _proof(self.ticket, "client", self.endpoint,
                                   client_nonce, server_nonce),
        })
        ack = self._read()
        if ack.get("type") != "session_hello_ack":
            raise WbipcError("E_PROTOCOL_MISMATCH",
                             "握手未确认：%r" % ack.get("type"))
        self.epoch = ack.get("connection_epoch", "")
        return ack

    # -- RPC --------------------------------------------------------------
    def call(self, method: str, params: dict | None = None,
             mode: str | None = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        frame: dict = {"id": rid, "method": method, "params": params or {}}
        if mode:
            frame["mode"] = mode
        self._write(frame)
        while True:
            reply = self._read()
            if reply.get("id") != rid:      # 跳过心跳等无关帧
                continue
            if "error" in reply:
                err = reply["error"] or {}
                raise WbipcError(str(err.get("code", "E_INTERNAL")),
                                 str(err.get("message", "")))
            return reply.get("result") or {}

    def get_pipe(self, name: str = PIPE_REQUEST) -> str:
        res = self.call("broker/GetPipe", {"pipe": name})
        return res.get("channel", "")

    def http(self, method: str, path: str, body: dict | None = None,
             query: dict | None = None, channel: str | None = None) -> dict:
        ch = channel or self.get_pipe()
        params: dict = {"method": method, "path": path}
        if query:
            params["query"] = {k: str(v) for k, v in query.items()}
        if body is not None:
            params["headers"] = {"content-type": "application/json"}
            params["body_b64"] = base64.b64encode(
                json.dumps(body, ensure_ascii=False).encode("utf-8")).decode()
        return self.call("%s/http.fetch" % ch, params, mode="call")


def _client_version() -> str:
    try:
        from importlib import metadata
        return "1.1.0"
    except Exception:  # noqa: BLE001
        return "1.1.0"


def is_available(path: str | None = None) -> tuple[bool, str]:
    """通道是否可用（不发起完整调用，只检查文件存在与管道可连）。"""
    try:
        endpoint, ticket = load_endpoint(path)
    except (OSError, KeyError, ValueError) as exc:
        return False, "读取 endpoint.json 失败：%s" % exc
    try:
        with WbipcClient(endpoint, ticket, timeout=8) as c:
            c.handshake()
        return True, "可用（epoch=%s）" % c.epoch
    except (WbipcError, OSError) as exc:
        return False, str(exc)


if __name__ == "__main__":
    ok, msg = is_available()
    print("通道可用：%s ｜ %s" % (ok, msg))
    if ok:
        endpoint, ticket = load_endpoint()
        t0 = time.time()
        with WbipcClient(endpoint, ticket) as c:
            c.handshake()
            ch = c.get_pipe()
            print("channel =", ch)
            res = c.http("POST", "/v2/billing/meter/checkin-activity-status", {})
            print("原始返回（%.2fs）：" % (time.time() - t0))
            print(json.dumps(res, ensure_ascii=False)[:600])
    sys.exit(0)
