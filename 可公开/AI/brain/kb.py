# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】AI 通过目录调用知识库，可申请修改目录内容（规格 §0）；【施工方】ai_client 封装
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""知识库接入：直接复用知识库（知识库i_client.py）的 ai_client（AI 的"眼和手"，已实测）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "知识库"))

from ai_client import KBClient, KBError  # noqa: E402,F401

from . import config  # noqa: E402


def get_client(api_key=None):
    """返回知识库客户端；密钥缺失时返回 None（第三轮审查 R3-1：
    此前 load_api_key 抛 SystemExit，except Exception 捕不到，
    "密钥缺失不致命、断连降级"的承诺失效——现在调用方判空即可）。"""
    try:
        return KBClient(api_key or config.load_api_key(), base_url=config.KB_BASE)
    except SystemExit:
        return None
