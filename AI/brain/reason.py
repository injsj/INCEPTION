# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】以逻辑推演为主、生物以概率为主要决策来源；动用一切知识攻坚直到耗尽方法（规格 §4，变更 #23）
# 【拍板】试假设法/类比攻坚/结构类比（变更 #23/#25/#28）；【施工方】规则求值引擎实现
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""推演层：因果规则网络（规格书第 4 节）。

- 规则 = {条件[], 结论, 强度(高/中/低), 域, 出处}：逻辑定结构，概率定强度
- 三档定性概率（规格书）：高 0.9 / 中 0.6 / 低 0.3——施工方默认值，明文可查，
  对账后升降档，每次变化记 weights.log
- 双域推演：
    逻辑域（数学/物理/逻辑等）= 精确计算：条件全确定才出结论，结论置信 100%，
        条件不足 → 输出"未知/需要观察"（观察是合法动作，规格书原文）
    概率域（生物/智能体等）= 概率传播为王：结论置信 = min(档值, 各条件置信最小值)，
        逻辑只搭脚手架
- 双向：forward(观察正推) / backward(目标反查路径)
- 思想自由：推演方向不限；输出强制带置信标注；执行另过白名单（规格 §5）
- 冷启动：种子规则由施工方填常识默认值（规格 §6），全部明文列在 SEED_RULES
"""
import re
from concurrent.futures import ThreadPoolExecutor

from . import ruleload, stats

TIERS = {"高": 0.9, "中": 0.6, "低": 0.3}
TIER_ORDER = ["低", "中", "高"]
LOGIC_DOMAINS = ("数学", "物理", "逻辑", "化学")
MAX_CHAIN = 20   # 前向链接迭代上限（防无限循环）

# ──────────────────── 种子规则（施工方填，出处写明，可随时改）────────────────────
# 域=生物 的三条来自你的设计原话（饿到极限的野兽推演示例）
SEED_RULES = [
    {"id": "R1", "when": ["饥饿=极高"], "then": "觅食行为=激烈",
     "tier": "高", "domain": "生物", "source": "设计者原话示例"},
    {"id": "R2", "when": ["觅食行为=激烈", "捍卫亲友意念=无"],
     "then": "威胁亲人=高", "tier": "中", "domain": "生物",
     "source": "设计者原话示例（概率为主，非必然）"},
    {"id": "R3", "when": ["威胁亲人=高", "友人在场=是"],
     "then": "目标转为友人=中", "tier": "中", "domain": "生物",
     "source": "设计者原话示例"},
    {"id": "R4", "when": ["饥饿=高", "食物可得=是"],
     "then": "觅食行为=缓和", "tier": "高", "domain": "生物",
     "source": "施工方常识默认"},
    {"id": "R5", "when": ["自制力=不足", "觅食行为=激烈"],
     "then": "突破禁忌=高", "tier": "中", "domain": "生物",
     "source": "设计者原话示例（自制力不足）"},
    {"id": "R6", "when": ["截止分钟<10"], "then": "紧迫度=高",
     "tier": "高", "domain": "逻辑", "source": "规格书第3节紧迫度分档"},
    {"id": "R7", "when": ["受力=平衡"], "then": "加速度=零",
     "tier": "高", "domain": "物理", "source": "施工方常识默认（牛顿第一定律）"},
]


class Fact:
    __slots__ = ("value", "conf", "origin", "domain")

    def __init__(self, value, conf=1.0, origin="用户陈述", domain=None):
        self.value, self.conf, self.origin = value, conf, origin
        self.domain = domain   # 域标签：哪条规则推的挂哪条规则的域；用户陈述=None（通配）


def _parse_cond(cond):
    """「截止分钟<10」→ (var, op, val)；其余按 「变量=值」。"""
    m = re.match(r"^([^<>=!]+)(<|<=|>|>=)(-?\d+(?:\.\d+)?)$", cond)
    if m:
        return m.group(1), m.group(2), float(m.group(3))
    if "=" in cond:
        var, val = cond.split("=", 1)
        return var, "=", val
    return cond, "=", "是"


class Reasoner:
    def __init__(self, rules=None):
        # 深拷贝：对账会改强度，不能污染全局种子规则（否则下次实例继承上次升降档）
        import copy as _copy
        self.rules = _copy.deepcopy(rules if rules is not None else SEED_RULES)
        self.facts = {}       # 变量 → Fact
        self.trace = []       # 触发的规则（留痕，进档案）
        self.competing = []   # 竞争假设：同变量不同值的并存结论（不互相覆盖）
        self._comp_keys = set()

    # ---- 观察（事实入库）----
    def assert_fact(self, var, value, conf=1.0, origin="用户陈述", domain=None):
        old = self.facts.get(var)
        if old is None or conf > old.conf:
            self.facts[var] = Fact(value, conf, origin, domain)

    def _cond_holds(self, cond, domain=None, facts=None):
        """返回条件置信（不满足返回 None）。数值比较用事实值，否则按字符串相等。
        域拦检（变更 #23）：带域结论只喂给同域规则；用户陈述无域=通配。"""
        facts = self.facts if facts is None else facts
        var, op, val = _parse_cond(cond)
        f = facts.get(var)
        if f is None:
            return None
        if domain and f.domain and f.domain != domain:
            return None   # 跨域结论不消费：物理规则的产物不进生物规则的当口
        if op == "=":
            return f.conf if f.value == val else None
        try:
            num = float(f.value)
        except (TypeError, ValueError):
            return None
        ok = {"<": num < val, "<=": num <= val,
              ">": num > val, ">=": num >= val}[op]
        return f.conf if ok else None

    # ---- 正向传播（观察 → 结论）----
    def _eval_rule(self, r, snapshot):
        """对冻结事实快照纯求值（无副作用，可并行）：条件全满足 → 结论候选，否则 None。"""
        conds = [self._cond_holds(c, r["domain"], facts=snapshot) for c in r["when"]]
        if any(c is None for c in conds):
            return None
        weakest = min(conds)
        if r["domain"] in LOGIC_DOMAINS:
            if weakest < 1.0:
                return None   # 逻辑域：条件不全部确定 → 不推演（答未知）
            conf = 1.0
        else:
            conf = round(min(TIERS[r["tier"]], weakest), 3)
        return (r, conf)

    def forward(self, workers=4):
        """迭代触发规则。返回新增/升级的结论 [{rule, then, conf}]。
        并行（变更 #23，规格 §4「拆得开就并行」）：每轮对冻结快照并行求值全部规则，
        结果按规则序串行落账——竞争假设/置信升级语义与串行版完全一致。"""
        derived = []
        for _ in range(MAX_CHAIN):
            snapshot = dict(self.facts)
            if len(self.rules) >= 8:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    cands = list(ex.map(lambda r: self._eval_rule(r, snapshot),
                                        self.rules))
            else:
                cands = [self._eval_rule(r, snapshot) for r in self.rules]
            changed = False
            for cand in cands:
                if cand is None:
                    continue
                r, conf = cand
                then_var, _, then_val = _parse_cond(r["then"])
                old = self.facts.get(then_var)
                if old is not None and old.value != then_val:
                    # 竞争假设（多假设按可能性排序）：不同值不互相覆盖，并存展示
                    key = (then_var, then_val, r["id"])
                    if key not in self._comp_keys:
                        self._comp_keys.add(key)
                        self.competing.append({"var": then_var, "value": then_val,
                                               "conf": conf, "rule": r["id"]})
                    continue
                if old is not None and old.conf >= conf:
                    continue   # 已有同等或更强结论，不产生新事实（防互指规则重复触发）
                upgraded = old is not None   # 同值但置信更高 → 本次是置信升级
                self.assert_fact(then_var, then_val, conf,
                                 origin="{}({}档)".format(r["id"], r["tier"]),
                                 domain=r["domain"])
                self.trace.append(r["id"] + ("(置信升级)" if upgraded else ""))
                derived.append({"rule": r["id"], "then": r["then"], "conf": conf,
                                "upgraded": upgraded})
                changed = True
            if not changed:
                break
        return derived

    # ---- 反向查路径（目标 → 需要哪些条件）----
    def backward(self, goal_var, depth=5):
        """目标反向找路径。返回 [{rule, conf, when, missing, direct}]：
        direct=True 直接产出目标的规则；False 是支撑子路径（推导缺失条件的规则）。
        直接路径排在前面，同档按置信降序。"""
        paths = []
        self._backward(goal_var, depth, [], paths)
        for p in paths:
            p["direct"] = (p.pop("_goal") == goal_var)
        paths.sort(key=lambda p: (not p["direct"], -p["conf"]))
        return paths

    def _backward(self, var, depth, seen, out):
        if depth <= 0 or var in seen:
            return
        for r in self.rules:
            then_var, _, _ = _parse_cond(r["then"])
            if then_var != var:
                continue
            conf = TIERS[r["tier"]]
            missing = []
            for c in r["when"]:
                hold = self._cond_holds(c, r["domain"])
                if hold is None:
                    cvar, _, _ = _parse_cond(c)
                    missing.append(c)
                    sub = self._backward(cvar, depth - 1, seen + [var], out)
                    if sub:
                        conf = min(conf, sub[0]["conf"])
                else:
                    conf = min(conf, hold)
            out.append({"rule": r["id"], "conf": round(conf, 3),
                        "when": list(r["when"]), "missing": missing,
                        "domain": r["domain"], "_goal": then_var})

    # ---- 对账（结果验证 → 强度升降档，记 weights.log）----
    def reconcile(self, rule_id, confirmed, note=""):
        """预测命中 → 升一档；落空 → 降一档。三档封顶/保底。"""
        for r in self.rules:
            if r["id"] == rule_id:
                i = TIER_ORDER.index(r["tier"])
                j = min(2, i + 1) if confirmed else max(0, i - 1)
                if j != i:
                    old = r["tier"]
                    r["tier"] = TIER_ORDER[j]
                    stats.log_weight_change(
                        "RULE:{} 对账{}".format(r["id"], "命中" if confirmed else "落空"),
                        old + "({})".format(TIERS[old]),
                        r["tier"] + "({})".format(TIERS[r["tier"]]))
                ruleload.record_outcome(r["id"], confirmed)   # 对账计数（晋升依据）
                return r["tier"]
        return None

    # ---- 观察清单（规格书：无法计算→答"未知/需要观察"，观察是合法动作）----
    def observe_wishlist(self):
        """逻辑域规则部分条件已满足、部分缺失 → 列出需要观察什么才能得到答案。"""
        wishes = []
        for r in self.rules:
            if r["domain"] not in LOGIC_DOMAINS:
                continue
            holds = [self._cond_holds(c, r["domain"]) for c in r["when"]]
            miss = [c for c, h in zip(r["when"], holds) if h is None]
            hit = [c for c, h in zip(r["when"], holds) if h is not None]
            if hit and miss:
                wishes.append({"rule": r["id"], "then": r["then"], "need": miss})
        return wishes

    # ---- 试假设攻坚（变更 #23，规格 §4：耗尽方法）----
    def hypothesize(self, missing_conds, workers=4):
        """缺条件不停在「还缺什么」——把缺失变量的各候选值逐一分支克隆推演。
        缺两个变量时追加组合分支（值对）——单补一个永远推不动的情形就靠它。
        候选值取自规则词表（所有规则条件里该变量出现过的值）。
        上限护栏：变量 ≤3、每变量值 ≤4、组合值对 ≤9、总分支 ≤12（防爆）。
        返回 [{assign: ((var,val),...), derived}]，derived 与 forward() 同构。"""
        vocab = {}
        for r in self.rules:
            for c in r["when"]:
                cv, op, val = _parse_cond(c)
                if op == "=" and isinstance(val, str):
                    vocab.setdefault(cv, set()).add(val)
        mvars = []
        for cond in missing_conds:
            var, op, _ = _parse_cond(cond)
            if op == "=" and var not in mvars:
                mvars.append(var)
            if len(mvars) >= 3:
                break
        jobs = []
        for var in mvars:
            for val in sorted(vocab.get(var, ()))[:4]:
                jobs.append(((var, val),))
        if len(mvars) == 2:   # 组合分支：双缺口单补一个推不动，值对才行
            from itertools import product
            for v1, v2 in product(sorted(vocab.get(mvars[0], ()))[:3],
                                  sorted(vocab.get(mvars[1], ()))[:3]):
                jobs.append(((mvars[0], v1), (mvars[1], v2)))
        jobs = jobs[:12]
        if not jobs:
            return []

        def _run(job):
            clone = Reasoner(self.rules)
            for v, f in self.facts.items():
                clone.assert_fact(v, f.value, f.conf, f.origin, f.domain)
            for var, val in job:
                clone.assert_fact(var, val, 1.0, origin="假设")
            return {"assign": job, "derived": clone.forward(workers=1)}

        if len(jobs) >= 3:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                return list(ex.map(_run, jobs))
        return [_run(j) for j in jobs]

    # ---- 输出（强制带置信标注）----
    def report(self, derived=None):
        derived = self.facts if derived is None else None
        lines = []
        for var, f in sorted(self.facts.items()):
            lines.append("{} = {}（置信 {:.0%}，来自{}）".format(
                var, f.value, f.conf, f.origin))
        return lines


