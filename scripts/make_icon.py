#!/usr/bin/env python3
"""生成技能图标 _icon.png（512x512，纯标准库，不依赖 PIL）。

用法：python make_icon.py [输出路径]
图形：圆角方块底 + 对勾 + 金币圆点，扁平风格。
"""
from __future__ import annotations

import struct
import sys
import zlib

SIZE = 512
BG_TOP = (255, 138, 76)       # 暖橙
BG_BOTTOM = (247, 92, 60)     # 橙红（竖向渐变）
CHECK = (255, 255, 255)
COIN = (255, 208, 84)
COIN_EDGE = (233, 168, 43)


def rounded(x: float, y: float, w: float, h: float, r: float) -> bool:
    """点是否落在圆角矩形内。"""
    if not (x <= w and y <= h):
        return False
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def in_check(x: float, y: float) -> bool:
    """对勾：两条线段，带宽度。"""
    def seg(px, py, x1, y1, x2, y2, half):
        vx, vy = x2 - x1, y2 - y1
        wx, wy = px - x1, py - y1
        t = (wx * vx + wy * vy) / (vx * vx + vy * vy)
        t = max(0.0, min(1.0, t))
        dx, dy = wx - t * vx, wy - t * vy
        return dx * dx + dy * dy <= half * half
    return (seg(x, y, 146, 276, 220, 350, 30)
            or seg(x, y, 220, 350, 330, 232, 30))


def in_ring(x: float, y: float, cx: float, cy: float, r: float, w: float) -> bool:
    d2 = (x - cx) ** 2 + (y - cy) ** 2
    return (r - w) ** 2 <= d2 <= r * r


def build() -> bytes:
    coin_cx, coin_cy = 386, 130
    rows = []
    for y in range(SIZE):
        row = bytearray()
        ty = y / (SIZE - 1)
        base = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * ty) for i in range(3))
        for x in range(SIZE):
            if not rounded(x, y, SIZE - 1, SIZE - 1, 112):
                row += bytes((0, 0, 0, 0))
                continue
            if in_ring(x, y, coin_cx, coin_cy, 72, 20):
                row += bytes(COIN_EDGE + (255,))
            elif in_ring(x, y, coin_cx, coin_cy, 54, 54):
                row += bytes(COIN + (255,))
            elif in_check(x, y):
                row += bytes(CHECK + (255,))
            else:
                row += bytes(base + (255,))
        rows.append(bytes(row))
    return b"".join(b"\x00" + r for r in rows)


def chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "_icon.png"
    raw = build()
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(out, "wb") as f:
        f.write(png)
    print("已生成 %s（%d 字节，%dx%d）" % (out, len(png), SIZE, SIZE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
