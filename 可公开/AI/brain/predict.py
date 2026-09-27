# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】每次推演输出带置信标注的预测→结果对账→强度更新（规格 §4）
# 【施工方】预测持久化与自动验证触发器（变更 #10）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""预测持久化 + 自动验证触发器（变更 #10，规格 §6 对账闭环的最后一块）。

此前缺口：推演结论只留在当轮档案里，对账全靠人手动调 Reasoner.reconcile()，
"预测→事后验证→规则升降档/晋升"的闭环没有触发器。

本模块三件事：
1. 持久化：每次推演产出结论时存一条预测（state\\predictions.json，原子写）
2. 触发：任何提醒到期时，会话自动带出待验证预测清单（提醒是天然的对账时机——
   时间到了，顺便看看之前的推演准不准）
3. 验证：用户说「验证 Y1 命中」/「验证 Y1 落空」→ 标记状态 + 对该预测
   规则链逐条记对账（ruleload.record_outcome，晋升建议的原料）+ weights.log 留痕
"""
import json
import re
import time

from . import config, ruleload, stats

PRED_FILE = config.STATE / "predictions.json"
MAX_RESOLVED = 100   # 已验证的只留最近 100 条，防文件无限增长（待验证的永不丢）

VERIFY_PAT = re.compile(r"^验证\s*([Yy]\d+)\s*(命中|证实|落空|证伪)")


def _load():
    if PRED_FILE.exists():
        try:
            return json.loads(PRED_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"next_id": 1, "items": []}


def _save(d):
    config.ensure()
    # 截断：待验证全留，已验证只留最近 MAX_RESOLVED 条
    pend = [p for p in d["items"] if p["status"] == "待验证"]
    done = [p for p in d["items"] if p["status"] != "待验证"][-MAX_RESOLVED:]
    d["items"] = pend + done
    config.atomic_write(PRED_FILE, json.dumps(d, ensure_ascii=False, indent=2))


def save_prediction(source, derived, trace):
    """推演产出结论时存档。derived 为结构化结论 [{rule, then, conf}]。
    返回预测编号（如 Y1）；没有结论返回 None。
    批一补丁：同一来源文本已有待验证预测时不重复堆叠，返回已有编号。"""
    if not derived:
        return None
    d = _load()
    src = source[:80]
    for p in d["items"]:
        if p["status"] == "待验证" and p["source"] == src:
            return p["id"]
    pid = "Y{}".format(d["next_id"])
    d["next_id"] += 1
    d["items"].append({
        "id": pid,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": src,
        "conclusions": [{"then": x["then"], "conf": x["conf"], "rule": x["rule"]}
                        for x in derived],
        # 规则链去重并去掉「(置信升级)」等留痕后缀，只留纯规则编号
        "rules": sorted({t.split("(")[0] for t in trace}),
        "status": "待验证",
        "verified_at": "",
    })
    _save(d)
    return pid


def pending():
    return [p for p in _load()["items"] if p["status"] == "待验证"]


def fmt_pending(n=3):
    """提醒到期时的对账提示文案。无待验证返回 None。"""
    pend = pending()
    if not pend:
        return None
    recent = "；".join("{}: {} → {}（{:.0%}）".format(
        p["id"], p["source"][:16], "、".join(c["then"] for c in p["conclusions"][:2]),
        p["conclusions"][0]["conf"]) for p in pend[-n:])
    return ("📋 顺便核对：我之前推演过 {} 条结论还没验证——应验了就告诉我「验证 编号 命中」，"
            "没应验就说「验证 编号 落空」，我会据此调整规则强度。最近：{}"
            .format(len(pend), recent))


def parse_verify(text):
    """「验证 Y1 命中」→ (pid, confirmed)；不匹配返回 None。"""
    m = VERIFY_PAT.match(text.strip())
    if not m:
        return None
    return m.group(1).upper(), m.group(2) in ("命中", "证实")


def verify(pid, confirmed):
    """对账落库：标记预测 + 规则链逐条记 outcome + weights.log 留痕。"""
    d = _load()
    for p in d["items"]:
        if p["id"] == pid:
            if p["status"] != "待验证":
                return False, "{} 已经验证过了（{}）".format(pid, p["status"])
            p["status"] = "已验证-命中" if confirmed else "已验证-落空"
            p["verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            for rid in p["rules"]:
                ruleload.record_outcome(rid, confirmed)
            stats.log_weight_change(
                "VERIFY:{} {}".format(pid, "命中" if confirmed else "落空"),
                "待验证", p["status"])
            _save(d)
            return True, ("{} 已验证{}。规则链 {} 各记一次{}，晋升计数已更新。".format(
                pid, "命中 ✓" if confirmed else "落空 ✗",
                "、".join(p["rules"]), "命中" if confirmed else "落空"))
    return False, "没有找到预测 " + pid