# ──────────────────── 语言桥（骨架级，天花板=语言层）────────────────────

FACT_PAT = [
    re.compile(r"^无([一-龥]{2,6})$"),                       # 无X → X=无
    re.compile(r"([一-龥]{2,6}?)(极高|很高|高|中|低|不足)$"),  # 饥饿极高 → 饥饿=极高
    re.compile(r"([一-龥]{2,6}?)是([一-龥]{1,6})$"),           # 直接等值
    re.compile(r"([一-龥]{2,6}?)(平衡|稀缺|充足)$"),           # 受力平衡 → 受力=平衡
]
WHOLE_AS_IS = re.compile(r"^[一-龥]{2,8}(在场|可得)$")        # 友人在场 → 友人在场=是


def extract_facts(text):
    """从「饥饿极高 无捍卫亲友意念 友人在场」式短语抽事实。尽力而为，拆不出就算了。"""
    facts = []
    for seg in re.split(r"[，,、\s]+", text.strip()):
        if not seg or seg == "推演":
            continue
        m = WHOLE_AS_IS.match(seg)
        if m:
            facts.append((seg, "是"))
            continue
        for pat in FACT_PAT:
            m = pat.match(seg)
            if m:
                val = m.group(2) if pat is not FACT_PAT[0] else "无"
                facts.append((m.group(1), val))
                break
    return facts


