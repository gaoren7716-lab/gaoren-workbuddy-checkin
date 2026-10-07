#!/usr/bin/env python3
"""从完整版源码构建「市场分发版（零凭据精简版）」。

做的是**真实裁剪**，不是代码伪装：把凭据相关能力整段拿掉，
让分发版在静态层面就不具备那些能力（而不是「有但默认不用」）。

裁剪项（每项都可核验）：
  1. 不打包 wb_credentials.py（读登录态 + 直连传输）
  2. 不打包 wb_crypto.py（ctypes 进程内存取密钥 + AES-GCM 解密）
  3. 不打包 test_vectors.py / gen_vectors.js（加密自测向量，市场版无需）
  4. 删除 doctor 中「登录态信封」检查（市场版没有凭据模块，永远执行不到）
  5. 删除文件日志能力（write_log 改为空实现，市场版不写任何文件）
  6. 修正「无可用通道」的提示（市场版只有客户端 RPC 一条路）

用法：python build_market.py <完整版源码目录> <输出目录>
"""
from __future__ import annotations

import pathlib
import shutil
import sys

KEEP = {
    "SKILL.md": "SKILL.market.md",
    "README.md": "README.market.md",
    "LICENSE": "LICENSE",
    "_icon.png": "_icon.png",
    "references/compat.md": "references/compat.md",
    "scripts/wb_auto_checkin.py": "wb_auto_checkin.py",   # 源码为平铺结构
    "scripts/wbipc_client.py": "wbipc_client.py",
}


def strip_checkin(src: str) -> str:
    # 4) 删除 doctor 里的登录态信封检查
    a = src.index('    _cred = _load_credentials_module()')
    b = src.index('    t, label = open_transport()', a)
    src = src[:a] + src[b:]

    # 5) 日志改为空实现（保留函数签名，避免调用点报错）
    a = src.index('def log_path()')
    b = src.index('# ---------------------------------------------------------------------------\n# 传输层')
    src = src[:a] + '''def log_path() -> str | None:
    """市场分发版**不写任何文件**，没有日志路径。"""
    return None


def write_log(line: str) -> None:
    """市场分发版不落盘：结果只输出到 stdout。"""
    return


''' + src[b:]

    # 6) 修正无可用通道的提示
    a = src.index('        print("❌ 四条通道都不可用。')
    b = src.index('        write_log("%s | status=error | msg=no_channel"', a)
    src = src[:a] + '''        print("❌ 客户端本机 RPC 通道不可用。\\n"
              "   本分发版只通过 WorkBuddy 客户端自身的本机通道发请求，"
              "不接触任何凭据，\\n"
              "   因此需要 WorkBuddy 桌面端处于**运行且已登录**状态。"
              "先跑 --doctor 看诊断。")\n''' + src[b:]

    # 通道参数收窄：市场版没有 A/C/B
    src = src.replace('choices=["auto", "D", "A", "C", "B"]', 'choices=["auto", "D"]')
    return src


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src_root, out_root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    if out_root.exists():
        shutil.rmtree(out_root)
    for dst_rel, src_rel in sorted(KEEP.items()):
        src = src_root / src_rel
        if not src.is_file():
            print("缺少文件：%s" % src_rel)
            return 1
        dst = out_root / ("LICENSE.md" if dst_rel == "LICENSE" else dst_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src_rel == "wb_auto_checkin.py":
            dst.write_text(strip_checkin(src.read_text(encoding="utf-8")),
                           encoding="utf-8")
        else:
            shutil.copy2(src, dst)
        print("  %s  <-  %s" % (dst_rel, src_rel))
    print("\n已裁剪掉：wb_credentials.py、wb_crypto.py、test_vectors.py、"
          "gen_vectors.js，以及 doctor 登录态检查、文件日志能力")
    return 0


if __name__ == "__main__":
    sys.exit(main())
