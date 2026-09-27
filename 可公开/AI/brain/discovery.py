# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】经验提炼：在使用中自己发现知识里没人教过的隐藏用法，是设计的重要一环（变更 #8 拍板，规格 §6）
# 【施工方】五种矿工的具体算法与阈值（共现/捷径/趋同/数值趋同/语词共现）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""经验提炼·低配版（规格「经验提炼」节，变更 #8）。

设计者原话：在使用中自己发现知识里没人教过的隐藏用法（如自己发现 3×3=3+3+3），
不是计数、不是攒够再盘点——发现发生在推演过程中。独立于神经网络（不依赖外部 AI）。

工程落地：推演全留痕（档案记录触发的规则链）→ 矿工定期翻档案 → 候选附证据进建议队列
→ 人裁决入库。四种矿工：
  A 共现  ：两条规则常同次触发 → 可能存在共同上游/隐藏联系
  B 捷径  ：A→B→C 规则链反复出现 → 也许有直达规律（新知识的雏形，附原始链证据）
  C 趋同  ：多条规则指向同一变量 → 可能是同一现象的不同描述
  D 数值趋同：两条算术路径算出同一结果（变更 #11 原子操作留痕后解锁，
              如 3*3 与 3+3+3 都得 9——设计者原话示例的落地点）

