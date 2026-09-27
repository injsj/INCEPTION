# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【拍板】第一站：玩具句端到端（规格 §10 三站止损制）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""玩具句端到端：一句话走完
语言识别 → 分词 → 多候选解析+打分 → 白名单执行 → 知识库 → 带置信回答 → 档案留痕
（规格书第 10 节"第一站"的最小真实闭环）"""
import sys

from . import config, executor, intent, kb, langid, lexicon, segment, trace


def run(sentence, api_key=None):
    task = {"input": sentence}

    # 1) 语言识别
    task["lang"] = langid.detect(sentence)

    # 2) 分词（词典最大匹配；生词标记）
    tokens = segment.segment(sentence, lexicon.seed())
    task["tokens"] = tokens
    task["unknown_words"] = [w for k, w in tokens if k == "未知"]

    # 3) 多候选解析 + 打分 + 选择（低置信则反问）
    cands = intent.parse(sentence, tokens)
    task["candidates"] = cands
    chosen, decision, conf = intent.decide(cands)
    task["decision"] = decision
    task["confidence"] = conf

    # 4) 白名单执行 + 知识库
    if decision == "ask":
        reply = "我不太确定你的意思（最佳候选置信 {:.0%}）。你是想：{} 吗？".format(
            conf, " / ".join(c["note"] for c in cands[:2]))
    elif chosen["action"] == "unknown":
        reply = "我还不能理解这句话。" + (
            "（生词：{}，将申请录入词典）".format("、".join(task["unknown_words"]))
            if task["unknown_words"] else "")
    else:
        client = kb.get_client(api_key)
        if client is None:
            # 第三轮审查 R3-1：密钥未配置时降级提示，不再崩在 executor 里
            reply = "我现在联系不上知识库（密钥未配置或服务没启动），这条先记在案。"
            task["reply"] = reply
            task["kb_down"] = True
            return reply, trace.record(task)
        status, out = executor.execute(chosen["action"], chosen["slots"], client)
        task["exec_status"] = status
        reply = out if status != "refused" else "[白名单拦截] " + out

    task["reply"] = reply
    path = trace.record(task)
    return reply, path


def main():
    sentence = sys.argv[1] if len(sys.argv) > 1 else "查一下P1"
    reply, path = run(sentence)
    print("你说：", sentence)
    print("AI  ：", reply)
    print("档案：", path)


if __name__ == "__main__":
    main()
