# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】拆句→解析→放回字词→再解析，重复至完整（规格 §1）
# 【施工方】模板候选+打分雏形（打分函数待训练层接管）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""意图解析：多候选 + 打分（规格书第 1 节"多种解析按可能性排序"的雏形）。
v1 用模板规则产生候选与初始分；分数的来源将是训练统计（规格书第 6 节），
当前分值为施工方填的默认值（权重可见原则）。"""
import re

# 候选模板：正则 → (动作, 初始分, 说明)。槽位提取见 _slots()。
TEMPLATES = [
    (re.compile(r"^提醒我(?:在)?(?:(明天|后天))?"
                r"((?:早上|上午|中午|下午|晚上|今晚|凌晨)?"
                r"\d{1,2}点(?:\d{1,2}分|半)?)(.+)$"),
     "remind", 0.90, "提醒我+(日期)+时间+事情"),
    (re.compile(r"^提醒我(.+)$"),
     "remind", 0.70, "提醒我+事情（缺时间槽位，上层反问）"),
    (re.compile(r"^(?:帮我)?(?:查一下|查询|看看)([A-Za-z]+\d+)$"),
     "describe", 0.90, "查一下+标记"),
    (re.compile(r"^([A-Za-z]+\d+)(?:是|讲了)?什么(?:意思)?$"),
     "describe", 0.85, "标记+是什么/讲了什么"),
    (re.compile(r"^(.{1,12}?)(?:是|讲了)?什么(?:意思)?$"),
     "search", 0.60, "普通词+是什么（按标题检索目录）"),
    (re.compile(r"^什么是(.{1,12})$"),
     "search", 0.60, "什么是X（按标题检索目录）"),
]

MARGIN_ASK = 0.20   # 第一名领先不足此值 → 反问（规格书：低置信反问）


def _slots(action, m):
    if action == "remind":
        # 模板一：group(1)=日期前缀 group(2)=时间 group(3)=事情；模板二：group(1)=事情，时间缺槽
        if m.re is TEMPLATES[0][0]:
            time_str = (m.group(1) or "") + (m.group(2) or "")
            return {"time_str": time_str, "thing": m.group(3).strip()}
        return {"time_str": "", "thing": m.group(1).strip()}
    if action == "search":
        return {"topic": m.group(1)}
    return {"target": m.group(1)}


def parse(sentence, tokens):
    """返回候选列表 [{action, slots, score, note}]，分数降序。"""
    cands = []
    for pat, action, score, note in TEMPLATES:
        m = pat.match(sentence.strip())
        if m:
            cands.append({"action": action, "slots": _slots(action, m),
                          "score": score, "note": note})
    # 关键词兜底：命中"查/看"类动词 → 目录检索候选
    words = [w for k, w in tokens if k == "词"]
    helpers = ("查一下", "查询", "看看", "帮我", "我", "你", "的",
               "快", "请", "麻烦", "赶紧", "赶快", "马上", "立刻", "先")   # 副词/敬语不是主题
    if not cands and any(w in helpers and w in ("查一下", "查询", "看看", "帮我") for w in words):
        # 主题 = 去掉辅助词后的全部成分（含生词——生词正是内容词）
        topic = "".join(w for k, w in tokens if w not in helpers)
        cands.append({"action": "search", "slots": {"topic": topic},
                      "score": 0.50, "note": "关键词目录检索"})
    if not cands:
        cands.append({"action": "unknown", "slots": {}, "score": 0.0, "note": "无匹配模板"})
    cands.sort(key=lambda c: -c["score"])
    return cands


def decide(cands):
    """选择解析：第一名领先足够则采用，否则进入反问（返回 ask）。"""
    top = cands[0]
    if top["action"] == "unknown":
        return top, "unknown", 0.0
    second = cands[1]["score"] if len(cands) > 1 else 0.0
    if top["score"] - second >= MARGIN_ASK:
        return top, "adopted", top["score"]
    return top, "ask", top["score"]   # 低置信：应向用户反问（v1 玩具层记录即可）