def reason_about(text, rules=None):
    """会话桥：抽事实 → 正向推演 → 带置信输出。rules=None 用种子规则（规则入库后传知识库规则）。
    返回 (输出行, 规则链, 结构化结论derived)——derived 供预测存档（predict.py）。"""
    ry = Reasoner(rules)
    for var, val in extract_facts(text):
        ry.assert_fact(var, val)
    derived = ry.forward()
    lines = ["{} = {}（置信 {:.0%}，来自{}触发{}）".format(
        _parse_cond(d["then"])[0], _parse_cond(d["then"])[2], d["conf"],
        d["rule"], "，置信升级" if d.get("upgraded") else "") for d in derived]
    # 竞争假设并存展示（谁更可能，由置信说话，不静默取舍）
    for c in sorted(ry.competing, key=lambda x: -x["conf"]):
        lines.append("（竞争假设）{} = {}（置信 {:.0%}，来自{}）".format(
            c["var"], c["value"], c["conf"], c["rule"]))
    # 答不出的明确说需要观察什么（规格书：观察是合法动作）
    for w in ry.observe_wishlist():
        lines.append("（未知/需要观察）要推出「{}」，还缺：{}".format(
            w["then"], "、".join(w["need"])))
    return lines, ry.trace, derived


# ──────────────────── 问句桥：普通提问 → 正向推演（2026-09-24，批一）────────────────────

