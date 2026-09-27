# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】由训练获得更好的权重打分能力（规格 §6）；【施工方】频率计数带衰减（每轮×0.95）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""在线统计（规格书第 6 节）：
- 频率计数带衰减：每轮 ×0.95，低于 0.01 剪掉（近期习惯权重大，远期遗忘）
- weights.log 哈希链：旧值→新值→触发信号→时间，仿知识库审计链，可 verify 验伪"""
import hashlib
import json
import time

from . import config

DECAY = 0.95
PRUNE_BELOW = 0.01
LOG_FILE = config.STATE / "weights.log"
LOG_STATE = config.STATE / "weights_logstate.json"


class Stats:
    def __init__(self, counts=None):
        self.counts = counts or {}

    def bump(self, topic, amount=1.0):
        self.counts[topic] = self.counts.get(topic, 0.0) + amount

    def round_decay(self):
        """每轮整体衰减并剪枝。"""
        self.counts = {k: v * DECAY for k, v in self.counts.items()
                       if v * DECAY >= PRUNE_BELOW}

    def top(self, n=10):
        return sorted(self.counts.items(), key=lambda kv: -kv[1])[:n]


def _chain_hash(prev, content):
    return hashlib.sha256("{}|{}".format(prev, content).encode("utf-8")).hexdigest()[:32]


def log_weight_change(signal, old, new):
    """记录一次类型权重变化（规格书：每次权重的变化都要记录下来）。"""
    config.ensure()
    prev = "GENESIS"
    if LOG_STATE.exists():
        try:
            prev = json.loads(LOG_STATE.read_text(encoding="utf-8")).get("head", "GENESIS")
        except ValueError:
            prev = "GENESIS"
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
             "signal": signal, "old": old, "new": new}
    content = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    h = _chain_hash(prev, content)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"prev": prev, "hash": h, "entry": entry},
                           ensure_ascii=False) + "\n")
    config.atomic_write(LOG_STATE, json.dumps({"head": h}))


def verify_log():
    """校验哈希链完整。返回 (是否完整, 记录条数)。"""
    if not LOG_FILE.exists():
        return True, 0
    prev, count = "GENESIS", 0
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        content = json.dumps(rec["entry"], ensure_ascii=False, sort_keys=True)
        if _chain_hash(rec["prev"], content) != rec["hash"]:
            return False, count
        prev, count = rec["hash"], count + 1
    if LOG_STATE.exists():
        try:
            head = json.loads(LOG_STATE.read_text(encoding="utf-8")).get("head")
        except ValueError:
            return False, count
        return head == prev, count
    return True, count