阈值是施工方默认值（明文），可调。
"""
import json
from collections import Counter
from itertools import combinations

from . import atoms, config

CO_OCCUR_MIN = 3     # 共现阈值（同一份档案里一起触发算一次）
CHAIN_MIN = 3        # 捷径阈值（同一条链出现次数）
SCAN_MAX = 200       # 每次最多翻多少份档案
NUM_CONVERGE_MIN = 2  # 数值趋同阈值（同一结果至少几条不同路径）


def _archives():
    # rglob：兼容旧版平铺档案与第二轮审查 L3 起的月度子目录
    files = sorted(config.ARCHIVE.rglob("task_*.json"))
    return files[-SCAN_MAX:]


def _traces():
    """读出每份档案的推演规则链 [(archive名, [规则id...]), ...]。"""
    out = []
    for f in _archives():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        tr = d.get("reason_trace")
        if tr:
            out.append((f.name, tr))
    return out


def _parse(cond):
    return cond.split("=", 1)[0] if "=" in cond else cond


# ── 矿工 A：共现 ──────────────────────────────────────────────
def mine_cooccurrence(traces):
    """同一档案里成对出现的规则，计数达阈值 → 候选。
    变更 #29：R↔R（种子/演示规则对）不再产出——它们在同一批演示档案里成对触发
    是设计内行为，没有挖掘价值（旧账清理已作废 43 条同类噪声，这里掐源头）。"""
    counter = Counter()
    for name, tr in traces:
        for a, b in combinations(sorted(set(tr)), 2):
            if a.startswith("R") and b.startswith("R"):
                continue   # 种子演示对，不挖
            counter[(a, b)] += 1
    return [{"kind": "cooccur",
             "content": "{} ↔ {}".format(a, b),
             "why": "共同触发 {} 次，也许存在共同上游或隐藏联系".format(n)}
            for (a, b), n in counter.items() if n >= CO_OCCUR_MIN]


# ── 矿工 B：捷径（规则链 → 候选直达规则）────────────────────────
def mine_shortcuts(traces, rules):
    """链 X→Y（X 的结论变量喂给 Y 的条件）出现达阈值 → 候选合并规则。
    档案可能跨越种子期(R1)与入库期(G1)，id 体系不同但结构同构——两边都纳入。"""
    from .reason import SEED_RULES
    by_id = {r["id"]: r for r in list(rules or []) + list(SEED_RULES)}

    def feeds(x, y):
        """X 的结论变量是否出现在 Y 的条件里。"""
        if x not in by_id or y not in by_id:
            return None
        out_var = _parse(by_id[x]["then"])
        for c in by_id[y]["when"]:
            if _parse(c) == out_var:
                return out_var
        return None

    counter = Counter()
    for name, tr in traces:
        seq = [r for r in tr if r in by_id]
        for x, y in zip(seq, seq[1:]):
            if feeds(x, y):
                counter[(x, y)] += 1
    cands = []
    for (x, y), n in counter.items():
        if n < CHAIN_MIN:
            continue
        rx, ry = by_id[x], by_id[y]
        mid_var = _parse(rx["then"])
        when = list(rx["when"]) + [c for c in ry["when"] if _parse(c) != mid_var]
        # 第三轮审查：合并后的条件若对同一变量给出两个不同取值（如 温度=高 与 温度=低），
        # 合并规则永远不可满足——这种候选是噪声，直接跳过。
        seen_val = {}
        contradictory = False
        for c in when:
            if "=" not in c:
                continue
            var, val = c.split("=", 1)
            if var in seen_val and seen_val[var] != val:
                contradictory = True
                break
            seen_val[var] = val
        if contradictory:
            continue
        tier = rx["tier"] if rx["tier"] == ry["tier"] else "中"  # 不同档取保守中档
        usage = "当[{}]→则[{}]档[{}]域[{}]".format(
            ";".join(when), ry["then"], tier, ry["domain"])
        cands.append({"kind": "shortcut", "content": usage,
                      "why": "链 {}→{} 出现 {} 次，也许存在直达规律".format(x, y, n)})
    return cands


# ── 矿工 C：趋同（静态）───────────────────────────────────────
def mine_convergence(rules):
    """多条规则指向同一结论变量 → 可能是同一现象的不同描述。"""
    groups = {}
    for r in rules or []:
        groups.setdefault(_parse(r["then"]), []).append(r)
    return [{"kind": "converge",
             "content": var,
             "why": "{} 条规则指向同一变量：{}，也许是同一现象的不同描述，值得看一眼".format(
                 len(rs), "、".join(x["id"] for x in rs))}
            for var, rs in groups.items() if len(rs) >= 2]


# ── 矿工 D：数值趋同（变更 #11 解锁）────────────────────────────
def mine_numeric_convergence():
    """两条不同的算术路径算出同一结果 → 候选等式规律（如 3*3 与 3+3+3 都得 9）。
    原料：atoms.py 的 atomic_ops.jsonl 留痕。"""
    by_result = {}
    for rec in atoms.load_log():
        if rec.get("op") != "计算" or not rec.get("ok"):
            continue
        val = rec.get("output")
        if not isinstance(val, (int, float)):
            continue
        by_result.setdefault(val, set()).add(rec["input"])
    return [{"kind": "num_converge",
             "content": "{}（都得 {}）".format(" = ".join(sorted(exprs)), val),
             "why": "{} 条不同计算路径结果一致（{}），也许存在等式规律".format(
                 len(exprs), "、".join(sorted(exprs)))}
            for val, exprs in by_result.items() if len(exprs) >= NUM_CONVERGE_MIN]


# ── 矿工 E：语词共现（变更 #24——矿工此前只读规则链编号，不读人话）────────────
LANG_COC_MIN = 3     # 语词共现阈值（分散在多少份不同档案里一起出现）


def mine_language_cooccur(rules):
    """扫档案 input 原文：规则词表里的概念在你们的对话里反复一起出现
    （分散 ≥3 份档案）→ 候选「这两个概念总是一起出现，也许有隐藏联系」。
    与矿工 A 的区别：A 挖规则编号共现（机器痕迹），E 挖人话里的概念共现（原始语言）。
    证据档案名随候选附上，人裁决。"""
    vocab = set()
    from . import lexicon as _lex
    _gv = _lex.grammar_view()
    # 程度/模态/虚词不作概念词（它们在每条规则里都有，收了全是噪声）
    _stop = (set(_gv["modals"]) | set(_gv["degrees"]) |
             {"是", "无", "有", "它", "进行", "不变", "改变", "成立"})
    for r in rules or []:
        for c in list(r.get("when", [])) + [r.get("then", "")]:
            if "=" in c:
                var, val = c.split("=", 1)
                if len(var) >= 2:
                    vocab.add(var)
                # 单字实词（铁/氧/水……）也是概念，不能只收两字以上
                if val.isalpha() and val not in _stop:
                    vocab.add(val)
            elif len(c) >= 2:
                vocab.add(c)
    if not vocab:
        return []
    counter = Counter()
    for f in _archives():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        text = d.get("input") or ""
        if not isinstance(text, str):
            continue
        present = sorted({w for w in vocab if w in text})
        for a, b in combinations(present, 2):
            counter[(a, b)] += 1
    return [{"kind": "lang_cooccur",
             "content": "{} ↔ {}".format(a, b),
             "why": "这两个概念在你的 {} 份对话档案里一起出现，也许有隐藏联系，"
                    "值得看看要不要提炼成规则".format(n)}
            for (a, b), n in counter.items() if n >= LANG_COC_MIN]


def mine(rules=None):
    """跑全部矿工，返回候选列表（不直接入队，由 session 交给 refine.add_suggestions）。"""
    traces = _traces()
    return (mine_cooccurrence(traces)
            + mine_shortcuts(traces, rules)
            + mine_convergence(rules)
            + mine_numeric_convergence()
            + mine_language_cooccur(rules))
