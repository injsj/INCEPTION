# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】exe 打包脚本
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""把 INCEPTION 打包成单个 exe（可选步骤，不影响直接运行）。

用法：
    pip install pyinstaller        （第一次，联网）
    python build_exe.py
产物：
    AI\\inception\\dist\\INCEPTION.exe   双击即用（桌面壳 + 自动拉起服务）

说明：
    - exe 里只有壳；知识库\\、AI\\brain 这些本体仍在仓库目录里，
      所以 exe 不能单独拷走——要在别的机器用，得连仓库目录一起拷。
    - 打包参数：--noconsole 无黑窗、--icon 眼睛图标、--name INCEPTION。
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--noconsole", "--onefile",
    "--name", "INCEPTION",
    "--icon", str(HERE / "inception.ico"),
    "--distpath", str(HERE / "dist"),
    "--workpath", str(HERE / "build"),
    str(HERE / "app.py"),
]
print(" ".join(cmd))
sys.exit(subprocess.call(cmd, cwd=str(HERE)))