# 泛泛值：单独出现不算证据，必须变量名也在句中（防「充足睡眠」误触发「光照=充足」）
GENERIC_VALUES = {"是", "无", "有", "能", "不能", "不变", "改变", "进行", "充足",
                  "零", "平衡", "高", "中", "低", "极高", "不足", "减小", "增大",
                  "会", "可以"}
# 单字值禁用表：这些高频虚字即使出现在规则值里也不认（防「的/了/吗」之类误触发）；
# 内容性单字（铁/氧/水……）不在此列，出现在句中即认作事实
_STOP1 = set("的了吗呢吧啊在我你它他她什怎多几和与或把被让对向从到着过得于以及都也还就不没这那个种类上下里中内外前后时会能可要想说做看听吃走来去")


def _var_link(names, text):
    """变量名集合与问句的最长公共子串长度。≥2 视为这条规则与问题相关。"""
    best = 0
    for n in names:
        for i in range(len(n)):
            for j in range(i + 2, len(n) + 1):
                if n[i:j] in text:
                    best = max(best, j - i)
    return best


def _is_entity_val(v):
    """规则条件值是不是「实体」——铁/绿色植物/真空是实体；潮湿/充足（形容词性）、
    有/无（存在）、会/不能（模态）、高/中/低（程度）都不是。查词典与属性词表。"""
    from . import compose as _cmp, lexicon as _lex
    if not isinstance(v, str) or not v:
        return False
    if v in ("是", "有", "无", "成立"):
        return False
    if v in _cmp._ADJ_VALUES:
        return False
    gv = _lex.grammar_view()
    if v in gv["modals"] or v in gv["degrees"]:
        return False
    return True


def _concl_subject(d, by_id):
    """结论的主体词：结论变量形态分解的主体；拆不出退回规则条件里的实体值。"""
    from . import compose as _cmp
    var = _parse_cond(d["then"])[0]
    dec = _cmp._decompose(var)
    if dec.get("subj_word"):
        return dec["subj_word"]
    for c in by_id.get(d["rule"], {}).get("when", []):
        v = _parse_cond(c)[2]
        if _is_entity_val(v):
            return v
    return None


# ──────────────────── 反问桥：「需要什么/怎样才能」→ 反向推演（2026-09-24，批二）────────────────────

NEED_PAT = re.compile(r"(需要什么|需要哪些|怎样才|怎么才能|如何才能|怎么让|怎么能|如何能)")

# 阻止类意图：「怎么才能让铁不生锈 / 防止 / 避免」——用户要的是目标不发生（批二漏洞修复）
_PREVENT_PAT = re.compile(
    r"(防止|避免|不让|不使|抑制|消除|才能不|"
    r"怎么才能让.*不|怎么让.*不|怎样才能.*不|怎样.*不|如何.*不|怎么能.*不|怎么才能.*不)")

# 否定/终止类结论值：规则结论是这些值时，它描述的是「目标不发生」而非「目标达成」。
# 对「需要什么条件」式问题，这种规则只能逆否使用（要达成目标就得不让它的条件凑齐），
# 否则会把「燃烧需要什么」误答成「需要氧气不足」（2026-09-24 批二自查发现）。
_NEG_VALUE_WORDS = ("熄灭", "失败", "消失", "停止", "死亡", "减弱", "不能", "不会", "不可", "不行")


def _neg_value(v):
    if not isinstance(v, str):
        return False
    return v.startswith("不") or any(k in v for k in _NEG_VALUE_WORDS)


