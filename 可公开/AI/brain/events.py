# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】列出从用户指令中拆解出的要处理的事件（规格 §2）
# 【隐含】人会改主意——事件生命周期与新事件顶替旧事件
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""事件结构（规格书第 2 节）：从指令中拆出的事件 + 生命周期管理。
生命周期：待办 → 进行中 → 完成 / 已取消（新事件可顶替旧事件，对话记录两者都留）。"""
from datetime import datetime

STATES = ["待办", "进行中", "完成", "已取消"]


def new_event(eid, etype, subject, action, obj="", time=None, source="", note=""):
    """time 为归一化后的 ISO 字符串（见 timenorm）或 None。"""
    return {
        "id": eid,
        "type": etype,            # 事件类型（对应类型注意力权重表）
        "subject": subject,       # 主体
        "action": action,         # 动作
        "object": obj,            # 对象
        "time": time,             # ISO 字符串或 None
        "state": "待办",
        "source": source,         # 来自用户哪句话
        "note": note,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def due_events(events, now=None):
    """到期待办事件（提醒的触发口）。"""
    now = now or datetime.now()
    out = []
    for e in events:
        if e["state"] != "待办" or not e.get("time"):
            continue
        try:
            t = datetime.fromisoformat(e["time"])
        except ValueError:
            continue
        if t <= now:
            out.append(e)
    return out
