# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【逐字】包括人本身也化为变量；AI 有两种答案：推测用户的答案／纯粹基于冷静利益分析的答案（规格 §7，变更 #31）
# 【原话】对人的建模仅限助手服务目的（规格 §0 铁律）；【施工方】稳定层/状态层实现
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""用户模型 v1（规格书 §7：包括人本身也化为变量；仅限助手服务目的）。

稳定层（慢变量）：从统计层归纳——常用话题域、语言、提问风格；每条带出处。
状态层（快变量）：本轮信号估计——紧迫/深夜疲劳/情绪极性；低置信只留痕不落盘。

铁律（规格 §0）：对人的建模只服务于「怎么更好地答你」，不做人身推演。
"""
import re
from datetime import datetime

# 状态层信号词（快变量证据）
URGENT_WORDS = ("快", "马上", "立刻", "赶紧", "紧急", "急", "快点", "赶快")
POS_WORDS = ("谢谢", "感谢", "太好了", "不错", "厉害", "棒")
NEG_WORDS = ("垃圾", "废物", "难看", "离谱", "不堪", "什么鬼", "孱弱")

# 话题域 ← stats 里 type: 计数映射
_DOMAIN_MAP = {"推演": "推理问答", "提醒": "生活提醒", "闲聊": "闲聊",
               "记录": "记录", "查询": "查知识库"}


def update(state, text, act, task):
    """每轮结束调用：稳定层从累计统计归纳，状态层从本轮信号估计。"""
    um = state.usermodel
    counts = state.stats_counts

    # ── 稳定层（慢变量）：攒够 5 轮才开始下结论，之前只写「观察中」──
    n = state.counter
    if n >= 5:
        # 常用话题域：事件类型计数前三
        type_counts = sorted(((k[5:], v) for k, v in counts.items()
                              if k.startswith("type:")), key=lambda x: -x[1])
        if type_counts:
            tops = [_DOMAIN_MAP.get(t, t) for t, _ in type_counts[:3]]
            um["stable"]["常用话题"] = {"值": "、".join(tops),
                                        "出处": "最近{}轮事件类型统计".format(n)}
        # 提问风格：疑问句占比
        q = counts.get("act:question", 0.0)
        total = sum(v for k, v in counts.items() if k.startswith("act:")) or 1.0
        ratio = q / total
        style = "探索型（爱提问）" if ratio > 0.5 else ("指令型（多吩咐）" if ratio < 0.25 else "混合型")
        um["stable"]["交流风格"] = {"值": style,
                                    "出处": "疑问句占比 {:.0%}（{}轮）".format(ratio, n)}
    else:
        um["stable"].setdefault("观察中", {"值": "轮数还少（{}），不下结论".format(n),
                                           "出处": "冷启动"})

    # ── 状态层（快变量）：只留本轮，不带出处不进档案 ──
    sig = {}
    if any(w in text for w in URGENT_WORDS) or (len(text) <= 6 and act == "command"):
        sig["紧迫"] = "高（信号：短促/急词）"
    hour = datetime.now().hour
    if hour >= 23 or hour < 5:
        sig["时段"] = "深夜（{:02d}点）——你可能疲劳，我长话短说".format(hour)
    if any(w in text for w in POS_WORDS):
        sig["情绪"] = "偏正面"
    elif any(w in text for w in NEG_WORDS) or "！" in text:
        sig["情绪"] = "偏负面——我多解释少反问"
    um["state"] = sig
    if sig:
        task["user_state"] = dict(sig)   # 留痕进档案（快变量不落稳定层）
    return um


def fmt_for_settings(um):
    """设置页展示文本。"""
    lines = ["【稳定层（慢变量，从长期统计归纳）】"]
    for k, v in (um.get("stable") or {}).items():
        lines.append("  {}：{}（{}）".format(k, v["值"], v["出处"]))
    lines.append("【状态层（快变量，只反映最近一轮）】")
    sig = um.get("state") or {}
    lines += ["  {}：{}".format(k, v) for k, v in sig.items()] or ["  （暂无信号）"]
    return "\n".join(lines)