def _mine_facts(ry, text):
    """从句中认事实并断言进推演机：短语模式 + 规则值词匹配。返回 [{var, val, how}]。"""
    matched, seen = [], set()

    def _assert(var, val, how):
        if (var, val) in seen:
            return
        seen.add((var, val))
        ry.assert_fact(var, val, origin="问题陈述")
        matched.append({"var": var, "val": val, "how": how})

    for var, val in extract_facts(text):
        _assert(var, val, "短语")
    for r in ry.rules:
        for c in r["when"]:
            var, op, val = _parse_cond(c)
            if op != "=" or not isinstance(val, str):
                continue
            if len(val) >= 2 and val not in GENERIC_VALUES and val in text:
                _assert(var, val, "认出「{}」".format(val))
            elif (len(val) == 1 and val not in GENERIC_VALUES
                  and val not in _STOP1 and val in text):
                _assert(var, val, "认出「{}」".format(val))
            elif val in GENERIC_VALUES and len(var) >= 2 and var in text and val in text:
                _assert(var, val, "认出「{}{}」".format(var, val))
    # 变更 #29：介词短语事实——「碰到水 / 在空气中 / 放在水里」→ 环境接触事实。
    # 它们未必直接点火规则（规则要的是「环境=潮湿」这类状态），但进了事实清单，
    # 上层就能拿它们向你确认「这算不算潮湿」，而不是假装没看见。
    # 边界修正：接触类触发词的对象边界由「下一个介词/方位尾/非汉字/句尾」划定
    # （「碰到水在空气中」能切出 水+空气 两个事实，不会吞成「水在空」）；
    # 「在X里/中/内」式边界由方位尾字决定，尾部不做额外限制（「在空气中长时间」也认）。
    for m in re.finditer(r"(?:碰到|接触|放在|放进|泡在|淋到|淋了|置于)"
                         r"([一-龥]{1,4}?)(?=里|中|内|在|$|[^一-龥])", text):
        _assert("环境接触", m.group(1), "介词短语")
    for m in re.finditer(r"在([一-龥]{1,4}?)(?:里|中|内)", text):
        _assert("环境接触", m.group(1), "介词短语")
    return matched


def answer_need(text, rules=None, force=False, negated=None):
    """「X 需要什么 / 怎样才能 X」式问题：锁定目标变量 → 反向找路径 → 列出所需条件。
    先认出句中事实：已满足的条件不再列为「还缺」；条件齐全的路径直接正向推出结论。
    force=True（组合式解析判为条件疑问）时跳过 NEED_PAT 名单自检；
    negated（来自 parse 的分句否定标记）优先于正则判断阻止意图。
    返回 None = 不认得目标（交给下一种方法）；
    返回 dict: {goals, paths, avoid, prevent, tiers, facts, conclusions}。"""
    if not force and not NEED_PAT.search(text):
        return None
    ry = Reasoner(rules)
    matched = _mine_facts(ry, text)
    # 目标变量：规则的结论变量名与问句有公共子串（≥2 字），或结论值词直接出现在句中
    goals = []
    for r in ry.rules:
        tv, _, tval = _parse_cond(r["then"])
        if _var_link([tv], text) >= 2 or (isinstance(tval, str) and len(tval) >= 2 and tval in text):
            if tv not in goals:
                goals.append(tv)
    if not goals:
        return None
    paths = []
    for g in goals:
        for p in ry.backward(g):
            p["goal"] = g
            paths.append(p)
    # 只留直接产出目标的规则路径，同目标按置信降序
    paths = [p for p in paths if p["direct"]]
    # 极性分流：把「产出目标达成」和「产出目标反面」的规则分开。
    # 用户想达成（默认）：正面规则=达成路径；反面规则（结论=熄灭/不会…）=逆否路径，
    #   含义反过来读：要达成目标就不能让它的条件凑齐。
    # 用户想阻止（防止/避免/怎么才能不）：正负互换。
    by_id = {r["id"]: r for r in ry.rules}
    # 否定信息优先用组合式解析的分句结果（「怎么才能不…」），没有才退回正则
    prevent = bool(negated) if negated is not None else bool(_PREVENT_PAT.search(text))
    positive, avoid = [], []
    for p in paths:
        p["then"] = by_id[p["rule"]]["then"]
        if _neg_value(_parse_cond(p["then"])[2]) == prevent:
            positive.append(p)
        else:
            avoid.append(p)
    positive.sort(key=lambda p: -p["conf"])
    avoid.sort(key=lambda p: -p["conf"])
    # 条件齐全 → 正向传播直接给结论（规格：拆得开就并行，条件够就算出来）
    conclusions = ry.forward() if any(not p["missing"] for p in positive) else []
    tiers = {r["id"]: r["tier"] for r in ry.rules}
    return {"goals": goals, "paths": positive, "avoid": avoid, "prevent": prevent,
            "tiers": tiers, "facts": matched, "conclusions": conclusions}


