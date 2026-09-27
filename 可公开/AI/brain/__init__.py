# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】包结构标记（规格书 §0 目录：六层分包）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""AI 本体包（brain）。施工依据：AI规格书.md。"""
import sys
from pathlib import Path

# 挂载知识库客户端路径（ai_client），任何子模块导入前完成
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "知识库"))
