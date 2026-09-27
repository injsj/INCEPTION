# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】拆句认成分，不是拿整句撞名单（变更 #22 拍板）；从句嵌套 v1（变更 #28）
# 【施工方】分句/疑问焦点/否定/纠正识别的具体实现
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""组合式理解 v2（2026-09-25 变更 #22：识别全部词典驱动，代码里无词表）。

把一句话拆成结构，而不是匹配整句模式：
    分句 → 每句识别：疑问焦点 / 否定 / 条件标记 / 祈使头
    → 逻辑式 {clauses: [{raw, intent, negated, has_condition}], any_need, any_negated}

v2 与 v1 的区别：v1 的疑问焦点/否定/条件/祈使词表写死在代码里（换汤不换药的名单）；
v2 全部改查词典（lexicon.grammar 的焦点/否定/条件/祈使/条件探针属性）——
词典里添一个新疑问词，理解力即时生效，代码零改动。
意图组合规则保留结构逻辑：方式焦点+能/才/让 → 问条件；条件探针+事实疑问 → 问条件。

从句嵌套 v1（2026-09-25 变更 #28）：靠词典连词的「关系」属性（条件/因果/让步 × 因/果/让/转）
识别一层嵌套，两种形态：
    ① 句内双标：「因为A所以B」→ rel=因果, cond_part=A, main_part=B
    ② 跨分句配对：「如果A，B」/「虽然A，但是B」→ 前句 absorbed，后句挂 rel/cond_part/main_part
