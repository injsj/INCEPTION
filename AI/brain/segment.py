# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】拆句到最小单位（规格 §1）；【施工方】最大匹配分词与生词合并（变更 #29）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""分词：词典正向最大匹配（规格书第 1 节）。
- 知识标记（如 P1）整段识别为实体
- 切不出的字记为"未知"（生词），由上层收集，将来走 REQ 申请入词典
- 变更 #29：连续未登录单字合并成一个词段——「环、境、潮、湿」散字刷屏
  既不是人话也不利于学习（学就学「环境潮湿」这种词段，不是单字碎片）。
"""
import re

ENTITY = re.compile(r"[A-Za-z]+\d+")   # 知识标记：字母+数字
MAX_WORD = 6                            # 最长词长（超出按单字处理）


def segment(text, lexicon):
    tokens, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace() or not ('一' <= ch <= '鿿') and not ch.isalnum():
            i += 1            # 空白与标点不切不报：标点不是生词
            continue
        m = ENTITY.match(text, i)
        if m:
            tokens.append(("标记", m.group()))
            i = m.end()
            continue
        hit = None
        for L in range(min(MAX_WORD, n - i), 0, -1):
            w = text[i:i + L]
            if w in lexicon:
                hit = w
                break
        if hit:
            tokens.append(("词", hit))
            i += len(hit)
        else:
            # 连续未登录字符合并为一个词段（标点/空白/字母数字断开）
            j = i
            while j < n and not text[j].isspace() and not ENTITY.match(text, j):
                probe = None
                for L in range(min(MAX_WORD, n - j), 0, -1):
                    if text[j:j + L] in lexicon:
                        probe = text[j:j + L]
                        break
                if probe:
                    break
                if not ('一' <= text[j] <= '鿿'):   # 只合并汉字段，其他字符逐字
                    break
                j += 1
            if j > i:
                tokens.append(("未知", text[i:j]))
                i = j
            else:
                tokens.append(("未知", text[i]))
                i += 1
    return tokens
