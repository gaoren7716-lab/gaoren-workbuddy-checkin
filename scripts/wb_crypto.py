"""gaoren-workbuddy-checkin · 加密与密钥模块（自研实现，纯标准库）

包含两部分：
  1. AES-256-GCM 的纯 Python 实现（用于解开客户端 5.6.2+ 的 AtRestEncryption 信封）
  2. 运行时密钥获取（环境变量 / 磁盘密钥块 / Windows 进程内存）

设计原则：
  · 零第三方依赖，只用 Python 标准库
  · 密钥只在内存中参与本地校验，绝不写盘、绝不打印
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os

import struct
import sys
import time
from ctypes import wintypes

# ---------------------------------------------------------------------------
# 一、AES-256-GCM（纯 Python）
# ---------------------------------------------------------------------------

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8)


def _xtime(b: int) -> int:
    b <<= 1
    if b & 0x100:
        b = (b ^ 0x11B) & 0xFF
    return b


def _expand_key(key: bytes) -> tuple[list[list[int]], int]:
    """AES-256 密钥扩展，返回 (轮密钥字数组, 轮数)。"""
    nk = len(key) // 4
    if nk not in (4, 6, 8):
        raise ValueError("AES 密钥长度必须是 16/24/32 字节")
    nr = nk + 6
    words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(words[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]                      # RotWord
            t = [_SBOX[b] for b in t]              # SubBytes
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        words.append([words[i - nk][j] ^ t[j] for j in range(4)])
    return words, nr


def _encrypt_block(words: list[list[int]], nr: int, block: bytes) -> bytes:
    state = [block[i] ^ words[i // 4][i % 4] for i in range(16)]
    for rnd in range(1, nr):
        state = [_SBOX[b] for b in state]
        # ShiftRows（状态按列排列，state[4*c + r]）
        state = [
            state[0], state[5], state[10], state[15],
            state[4], state[9], state[14], state[3],
            state[8], state[13], state[2], state[7],
            state[12], state[1], state[6], state[11],
        ]
        ns = []
        for c in range(4):
            a = state[4 * c:4 * c + 4]
            x = a[0] ^ a[1] ^ a[2] ^ a[3]
            ns += [
                a[0] ^ x ^ _xtime(a[0] ^ a[1]),
                a[1] ^ x ^ _xtime(a[1] ^ a[2]),
                a[2] ^ x ^ _xtime(a[2] ^ a[3]),
                a[3] ^ x ^ _xtime(a[3] ^ a[0]),
            ]
        state = [ns[i] ^ words[rnd * 4 + i // 4][i % 4] for i in range(16)]
    state = [_SBOX[b] for b in state]
    state = [
        state[0], state[5], state[10], state[15],
        state[4], state[9], state[14], state[3],
        state[8], state[13], state[2], state[7],
        state[12], state[1], state[6], state[11],
    ]
    return bytes(state[i] ^ words[nr * 4 + i // 4][i % 4] for i in range(16))


def _ghash_mul(x: int, y: int) -> int:
    """GF(2^128) 乘法，约减多项式 x^128 + x^7 + x^2 + x + 1。

    位序要点：X 从**最高位**开始逐位处理（GCM 规范约定的位序）。
    V 每轮右移一位，最低位为 1 时异或 R（R = 0xE1 << 120）。
    位序写反不会报异常，只会算出另一个值，最后表现为 authTag 校验失败。
    """
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        v = (v >> 1) ^ (0xE1 << 120) if v & 1 else v >> 1
    return z


def _ghash(h: int, data: bytes) -> int:
    y = 0
    for off in range(0, len(data), 16):
        block = data[off:off + 16].ljust(16, b"\x00")
        y = _ghash_mul(y ^ int.from_bytes(block, "big"), h)
    return y


def _pad16(data: bytes) -> bytes:
    """补齐到 16 字节边界。GCM 要求 AAD 与 C 各自补齐后再拼接，
    不能把 AAD||C 一起拼完再补——AAD 长度非 16 倍数时后续所有块都会算错。"""
    rem = len(data) % 16
    return data if rem == 0 else data + b"\x00" * (16 - rem)


def gcm_tag_s(h: int, aad: bytes, ciphertext: bytes) -> int:
    """GHASH 部分：H(A‖pad‖C‖pad‖[len(A)]64‖[len(C)]64)。"""
    tail = struct.pack(">Q", len(aad) * 8) + struct.pack(">Q", len(ciphertext) * 8)
    return _ghash(h, _pad16(aad) + _pad16(ciphertext) + tail)


def aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes,
                    aad: bytes, tag: bytes) -> bytes:
    """AES-GCM 解密，严格校验 authTag。"""
    if len(nonce) != 12:
        raise ValueError("本实现仅支持 12 字节 nonce")
    words, nr = _expand_key(key)
    h = int.from_bytes(_encrypt_block(words, nr, b"\x00" * 16), "big")

    # GCM 计数器：J0 只用于 tag，数据块从 inc32(J0)=2 开始
    j0 = nonce + b"\x00\x00\x00\x01"
    plain = bytearray()
    for off in range(0, len(ciphertext), 16):
        counter = nonce + struct.pack(">I", off // 16 + 2)
        ks = _encrypt_block(words, nr, counter)
        chunk = ciphertext[off:off + 16]
        plain += bytes(a ^ b for a, b in zip(chunk, ks))

    s = gcm_tag_s(h, aad, ciphertext)
    expect = s ^ int.from_bytes(_encrypt_block(words, nr, j0), "big")
    if not hmac.compare_digest(expect.to_bytes(16, "big"), tag):
        raise ValueError("authTag 校验失败：密钥错误或数据被篡改")
    return bytes(plain)


# ---------------------------------------------------------------------------
# 二、信封（AtRestEncryption suite=1）解析
# ---------------------------------------------------------------------------

_AAD_DOMAIN = b"WB-AAD\x00"
_STD_FORMAT = b"WBEV1"
_SUITE = b"sym-v1"
_FRAMING_FIELD = 2


def _lp(s: bytes) -> bytes:
    """长度前缀编码：4 字节大端长度 + 内容。"""
    return struct.pack(">I", len(s)) + s


def build_field_aad(key_id_hex: str) -> bytes:
    """构造 field 框架的 AAD（与客户端 5.6.2+ 一致）。"""
    kid = key_id_hex.encode()
    return (b"".join((
        _AAD_DOMAIN,
        bytes([1]),            # 版本
        _lp(_STD_FORMAT),      # 标准格式 id
        _lp(_SUITE),           # 套件
        struct.pack(">I", 1),  # keyId 计数
        _lp(kid),              # keyId
        bytes([_FRAMING_FIELD]),
        b"\x00",               # sequence 占位
        b"\x00",               # final 占位
    )))


def derive_key(secret: str) -> tuple[bytes, str]:
    """44 字符密钥串 → (AES 密钥, keyId)。"""
    key = hashlib.sha256(secret.encode("utf-8")).digest()[:32]
    return key, hashlib.sha256(key).hexdigest()[:16]


def open_envelope(envelope_b64: str, secret: str) -> str:
    """解开一条加密字段，返回明文（通常是 JWT 字符串）。"""
    env = json.loads(base64.b64decode(envelope_b64))
    if env.get("suite") != 1:
        raise ValueError("暂不支持的加密套件：%r" % env.get("suite"))
    key, key_id = derive_key(secret)
    if env.get("keyId") != key_id:
        raise ValueError("keyId 不匹配，密钥不是这一把")
    plain = aes_gcm_decrypt(
        key,
        base64.b64decode(env["nonce"]),
        base64.b64decode(env["ciphertext"]),
        build_field_aad(env["keyId"]),
        base64.b64decode(env["authTag"]),
    )
    return plain.decode("utf-8")


# ---------------------------------------------------------------------------
# 三、密钥获取
# ---------------------------------------------------------------------------

DEFAULT_PROCS = ("WorkBuddy.exe", "WorkBuddyAI.exe", "CodeBuddy.exe")
_B64_ALPHABET = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def _candidate_at(data: bytes, pos: int) -> str | None:
    """data[pos] 是 '=' 时，往前取 43 个 base64 字符，校验后返回字符串。"""
    start = pos - 43
    if start < 0:
        return None
    window = data[start:pos + 1]
    if len(window) != 44 or not _B64_ALPHABET.issuperset(window):
        return None
    return window.decode()


def key_from_env() -> str | None:
    v = os.environ.get("GAOREN_ATREST_KEY") or os.environ.get("WORKBUDDY_ATREST_KEY")
    if v and len(v) == 44:
        return v.strip()
    return None


def key_from_disk(data_dir: str) -> str | None:
    """个别版本会把密钥块落盘；在 Data 目录里找 44 字符 base64。"""
    if not data_dir or not os.path.isdir(data_dir):
        return None
    for root, _dirs, files in os.walk(data_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
                blob = open(path, "rb").read()
            except OSError:
                continue
            idx = blob.find(b"=")
            while idx != -1:
                cand = _candidate_at(blob, idx)
                if cand:
                    return cand
                idx = blob.find(b"=", idx + 1)
    return None


if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # 关键：必须显式声明 HANDLE 相关的 argtypes/restype，
    # 否则 64 位句柄会被默认按 c_int 截断，导致后续调用全部失败。
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.restype = wintypes.BOOL
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.Process32NextW.restype = wintypes.BOOL
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.VirtualQueryEx.restype = ctypes.c_size_t
    _k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_size_t]
    _k32.ReadProcessMemory.restype = wintypes.BOOL
    _k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.c_void_p]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p),
                    ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD),
                    ("PartitionId", wintypes.WORD),
                    ("RegionSize", ctypes.c_size_t),
                    ("State", wintypes.DWORD),
                    ("Protect", wintypes.DWORD),
                    ("Type", wintypes.DWORD)]

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    _k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    _k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                             wintypes.DWORD]

    def _working_set(pid: int) -> int:
        h = _k32.OpenProcess(0x0400 | 0x0010, False, pid)
        if not h:
            return 0
        c = _PROCESS_MEMORY_COUNTERS()
        c.cb = ctypes.sizeof(c)
        ok = _k32.K32GetProcessMemoryInfo(h, ctypes.byref(c), ctypes.sizeof(c))
        _k32.CloseHandle(h)
        return int(c.WorkingSetSize) if ok else 0

    def _list_client_procs() -> list[tuple[int, str, int]]:
        """返回 [(pid, 进程名, 工作集字节)]，按内存占用从大到小排序。

        密钥通常在渲染进程（大内存那个）里，先扫大的命中率更高。
        """
        TH32CS_SNAPPROCESS = 0x00000002
        snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap in (0xFFFFFFFFFFFFFFFF, -1):
            return []
        out, entry = [], _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        names = {n.lower() for n in os.environ.get(
            "GAOREN_CLIENT_PROCS", ",".join(DEFAULT_PROCS)).split(",") if n}
        try:
            if _k32.Process32FirstW(snap, ctypes.byref(entry)):
                while True:
                    name = entry.szExeFile
                    if name.lower() in names:
                        pid = int(entry.th32ProcessID)
                        out.append((pid, name, _working_set(pid)))
                    if not _k32.Process32NextW(snap, ctypes.byref(entry)):
                        break
        finally:
            _k32.CloseHandle(snap)
        out.sort(key=lambda t: t[2], reverse=True)
        return out

    def _scan_one(pid: int, key_id: str, budget: int, deadline: float,
                  stop=None) -> str | None:
        """在一个进程的可读内存里找 44 字符 base64 密钥，用 keyId 校验。"""
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        MEM_COMMIT = 0x1000
        READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}

        h = _k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            h = _k32.OpenProcess(0x1000 | PROCESS_VM_READ, False, pid)
        if not h:
            return None
        addr, scanned = 0, 0
        mbi = _MEMORY_BASIC_INFORMATION()
        CHUNK = 4 << 20
        OVERLAP = 64
        try:
            while addr < 0x7FFFFFFF0000:
                if time.time() > deadline or scanned >= budget or (stop and stop.is_set()):
                    return None
                got_size = _k32.VirtualQueryEx(h, ctypes.c_void_p(addr),
                                               ctypes.byref(mbi), ctypes.sizeof(mbi))
                if not got_size:
                    break
                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)
                protect = int(mbi.Protect)
                readable = (int(mbi.State) == MEM_COMMIT
                            and not (protect & 0x100)
                            and (protect & 0xFF) in READABLE)
                nxt = base + size if size else 0
                if readable and size and base:
                    off = 0
                    tail = b""
                    while off < size and scanned < budget and time.time() < deadline:
                        if stop and stop.is_set():
                            return None
                        want = min(CHUNK, size - off)
                        buf = ctypes.create_string_buffer(want)
                        got = wintypes.DWORD(0)
                        if _k32.ReadProcessMemory(h, ctypes.c_void_p(int(mbi.BaseAddress) + off),
                                                  buf, want, ctypes.byref(got)):
                            scanned += got.value
                            data = tail + buf.raw[:got.value]
                            idx = data.find(b"=")
                            while idx != -1:
                                cand = _candidate_at(data, idx)
                                if cand and derive_key(cand)[1] == key_id:
                                    return cand
                                idx = data.find(b"=", idx + 1)
                            tail = data[-OVERLAP:]
                        else:
                            break
                        off += want
                addr = nxt
        finally:
            _k32.CloseHandle(h)
        return None

    def key_from_memory(key_id: str, budget_mb: int = 768,
                        timeout_s: int = 75, verbose: bool = False) -> str | None:
        """从运行中的客户端进程内存里取密钥（仅 Windows，需同一用户运行）。

        多进程并行扫描：ReadProcessMemory 会释放 GIL，串行扫要半分钟，
        并行后基本等于最慢那个进程的时间。
        """
        import threading
        from concurrent.futures import ThreadPoolExecutor

        procs = _list_client_procs()
        if not procs:
            return None
        stop = threading.Event()
        deadline = time.time() + timeout_s
        budget = budget_mb * 1024 * 1024

        def work(item):
            pid, name, _ws = item
            if verbose:
                print("[gaoren-checkin] 扫描 %s pid=%d" % (name, pid), file=sys.stderr)
            return _scan_one(pid, key_id, budget, deadline, stop)

        with ThreadPoolExecutor(max_workers=min(4, len(procs))) as pool:
            for res in pool.map(work, procs):
                if res:
                    stop.set()
                    return res
        return None
else:  # 非 Windows 走网页授权通道，本模块不提供内存提取
    def key_from_memory(*_a, **_k):
        return None


def obtain_key(key_id: str, data_dir: str = "", verbose: bool = False) -> tuple[str | None, str]:
    """按 env → 磁盘 → 进程内存 的顺序找密钥。返回 (密钥, 来源描述)。"""
    v = key_from_env()
    if v and derive_key(v)[1] == key_id:
        return v, "环境变量"
    v = key_from_disk(data_dir)
    if v and derive_key(v)[1] == key_id:
        return v, "磁盘密钥块"
    v = key_from_memory(key_id, verbose=verbose)
    if v:
        return v, "客户端进程内存"
    return None, "未找到"


# ---------------------------------------------------------------------------
# 四、自测（NIST GCM 测试向量 + 往返）
# ---------------------------------------------------------------------------

def self_test() -> int:
    fails = []

    def check(name: str, cond: bool) -> None:
        print("  %s %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            fails.append(name)

    # 权威向量由 Node crypto 生成（见 test_vectors.py / gen_vectors.js）
    try:
        from test_vectors import VECTORS
    except ImportError:  # 独立分发时降级为内置的两条
        VECTORS = [
            ("aes256-empty", "00" * 32, "00" * 12, "", "", "",
             "530f8afbc74536b9a963b4f1c4cb738b"),
            ("aes128-oneblock", "00" * 16, "00" * 12, "", "00" * 16,
             "0388dace60b6a392f328c2b971b2fe78",
             "ab6e47d42cec13bdf53a67b21257bddf"),
        ]

    for name, k_hex, n_hex, aad_hex, pt_hex, ct_hex, tag_hex in VECTORS:
        key, nonce = bytes.fromhex(k_hex), bytes.fromhex(n_hex)
        aad, pt = bytes.fromhex(aad_hex), bytes.fromhex(pt_hex)
        try:
            got = aes_gcm_decrypt(key, nonce, bytes.fromhex(ct_hex), aad,
                                  bytes.fromhex(tag_hex))
            check("AES-GCM 解密 %s" % name, got == pt)
        except ValueError as exc:
            check("AES-GCM 解密 %s（%s）" % (name, exc), False)

    # 错误密钥必须被 authTag 拒绝
    try:
        aes_gcm_decrypt(bytes(32), bytes(12), b"x" * 16, b"aad", bytes(16))
        check("错误密钥被拒绝", False)
    except ValueError:
        check("错误密钥被拒绝", True)

    # 密钥派生与 AAD 结构
    key, key_id = derive_key("A" * 43 + "=")
    check("keyId 为 16 位 hex", len(key_id) == 16)
    aad = build_field_aad(key_id)
    check("AAD 54 字节且以域常量开头",
          len(aad) == 54 and aad.startswith(_AAD_DOMAIN))

    print("\n自测结果：%s" % ("全部通过" if not fails else "失败 %d 项：%s"
                          % (len(fails), ", ".join(fails))))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(self_test())