def hypothesize_branches(rules, facts, missing_conds):
    """试假设攻坚的会话桥（变更 #23）：重建推演机 → 逐分支试算。
    返回 (branches, by_id)；branches 元素 {var, val, derived}，by_id 供组句查规则条件。"""
    ry = Reasoner(rules)
    for f in facts or []:
        ry.assert_fact(f["var"], f["val"], origin="问题陈述")
    branches = ry.hypothesize(missing_conds)
    return branches, {r["id"]: r for r in ry.rules}


# ──────────────────── 类比攻坚（变更 #25）────────────────────

def analogize(text, rules, unknown_words):
    """陌生实体 + 认得的目标 → 类比迁移假设（明说只是类比，产物是待验证假设不是结论）。

    「铜会生锈吗」：铜不认识（分词标未知），但生锈有规则 G18（金属=铁；环境=潮湿）。
    把条件里的实体值换成陌生词：假如铜和铁同类，规律搬过来就是……
    返回 [{goal, rule, then, when, sub_var, old, new, tier, domain}]，上限 3 条。"""
    # 陌生实体过滤：语法词典里的功能词（疑问/模态/否定/副词……）不是实体——
    # 分词器只查基础词典，这些功能词常被误标「未知」，代进去就闹笑话
    from . import lexicon as _lex
    _grammar_words = set(_lex.grammar_view()["entries"])
    subs = [w for w in (unknown_words or [])
            if 1 <= len(w) <= 4 and w not in GENERIC_VALUES and w not in _STOP1
            and w not in _grammar_words]
    if not subs:
        return []
    out = []
    for r in rules or []:
        tv, _, tval = _parse_cond(r["then"])
        linked = _var_link([tv], text) >= 2 or \
            (isinstance(tval, str) and len(tval) >= 2 and tval in text)
        if not linked:
            continue
        for c in r["when"]:
            cv, op, val = _parse_cond(c)
            if op != "=" or not isinstance(val, str) or len(val) > 6:
                continue
            for w in subs:
                if w != val and w not in tv:
                    out.append({"goal": tv, "rule": r["id"], "then": r["then"],
                                "when": list(r["when"]), "sub_var": cv,
                                "old": val, "new": w,
                                "tier": r["tier"], "domain": r["domain"]})
    seen, res = set(), []
    for o in out:
        k = (o["rule"], o["new"])
        if k in seen:
            continue
        seen.add(k)
        res.append(o)
        if len(res) >= 3:
            break
    return res


# ──────────────────── 结构类比（变更 #28，规格「关系结构也是武器」）────────────────────

def _asked_value_class(text):
    """问题所问值的情态归类：会不会→modal；有没有→exist；高不高→degree。
    全查词典（模态/程度词表），代码无硬编码词表。认不出 → None（结构类比没锚点）。"""
    from . import compose as _cmp
    for w in _cmp._GV["modals"]:
        if w in text:
            return "modal"
    if "有没有" in text or "是否有" in text:
        return "exist"
    for w in ("高", "低", "大", "小", "强", "弱", "快", "慢"):
        if w + "吗" in text or "多" + w in text:
            return "degree"
    return None


