# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】由训练获得更好的权重打分能力（规格 §6）
# 【施工方】训练界/步长/安全只升不降；outcome 接回（变更 #30：频率×成功率）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""训练层·在线速（规格书第 6 节）：频率统计 → 类型权重微调 + 结果对账。
规则（规格 §3）：类型权重可调范围 = 种子值 ±20%，步长 0.02，每次变化记 weights.log。
每 TRAIN_EVERY 轮训练一次：轮次太短统计不稳，权重会抖动。"""
from . import attention

TRAIN_EVERY = 10     # 每 10 轮训练一次
STEP = 0.02          # 步长（规格书）
BAND = 0.2           # ±20%（规格书）
HOT = 1.2            # 频率高于均值此倍数 → 上调
COLD = 0.8           # 频率低于均值此倍数 → 下调

# 第二轮审查 M5：安全告警命中天然稀少，频率训练会让它持续向下漂，
# 违背"安全永远最高优先"的设计意图——该类型只许升、不许自动降。
NEVER_TRAIN_DOWN = {"安全告警"}


def on_round(sess, task):
    """每轮调用：收集结果信号（对账的原料，规格书：结果对账→强度更新）。
    变更 #30 条文②：预测验证命中/落空也入 outcome 账——推演类的成败第一次有了记录。"""
    if task.get("emergency"):
        sess.stats.bump("outcome:安全告警:已建议")
    for eid in task.get("fired_reminders", []):
        sess.stats.bump("outcome:提醒:达成")
    v = task.get("verified")
    if v:
        sess.stats.bump("outcome:推演:" + ("命中" if v.get("confirmed") else "落空"))


def _success_rate(counts, etype):
    """类型的 outcome 成功率：好信号/(好+坏)；没有任何记录 → 0.5 中性（不冤不捧）。
    好信号：达成/命中/已建议；坏信号：落空。"""
    good = sum(v for k, v in counts.items()
               if k.startswith("outcome:{}:".format(etype))
               and k.rsplit(":", 1)[-1] in ("达成", "命中", "已建议"))
    bad = sum(v for k, v in counts.items()
              if k.startswith("outcome:{}:".format(etype))
              and k.rsplit(":", 1)[-1] in ("落空",))
    if good + bad <= 0:
        return 0.5
    return good / (good + bad)


def maybe_train(sess):
    """到点训练：类型的近期训练分高 → 权重上调（看得更早），反之降调。
    变更 #30 条文②：训练分 = 频率 × (0.5 + 成功率)——频率对账之外，
    结果好坏第一次接回权重（此前 outcome 只写不读，是断头路）。
    返回本次变化的 [(类型, 旧值, 新值)]，无变化返回空列表。"""
    if sess.state.counter == 0 or sess.state.counter % TRAIN_EVERY != 0:
        return []
    counts = sess.stats.counts
    type_freq = {t: counts.get("type:" + t, 0.0) for t in sess.state.weights}
    scored = {t: f * (0.5 + _success_rate(counts, t)) for t, f in type_freq.items()}
    total = sum(scored.values())
    if total <= 0:
        return []
    avg = total / len(scored)
    changed = []
    for t, f in sorted(scored.items()):
        seed = attention.SEED_WEIGHTS.get(t, 1.0)
        lo, hi = round(seed * (1 - BAND), 3), round(seed * (1 + BAND), 3)
        cur = sess.state.weights.get(t, seed)
        if f >= avg * HOT and cur < hi:
            sess.adjust_weight(t, min(hi, round(cur + STEP, 3)), signal="TRAIN")
            changed.append((t, cur, sess.state.weights[t]))
        elif (f <= avg * COLD and cur > lo
              and t not in NEVER_TRAIN_DOWN):  # 安全类永不自动降权（M5）
            sess.adjust_weight(t, max(lo, round(cur - STEP, 3)), signal="TRAIN")
            changed.append((t, cur, sess.state.weights[t]))
    return changed
