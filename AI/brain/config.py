# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】路径与常数配置
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""路径与基础配置。运行期状态全落盘（规格书第 7 节：崩溃可恢复）。"""
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent        # D:\cangku\AI
STATE = ROOT / "state"
ARCHIVE = ROOT / "archive"
ZHISHIKU = ROOT.parent / "知识库"                   # 知识库（ai_client 从这里引入）
KB_BASE = "http://127.0.0.1:8320"
# 机密目录（2026-09-27 变更 #33）：密钥移出仓库树，与知识库侧同一机密目录
SECRET_DIR = Path(os.environ.get("CANGKU_SECRET_DIR", r"D:\机密"))
KEY_FILE = SECRET_DIR / "ai_key.txt"


def ensure():
    STATE.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)


def atomic_write(path, text):
    """原子写文本（临时文件 + os.replace + 失败重试）。

    第二轮审查 M2：此前 session.json / 建议队列 / 对账计数等都是直接覆写，
    写盘中途崩溃 → 文件截断 → 冷启动丢全部提醒与状态。与知识库 safeio 同款。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    err = None
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:  # Windows 杀毒软件/句柄占用
            err = e
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)
    raise err


def load_api_key():
    """密钥来源：环境变量 KB_API_KEY 优先，其次 state\\ai_key.txt。"""
    k = os.environ.get("KB_API_KEY", "").strip()
    if k:
        return k
    if KEY_FILE.exists():
        k = KEY_FILE.read_text(encoding="utf-8").strip()
        if k:
            return k
    raise SystemExit(
        "缺少 AI 密钥：请设置环境变量 KB_API_KEY，或把密钥写入 {}".format(KEY_FILE))
