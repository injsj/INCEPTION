# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【拍板】规则入库：G 类条目两张脸、分区=确定性程度、流转与对账晋升（变更 #7，规格 §4）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""规则入库加载器（规格 §4「规则入库」，变更 #7 拍板）：
- 会话启动时从知识库拉 G 类（推演规则）条目，"用法"字段的执行格式解析成规则
- 断连 / 库里没有 G 类 → 返回 None，调用方用代码内种子规则兜底（断连降级同规）
- 对账计数（命中/落空）落 state\\rule_reconcile.json，攒够阈值 → 晋升建议（见 refine.py）
"""
import json
import re

from . import config

RULE_TYPE = "推演规则"
RULE_USAGE_PAT = re.compile(
    r"当\[(?P<when>.*?)\]→则\[(?P<var>[^=\]]+?)=(?P<val>[^\]]+?)\]"
    r"档\[(?P<tier>高|中|低)\]域\[(?P<domain>[^\]]+?)\]")
PROMOTE_HITS = 3       # 晋升信任区：累计命中 ≥3
PROMOTE_RATE = 0.7     # 且命中率 ≥70%（默认值，明文可调）
COUNT_FILE = config.STATE / "rule_reconcile.json"


def parse_usage(text):
    """把「当[饥饿=极高]→则[觅食行为=激烈]档[高]域[生物]」解析成规则字典。失败返回 None。"""
    m = RULE_USAGE_PAT.search(text or "")
    if not m:
        return None
    return {
        "when": [c.strip() for c in m.group("when").split(";") if c.strip()],
        "then": "{}={}".format(m.group("var").strip(), m.group("val").strip()),
        "tier": m.group("tier"),
        "domain": m.group("domain").strip(),
    }


def load_rules(client):
    """从知识库拉全部可读的 G 类规则。返回 [{id(KB标记), when, then, tier, domain, source}]
    或 None（断连/没有 G 类条目/全部解析失败）。"""
    try:
        zones = client.catalog()
    except Exception:
        return None
    got = []
    for zone in zones:
        for e in zone["entries"]:
            if e.get("type") != RULE_TYPE:
                continue
            try:
                entry = client.read(e["id"])
            except Exception:
                continue
            rule = parse_usage(entry.get("usage", ""))
            if rule:
                rule["id"] = e["id"]
                rule["source"] = "知识库 {}（{}）".format(e["id"], entry.get("title", ""))
                got.append(rule)
    return got or None


# ──────────────────── 对账计数（晋升的依据）────────────────────

def _load_counts():
    if COUNT_FILE.exists():
        try:
            return json.loads(COUNT_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def _save_counts(c):
    config.ensure()
    config.atomic_write(COUNT_FILE, json.dumps(c, ensure_ascii=False, indent=2))


def record_outcome(rule_id, confirmed):
    """对账计数：rule_id 用知识库标记（如 G2）。confirmed=预测是否命中。"""
    c = _load_counts()
    rec = c.setdefault(rule_id, {"hit": 0, "miss": 0})
    rec["hit" if confirmed else "miss"] += 1
    _save_counts(c)


def promotable(rules):
    """达到晋升阈值的规则列表：[{id, hit, miss, rate}]。"""
    c = _load_counts()
    out = []
    for r in rules or []:
        rid = r.get("id", "")
        rec = c.get(rid)
        if not rec:
            continue
        total = rec["hit"] + rec["miss"]
        rate = rec["hit"] / total if total else 0.0
        if rec["hit"] >= PROMOTE_HITS and rate >= PROMOTE_RATE:
            out.append({"id": rid, "hit": rec["hit"], "miss": rec["miss"],
                        "rate": round(rate, 3)})
    return out
