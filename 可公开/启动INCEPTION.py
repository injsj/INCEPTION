# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】桌面壳启动入口
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
# ═══════════════════════════════════════════════════════
#   INCEPTION 一键启动（桌面窗口版）
#   PyCharm 里右键本文件 → 运行（Run）即可；双击也可以。
#   它会自动：拉起知识库服务（没在跑的话）→ 打开 INCEPTION 窗口
#   第一次运行缺 pywebview 时会自动联网安装，以后不再出现。
# ═══════════════════════════════════════════════════════
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # D:\cangku
APP = ROOT / "AI" / "inception" / "app.py"


def ensure_shell_dep():
    """缺 pywebview 时自动安装（只发生在第一次运行）。"""
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        print("首次运行，正在自动安装桌面壳依赖（pywebview）……")
        print("（需要联网，只此一次）")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "pywebview"])
        if r.returncode != 0:
            print("安装失败：请检查网络，或手动执行  pip install pywebview")
            return False
        return True


if __name__ == "__main__":
    if ensure_shell_dep():
        # 用当前解释器起桌面壳；壳会自己处理服务拉起与窗口
        sys.exit(subprocess.call([sys.executable, str(APP)]))
    sys.exit(1)
