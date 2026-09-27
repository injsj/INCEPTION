# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】回答用户的问题和执行的任务——先行为分类再进解析（规格 §2）
# 【施工方】模式匹配分流实现；词表迁入词典（变更 #22）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""对话行为分类（规格书第 2 节：模式匹配，廉价分流；变更 #22：词表迁入词典）。
先分清是问候/询问/命令/陈述，再决定走哪条处理线。
v2：问候核/疑问词/祈使头/句首副词全部查 lexicon.grammar，
代码里没有任何具体词的名单——词典加词即生效。"""
import re

from . import lexicon

_GV = lexicon.grammar_view()
_GREETING_ROOTS = sorted(_GV["greeting_roots"], key=len, reverse=True)
_QUESTION_SIGNS = tuple(_GV["question_words"])
_COMMAND_HEADS = sorted(_GV["imperatives"], key=len, reverse=True)
_LEAD_ADVERBS = sorted(_GV["lead_adverbs"], key=len, reverse=True)

# 语气词：问候核后面只许跟这些（再加标点），否则句子里有实义内容，不是纯问候
_MODAL = set("呀啊呢嘛吗么哦噢哇啦哈咯哟")
_PUNCT = "，。！？!?,．.~～、 "
# 「X好」组合：称呼/时段 + 好（+语气词）——「新年好」「各位好呀」之类不再漏
_HI_PAT = re.compile(r"^(你|您|你们|各位|大家|早上|上午|中午|下午|晚上|夜里|新年)?好+$")


def _is_greeting(t):
    """组合式问候识别（规格书：组句的逆运算，不是撞名单）。

    结构：问候核 + 语气词/标点，整句再无其他实义 → 问候。
    「你好呀」「早」「在么」「各位好」都认得；「你好，十乘十是多少」不认
    （逗号后有实义内容，会落给后面的 question 判定）。
    """
    s = t.strip().strip(_PUNCT).lower()
    if not s:
        return False
    if _HI_PAT.match(s):
        return True
    for root in _GREETING_ROOTS:
        if not s.startswith(root):
            continue
        rest = s[len(root):].strip(_PUNCT)
        if not rest:
            return True                      # 裸问候核：「你好」「在吗」
        if all(ch in _MODAL or ch in _PUNCT for ch in rest):
            return True                      # 问候核+语气词：「你好呀」「嗨啊」
        if rest == root:                     # 叠用：「嗨嗨」
            return True
    return False


def classify(text):
    """返回 greeting / question / command / statement 之一。"""
    t = text.strip()
    if not t:
        return "statement"
    if _is_greeting(t):
        return "greeting"
    if t.endswith(("?", "？")) or any(s in t for s in _QUESTION_SIGNS):
        return "question"
    # 剥句首副词再认指令头：「快帮我查一下」「请提醒我」
    head = t
    for adv in _LEAD_ADVERBS:
        if head.startswith(adv) and len(head) > len(adv):
            head = head[len(adv):]
            break
    if any(head.startswith(h) for h in _COMMAND_HEADS):
        return "command"
    return "statement"