def structural_analogize(text, rules, unknown_words):
    """结构类比：目标本身没规则时，跨域搬关系结构（单规则移植，链式搬家留后期）。

    签名同构：问题所问值的归类 == 规则结论值的归类（modal/exist/degree）。
    锚点：主体 = 话里出现的规则词表词（变量名或条件值，单字实体也算——铁/氧/水是实体）；
    目标 = 问句里的谓词（从原文抽：剥掉主体、语法词、标点后的最长残段——
    不依赖分词，生僻谓词在原文里本是连着的）。返回上限 2；锚点缺一 → []。"""
    import re as _re
    from . import compose as _cmp
    from . import lexicon as _lex
    asked = _asked_value_class(text)
    if not asked:
        return []
    _gv = _lex.grammar_view()
    _grammar_words = set(_gv["entries"])
    # 主体锚点：规则词表里出现在话里的词（排除泛泛值；单字实体也算——铁/氧/水是实体，
    # 它们若登记在词典里也是名词，名词不当虚词杀）
    anchors = set()
    for r in rules or []:
        for c in list(r.get("when", [])) + [r.get("then", "")]:
            v, _, val = _parse_cond(c)
            for w in (v, val):
                if (isinstance(w, str) and len(w) >= 2 and w in text
                        and w not in GENERIC_VALUES):
                    anchors.add(w)
                elif (isinstance(w, str) and len(w) == 1 and w in text
                      and w not in GENERIC_VALUES and w not in _STOP1):
                    ge = _gv["entries"].get(w)
                    if not ge or ge.get("词性") == "名词":
                        anchors.add(w)
    if not anchors:
        return []
    subj = sorted(anchors, key=len)[-1]   # 最长优先（绿色植物 > 植物）
    # 主体的家乡域（提过它的规则所在的域）——来源规则同域优先
    subj_domains = {r["domain"] for r in (rules or [])
                    if any(subj in c for c in list(r.get("when", [])) + [r.get("then", "")])}
    # 目标谓词：剥主体 → 剥语法词（长词优先）→ 剥单字虚词 → 最长残段
    # 残骸字随主体一并剥（变更 #29 谓词保真加强）：分词器把生词切成「铁+器」时，
    # 锚点「铁」剥掉后残字「器」会拼进谓词造出「器水」这种胡话——主体后紧跟的
    # 不识字（全量词典不在册）多半是词残骸，随主体一并剥；识的字不剥
    # （碰到水的「水」在册，不冤杀）。上限 2 字防失控。
    idx = text.find(subj)
    victim = subj
    _all = _lex.load_all()
    if idx >= 0:
        pos, extra = idx + len(subj), 0
        while pos < len(text) and extra < 2:
            ch = text[pos]
            # 停剥线：已识字，或虽未识字但它是某个在册词的词头（「碰到」的「碰」
            # 是词头不是残骸——铁器「碰」到水：剥器不剥碰）
            if (not ("一" <= ch <= "龥") or ch in _all
                    or text[pos:pos + 2] in _all):
                break
            victim += ch
            pos += 1
            extra += 1
    span = text.replace(victim, " ", 1)
    for w in sorted(_grammar_words, key=len, reverse=True):
        if len(w) >= 2 and w in span:
            span = span.replace(w, " ")
    span = _re.sub(r"[，。；！？,!?;．、\s]", "", span)
    for ch in "会不会能可能没不的吗么呢吧啊呀嘛哦了着过":
        span = span.replace(ch, "")
    goal = span if 2 <= len(span) <= 6 else ""
    # 变更 #29 谓词剥取保真：残段里还卡着虚字（是/才/就/都……）或嵌着另一个锚点词，
    # 说明剥取失败——弃权不出声，宁可没招也不输出「铁是加上水才」这种胡话。
    if goal and any(ch in goal for ch in "是才就都还但又倒也"):
        goal = ""
    if goal and any(a != subj and a in goal for a in anchors):
        goal = ""
    # 残段形态检查：多词残段的首词是动词/介词（「碰到水」是条件碎片）或根本
    # 不在册（「器水」式散字拼接）→ 不是属性谓词，弃权。单词残段不管——
    # 「感冒」这种生僻谓词本就是结构类比存在的意义。
    if goal:
        from . import segment as _seg
        _gtoks = _seg.segment(goal, _all)
        if len(_gtoks) >= 2:
            _e0 = _all.get(_gtoks[0][1])
            if _e0 is None or _e0.get("词性") in ("动词", "介词"):
                goal = ""
    if not goal:
        return []
    out = []
    for r in sorted(rules or [],
                    key=lambda r: (r["domain"] not in subj_domains, r["id"])):
        tv, _, tval = _parse_cond(r["then"])
        if _cmp._value_class(tval) != asked:
            continue
        if tv == goal:   # 目标本身就是这条规则的结论变量——那不叫类比，是正路（不该到这）
            continue
        # 条件里的实体槽：具体实体值里最短的那个（铁 优先于 潮湿——状态词往往更长）；
        # 没有就不换（纯状态规则照搬结构）
        ent_var, ent_val = None, None
        cands = []
        for c in r["when"]:
            cv, op, val = _parse_cond(c)
            if (op == "=" and isinstance(val, str) and val.isalpha()
                    and val not in GENERIC_VALUES):
                cands.append((cv, val))
        if cands:
            ent_var, ent_val = sorted(cands, key=lambda x: len(x[1]))[0]
        out.append({"rule": r["id"], "domain": r["domain"], "when": list(r["when"]),
                    "then": r["then"], "subj_old": ent_val, "subj_var": ent_var,
                    "subj_new": subj, "goal_var": tv, "goal_word": goal, "val": tval})
        if len(out) >= 2:
            break
    return out