嵌套分句的 intent/negated 按主句部重算（问题在主句里），cond_part 原文保留给事实挖掘。
v1 边界：深度≤1（cond_part 里再出现关系标记不递归）；果在前语序（「A，因为B」）不拆；
拆不开的分句 intent=none 交给下游兜底。
"""
import re

from . import lexicon

_GV = lexicon.grammar_view()
CLAUSE_SPLIT = re.compile(r"[，。；！？,!?;．]+")

# 词典视图排序一次：长词先匹配（「为什么」先于「什么」）
_QUESTION_WORDS = sorted(_GV["question_words"], key=len, reverse=True)
_NEG_WORDS = sorted(_GV["negations"], key=len, reverse=True)
_COND_MARKS = tuple(_GV["cond_marks"])
_IMPERATIVES = sorted(_GV["imperatives"], key=len, reverse=True)
_COND_PROBES = tuple(_GV["cond_probes"])

# 关系连词视图（变更 #28）：词典「关系」属性 → 头标记（因/让侧）与尾标记（果/转侧）
_REL_MARKS = {w: e["关系"] for w, e in _GV["entries"].items() if e.get("关系")}
_REL_HEADS = sorted((w for w, r in _REL_MARKS.items()
                     if r.endswith("-因") or r.endswith("-让")), key=len, reverse=True)
_REL_TAILS = sorted((w for w, r in _REL_MARKS.items()
                     if r.endswith("-果") or r.endswith("-转")), key=len, reverse=True)


def _rel_family(mark):
    """「条件-因」→「条件」；「因果-果」→「因果」；「让步-让」→「让步」。"""
    rel = _REL_MARKS.get(mark, "")
    return rel.split("-")[0] if rel else None


def _strip_head(clause):
    """句首的因/让侧关系连词。返回 (mark, 剩余) 或 (None, 原句)。"""
    for w in _REL_HEADS:
        if clause.startswith(w) and len(clause) > len(w):
            return w, clause[len(w):]
    return None, clause


def _strip_tail(clause):
    """句首的果/转侧关系连词。返回 (mark, 剩余) 或 (None, 原句)。"""
    for w in _REL_TAILS:
        if clause.startswith(w) and len(clause) > len(w):
            return w, clause[len(w):]
    return None, clause


def _split_inline(clause):
    """形态①句内双标：句首因标记 + 句中果标记 → (rel, cond, main)；否则 None。
    果标记优先长词（「所以/那么」先于「则/便」），降低单字误切。"""
    head, rest = _strip_head(clause)
    if not head:
        return None
    for tails in (tuple(w for w in _REL_TAILS if len(w) >= 2),
                  tuple(w for w in _REL_TAILS if len(w) < 2)):
        best_pos, best_tail = -1, None
        for t in tails:
            p = rest.find(t)
            if p > 0 and (best_pos < 0 or p < best_pos):
                best_pos, best_tail = p, t
        if best_tail:
            cond, main = rest[:best_pos], rest[best_pos + len(best_tail):]
            if cond and main:
                return _rel_family(head), cond, main
    return None


def _focus_of(clause):
    """句中第一个疑问词（最长优先）的疑问焦点属性——词典里没有的词不产生焦点。"""
    for w in _QUESTION_WORDS:
        if w in clause:
            return _GV["question_words"][w], w
    return None, None


def _clause_intent(c):
    focus, _ = _focus_of(c)
    # 变更 #29：纠正/反问句——「不是…吗」「难道…（吗）」是用户在质疑/纠正规则，
    # 不是求确认。「不是」与「难道」是断言性反问标记；「铁不会生锈吗」（只有「不」）
    # 仍是普通求确认，不在此列。
    if focus == "确认" and ("不是" in c or "难道" in c):
        return "challenge"
    has_probe = any(k in c for k in _COND_PROBES)
    if has_probe and focus in ("事实", "数量", None) and any(
            q in c for q in ("什么", "哪些", "哪", "几")):
        return "query_condition"     # 需要什么条件/哪些条件 → 问条件
    if focus == "方式" and ("才" in c or "能" in c or "让" in c):
        return "query_condition"     # 怎么样才能/怎么让 → 也是问条件
    if focus == "方式":
        return "query_how"
    if focus == "原因":
        return "query_why"
    if focus == "定义":
        return "query_fact"
    if focus == "确认":
        return "query_confirm"
    if any(c.startswith(k) for k in _IMPERATIVES):
        return "command"
    if focus in ("事实", "数量"):
        return "query_fact"
    return "none"


def parse(text):
    """拆句成逻辑式列表。返回：
    {clauses: [{raw, intent, negated, has_condition,
                rel, cond_part, main_part, absorbed}],
     any_need: 是否存在条件/方式疑问, any_negated: 是否有否定}
    嵌套字段：rel∈{条件,因果,让步} 或 None；cond_part/main_part 仅嵌套时非空；
    absorbed=True 表示该分句是下一句的条件部，已被吸收、不应独立处理。"""
    clauses = []
    for raw in CLAUSE_SPLIT.split(text.strip()):
        raw = raw.strip()
        if not raw:
            continue
        clauses.append({
            "raw": raw,
            "intent": _clause_intent(raw),
            "negated": any(k in raw for k in _NEG_WORDS),
            "has_condition": any(k in raw for k in _COND_MARKS),
            "rel": None, "cond_part": "", "main_part": "", "absorbed": False,
        })
    _nest(clauses)
    return {
        "clauses": clauses,
        "any_need": any(c["intent"] in ("query_condition",) for c in clauses),
        "any_negated": any(c["negated"] for c in clauses),
    }


def _nest(clauses):
    """一层从句嵌套：形态①句内双标，形态②跨分句配对。就地修改。"""
    for i, c in enumerate(clauses):
        # 形态①：句内双标（因为A所以B）
        hit = _split_inline(c["raw"])
        if hit:
            rel, cond, main = hit
            c.update(rel=rel, cond_part=cond, main_part=main,
                     intent=_clause_intent(main),
                     negated=any(k in main for k in _NEG_WORDS),
                     has_condition=True)
            continue
        # 形态②：因/让侧开头、句内无果标记 → 与下一分句配对
        head, cond = _strip_head(c["raw"])
        if not head or i + 1 >= len(clauses):
            continue
        if any(t in cond for t in _REL_TAILS if len(t) >= 2):
            continue  # 双标但没拆动（如主句部缺失），不往下一句挂
        nxt = clauses[i + 1]
        if nxt["rel"] or nxt["absorbed"]:
            continue
        _, main = _strip_tail(nxt["raw"])  # 「如果A，那么B」剥掉「那么」
        c["absorbed"] = True
        nxt.update(rel=_rel_family(head), cond_part=cond, main_part=main,
                   intent=_clause_intent(main),
                   negated=any(k in main for k in _NEG_WORDS),
                   has_condition=True)
