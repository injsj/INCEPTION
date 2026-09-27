# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】类型权重决定先看谁／打分只针对事件本身／头疼vs煤气／轮得到看（规格 §3）
# 【逐字】两个正交权重体系澄清（变更 #31）；【施工方】语境加权与urgent接线（变更 #30，用户质问驱动）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""注意力调度（规格书第 3 节）：
- 类型权重 = 慢变量：决定先看谁（种子值由施工方填，明文可见；训练可调，记 weights.log）
- 事件紧急分 = 快变量：每轮独立打分 = 严重度 + 截止紧迫度 + 阻塞度（变更 #26 补建），与类型权重无关
- 三保险：急诊预筛（插队）/ 等待老化（每轮 +0.1 封顶 +0.5）/ 紧急打断（越线立即处理）
- 每轮预算 K=3：时间串行注意力，任一时刻深想一件事"""
from datetime import datetime

SEED_WEIGHTS = {"安全告警": 2.0, "提醒": 1.2, "知识查询": 1.0, "推演": 1.0, "闲聊": 0.6}

EMERGENCY_WORDS = ["泄漏", "煤气", "着火", "急救", "救命", "中毒"]

AGING_STEP = 0.1
AGING_CAP = 0.5
BUDGET_K = 3
EMERGENCY_SCORE = 2.0   # 打断阈值：事件分越线立即插队

SEVERITY = {"安全告警": 1.0, "提醒": 0.5, "知识查询": 0.4, "推演": 0.4, "闲聊": 0.1}
DEFAULT_SEVERITY = 0.3


def prescreen(events):
    """急诊预筛：全部事件过一遍（浅层匹配），命中关键词 → 标记紧急。"""
    hits = []
    for e in events:
        if e["state"] in ("完成", "已取消"):
            continue
        text = e.get("source", "") + e.get("object", "") + e.get("note", "")
        if any(w in text for w in EMERGENCY_WORDS):
            e["emergency"] = True
            hits.append(e)
    return hits


def age(aging):
    """等待老化：每轮未被处理的事件 +0.1，封顶 +0.5（保证最迟被看到）。"""
    for k in list(aging):
        aging[k] = min(AGING_CAP, aging[k] + AGING_STEP)


def scan_order(open_events, weights, aging, context_types=None):
    """有效扫描序 = 类型权重 + 老化增量 + 语境加权（降序）。类型权重高 → 先被看到。
    语境加权（变更 #30 条文③）：最近几轮对话的行为类型与事件同类的 +0.5——
    聊推演的日子里，推演欠账排前头。量级定 0.5：能翻同档（闲聊0.6↔推演1.0）
    的盘，但翻不过提醒(1.2)与安全(2.0)——时间承诺和安全不被语境盖过。"""
    ctx = context_types or set()

    def key(e):
        return (weights.get(e["type"], DEFAULT_SEVERITY) + aging.get(e["id"], 0.0)
                + (0.5 if e["type"] in ctx else 0.0))
    return sorted(open_events, key=key, reverse=True)


def score_event(e, now=None, blocks_main=False, urgent=False):
    """事件紧急分（打分只针对事件本身，和类型权重无关——规格书第 3 节）。
    三成分：严重度 + 截止紧迫度 + 阻塞度（变更 #26 补账——阻塞度此前漏建）。
    阻塞度：卡着对话主线（追问挂起等你答）+0.5；显式声明阻塞其他事件每件 +0.3。
    变更 #30 条文③：用户状态判「紧迫」时阻塞度加分加倍——你急的时候，
    卡主线的事更靠前。紧急事件直接顶到打断阈值。"""
    if e.get("emergency"):
        return EMERGENCY_SCORE
    s = SEVERITY.get(e["type"], DEFAULT_SEVERITY)
    t = e.get("time")
    if t:
        try:
            dt = datetime.fromisoformat(t)
            mins = (dt - (now or datetime.now())).total_seconds() / 60.0
            if mins < 10:
                s += 0.8
            elif mins < 60:
                s += 0.5
            else:
                s += 0.2
        except ValueError:
            pass
    boost = 2.0 if urgent else 1.0
    if blocks_main:                       # 追问挂起：你不答，别的都别想好
        s += 0.5 * boost
    s += 0.3 * boost * len(e.get("blocks", []))   # 显式依赖：它卡着多少别的事件
    return round(s, 3)


def pick_batch(all_events, weights, aging, now=None, pending_event_id=None,
               context_types=None, urgent=False):
    """一轮预算：未完成的 events 按扫描序取前 K 个，再按事件分降序排执行。
    pending_event_id（变更 #26）：追问挂起中的事件卡着对话主线，阻塞度计入事件分。
    context_types/urgent（变更 #30 条文③）：语境类型加权与用户紧迫态传入。"""
    open_ = [e for e in all_events if e["state"] in ("待办", "进行中")]
    batch = scan_order(open_, weights, aging, context_types=context_types)[:BUDGET_K]
    batch.sort(key=lambda e: -score_event(
        e, now, blocks_main=(e["id"] == pending_event_id), urgent=urgent))
    return batch