def answer_question(text, rules=None):
    """把普通问句桥进推演层：认出事实 → 正向传播 → 按与问题的相关性筛结论。

    返回 None = 没认出任何事实（交给下一种方法：检索救援）。
    返回 dict:
      facts       认出的事实 [{var, val, how}]
      conclusions 结论 [{rule, then, conf, chained}]（chained=True 是沿规则链进一步推出的）
      competing   竞争假设并存列表
      wishes      观察清单（部分条件命中，列出还缺什么）
      derived     全部结构化结论（预测存档原料）
      chain       触发规则链（留痕）
    """
    if NEED_PAT.search(text):
        return None   # 「需要什么/怎样才能」是反问桥 answer_need 的活，正向桥不接
    ry = Reasoner(rules)
    by_id = {r["id"]: r for r in ry.rules}
    matched = _mine_facts(ry, text)

    if not matched:
        return None
    derived = ry.forward()
    wishes = ry.observe_wishlist()
    if not derived:
        # 认出了事实但推不出结论：有观察清单才值得说话，否则交给检索
        if wishes:
            return {"facts": matched, "conclusions": [], "competing": [],
                    "wishes": wishes, "derived": [], "chain": ry.trace}
        return None

    # 3) 相关性筛选：多个不同结论变量时，保留与问题有公共子串（≥2 字）的规则结论；
    #    链条结论（其条件是被保留规则的结论变量）随链保留——这样
    #    「绿色植物光照充足会怎样」能带出「光合作用→释放氧气」的完整链，
    #    而「真空能传声吗」不会捎带「光传播=可以」的无关结论。
    def _then_var(d):
        return _parse_cond(d["then"])[0]

    conclusions = derived
    if len({_then_var(d) for d in derived}) > 1:
        direct = [d for d in derived
                  if _var_link([_then_var(d)] + [_parse_cond(c)[0] for c in
                                               by_id[d["rule"]]["when"]], text) >= 2]
        if direct:
            kept_rules = {d["rule"] for d in direct}
            kept_vars = {_then_var(d) for d in direct}
            chained = []
            changed = True
            rest = [d for d in derived if d not in direct]
            while changed:
                changed = False
                for d in list(rest):
                    rvars = {_parse_cond(c)[0] for c in by_id[d["rule"]]["when"]}
                    if rvars & kept_vars:
                        rest.remove(d)
                        chained.append(dict(d, chained=True))
                        kept_vars.add(_then_var(d))
                        kept_rules.add(d["rule"])
                        changed = True
            conclusions = direct + chained

    # 变更 #29 主体锚定否决：单一主体问题里（事实只认出一个实体值，如「铁」），
    # 结论的主体若是另一个实体、且原文压根没提过它（如「人」），就是答非所问的
    # 捎带噪音——「问铁器附送人不能生存」那种。链式结论豁免（链条关系本身就是相关性）。
    ents = {f["val"] for f in matched if _is_entity_val(f["val"])}
    if len(ents) == 1 and conclusions:
        subj = next(iter(ents))
        kept = []
        for d in conclusions:
            if d.get("chained"):
                kept.append(d)
                continue
            s = _concl_subject(d, by_id)
            if s and s != subj and s not in ents and not any(ch in text for ch in s):
                continue   # 跨实体捎带，砍掉
            kept.append(d)
        conclusions = kept

    competing = [c for c in ry.competing
                 if _var_link([c["var"]], text) >= 2] or list(ry.competing)
    return {"facts": matched, "conclusions": conclusions, "competing": competing,
            "wishes": wishes, "derived": derived, "chain": ry.trace,
            "by_id": {k: {"tier": v["tier"], "domain": v["domain"], "when": list(v["when"])}
                      for k, v in by_id.items()}}


def demo():
    """设计者原话示例：饿到极限的野兽。"""
    print("══ 推演演示（你的原话示例：饿到极限的野兽）══")
    ry = Reasoner()
    ry.assert_fact("饥饿", "极高")
    ry.assert_fact("自制力", "不足")
    ry.assert_fact("捍卫亲友意念", "无")
    ry.assert_fact("友人在场", "是")
    derived = ry.forward()
    print("已知事实：饥饿=极高，自制力=不足，捍卫亲友意念=无，友人在场=是")
    print("\n正向推演结论：")
    for d in derived:
        print("  [{}] {}（置信 {:.0%}）".format(d["rule"], d["then"], d["conf"]))
    print("\n反向查路径：目标「威胁亲人」从哪来？")
    for p in ry.backward("威胁亲人"):
        print("  [{}] 置信{:.0%} 需要{} 缺{}".format(
            p["rule"], p["conf"], " ∧ ".join(p["when"]),
            "、".join(p["missing"]) or "（不缺）"))
    print("\n逻辑域演示：受力=平衡 → 加速度？")
    ry2 = Reasoner()
    ry2.assert_fact("受力", "平衡")
    for d in ry2.forward():
        print("  [{}] {}（置信 {:.0%}）".format(d["rule"], d["then"], d["conf"]))
    print("（确定性结论：条件确定 → 置信 100%）")
    print("\n对账演示：R2 预测命中 → 升档")
    print("  R2 强度：", ry.reconcile("R2", True))
    print("\n语言桥演示：「饥饿极高 无捍卫亲友意念 友人在场」")
    concl, trace, _ = reason_about("推演 饥饿极高 无捍卫亲友意念 友人在场")
    for c in concl:
        print("  ", c)
    ok, n = stats.verify_log()
    print("\nweights.log：{} 条，链{}".format(n, "完整" if ok else "【断裂】"))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        concl, trace, _ = reason_about(" ".join(sys.argv[1:]))
        print("触发规则：", trace)
        for c in concl:
            print(c)
        if not concl:
            print("没有可推演的内容。试试：饥饿极高 无捍卫亲友意念 友人在场")
    else:
        demo()
