# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【逐字】组句：逻辑推演出语句，让它自己会组句，不是背模板对剧本（变更 #22 拍板，规格 §1）
# 【施工方】类型级装配器实现；装配弃权守卫（变更 #30）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""组句器（2026-09-25 变更 #22：谓语层真组合——类型级通用装配，无单词专条）。

规格书 §1 变更 #22：动词在词典里只登记「情态类型 + 论元结构」，
句子由"语义→句法"的类型级通用规则装配，不为任何单词写句式。
组句 pipeline：
  1. 变量名形态分解（_decompose）：剥论元/属性后缀 → 词典查拆 主体词/谓词/客体
  2. 值词归类（_value_class）：模态/变化/程度/存在/名词——全部查词典
  3. 情态类型装配（_assemble）：环绕/能力/变化/存在/程度/进行/转变/属性 八条通用规则
  4. 主语裁决：变量自带主体词 > 条件主角（须是实体）> 泛指「它」
词典里没有的搭配 → 通用组装「变量是值」兜底。
组合性验证方式：词典加一个新动词（只标情态类型），句子自动长出，无人替它写句式。
"""
import re

from . import lexicon

_GV = lexicon.grammar_view()
_DUMMY_MARKERS = ("状态", "现象")          # 虚义属性后缀：装配时剥掉不占位
_DEG_ADV = {"高": "很可能", "中": "可能", "低": "小概率", "零": "不会"}
_OPERATOR_VAL = re.compile(r"大于|小于|等于|^\d|摄氏度")


# ──────────────────── 1. 变量名形态分解 ────────────────────

def _decompose(var):
    """把关系词拆成语义成分，全部靠词典，没有为具体词写的分支。
    返回 {subj_word 主体词, verb 谓词, obj_word 客体, marker 属性/论元后缀,
          noun 整体名词, passive 被动短语, turn 转变拆分}。"""
    d = {"subj_word": None, "verb": None, "obj_word": None,
         "marker": None, "noun": None, "passive": False, "turn": None}
    if var.startswith("被"):
        d["passive"] = True                      # 被动短语整体作谓词（被2整除）
        return d
    core, marker = var, None
    for suf in sorted(_GV["suffixes"], key=len, reverse=True):
        if var.endswith(suf) and len(var) > len(suf):
            core, marker = var[:-len(suf)], suf  # 绕转对象→绕转+对象
            break
    d["marker"] = marker
    # 转变类拆分：主体词 + 转变动词 + 客体（目标|转为|友人）
    for tv, e in _GV["verbs"].items():
        if e.get("情态类型") == "转变" and tv in core:
            head, _, tail = core.partition(tv)
            if head in _GV["nouns"] and tail in _GV["nouns"]:
                d.update(subj_word=head, turn=(tv, tail))
                return d
    # 动词后缀拆分：主体词+谓词（声音+传播）或 客体+谓词（氧气+释放）
    for verb in sorted(_GV["verbs"], key=len, reverse=True):
        if core.endswith(verb) and len(core) > len(verb):
            head = core[:-len(verb)]
            if head in _GV["nouns"]:
                d["verb"] = verb
                if _GV["nouns"][head].get("论元角色") == "客体":
                    d["obj_word"] = head
                else:
                    d["subj_word"] = head
                return d
    # 动词前缀拆分：谓词+客体（威胁+亲人）
    for verb in sorted(_GV["verbs"], key=len, reverse=True):
        if core.startswith(verb) and len(core) > len(verb):
            tail = core[len(verb):]
            if tail in _GV["nouns"]:
                d["verb"], d["obj_word"] = verb, tail
                return d
    # 整体查词典
    if core in _GV["verbs"]:
        d["verb"] = core
    elif core in _GV["nouns"]:
        if _GV["nouns"][core].get("论元角色") == "主体":
            d["subj_word"] = core
        d["noun"] = core
    return d


# ──────────────────── 2. 值词归类（查词典）────────────────────

def _value_class(val):
    if val in _GV["modals"]:
        return "modal"
    if val in ("有", "无"):
        return "exist"
    if val in _GV["degrees"]:
        return "degree"
    e = _GV["entries"].get(val)
    if e and e.get("词性") == "动词":
        return "change" if e.get("情态类型") == "变化" else "verb"
    return "noun"


# ──────────────────── 3. 主语裁决 ────────────────────

def _valid_entity(word):
    """条件主角能当主语的前提：是个实体，不是程度/模态/比较运算值。"""
    if not word or word in _GV["modals"] or word in _GV["degrees"]:
        return False
    if word in ("是", "有", "无", "成立"):
        return False
    return not _OPERATOR_VAL.search(word)


def _subject_of(rule_when, facts):
    """条件主角 = 规则条件里被问句认出的事实值。"""
    fact_vals = {f.get("val") for f in facts or []}
    for c in rule_when or []:
        m = re.match(r"^([^=<>=!]+)=(.+)$", c)
        if m and m.group(2) in fact_vals:
            return m.group(2)
    return None


def _pick_subject(dec, cond_subj):
    if dec.get("subj_word"):
        return dec["subj_word"]
    if _valid_entity(cond_subj):
        return cond_subj
    return None


# ──────────────────── 4. 情态类型装配（八条通用规则）────────────────────

def _assemble(var, val, cond_subj=None):
    """语义→句法的类型级映射。规则属于情态类型，不属于单词。
    变更 #30 条文⑤ 装配弃权：变量名拆不出任何登记成分、自身也不在词典，
    且值撑不起句子（模态/程度/存在类虚词值，或名词值也未登记）时——
    返回 None 弃权，宁可说「说不好」也不胡拼「它会星芒闪耀」。"""
    dec = _decompose(var)
    vc = _value_class(val)
    if (not any(dec.values()) and var not in _GV["entries"]
            and (vc in ("modal", "degree", "exist")
                 or (vc == "noun" and val not in _GV["entries"]))):
        return None
    subj = _pick_subject(dec, cond_subj)
    s = subj or "它"

    # 转变类：目标+转为+友人 → 「目标可能转为友人」
    if dec["turn"]:
        tv, obj = dec["turn"]
        adv = _DEG_ADV.get(_GV["degrees"].get(val), "")
        return "{}{}{}{}".format(dec["subj_word"], adv, tv, obj)

    # 环绕类：绕转对象=太阳 → 「主体绕着对象转」
    if dec["marker"] in _GV["argument_markers"] and dec["verb"] and \
            _GV["verbs"][dec["verb"]].get("情态类型") == "环绕":
        return "{}绕着{}转".format(s, val)

    # 能力类：值是模态词 → 「主体+模态+动作」
    if vc == "modal":
        if dec["passive"]:
            return "{}{}{}".format(s, val, var)          # 偶数能被2整除
        if dec["verb"]:
            return "{}{}{}".format(s, val, dec["verb"])  # 铁会生锈/声音不能传播
        return "{}{}{}".format(s, val, var)

    # 进行类：活动名词 + 进行 → 「主体会进行某活动」
    if vc == "verb" and val == "进行" and dec.get("noun"):
        return "{}会进行{}".format(s, dec["noun"])       # 绿色植物会进行光合作用

    # 变化类：值是变化动词 → 「主体+会+变化」
    if vc == "change":
        if dec["marker"] and dec["marker"] not in _DUMMY_MARKERS and dec["subj_word"]:
            return "{}的{}会{}".format(dec["subj_word"], dec["marker"], val)
        if dec["subj_word"]:
            return "{}会{}".format(dec["subj_word"], val)  # 水会沸腾/电流会减小
        if dec["verb"]:
            return "{}会{}".format(dec["verb"], val)       # 燃烧会熄灭（事件作主语）
        return "{}会{}".format(s, val)                     # 它会下沉/变浑浊

    # 存在类：客体+动词 倒置 → 「（主体）会+动词+客体」
    if vc == "exist":
        if dec["verb"] and dec["obj_word"]:
            neg = "" if val == "有" else "不"
            return "{}{}会{}{}".format(subj or "", neg, dec["verb"], dec["obj_word"])
        return "{}{}".format(s, "有" if val == "有" else "没有") + var

    # 程度类：事件短语 → 「主体+可能性副词+动宾」；属性名词 → 「名词+很+程度」
    if vc == "degree":
        if dec["verb"] and dec["obj_word"]:
            adv = _DEG_ADV.get(_GV["degrees"][val], "")
            return "{}{}{}{}".format(s, adv, dec["verb"], dec["obj_word"])
        noun = dec.get("noun")
        if noun and _GV["nouns"][noun].get("论元角色") == "属性":
            return "{}是零".format(noun) if val == "零" else "{}很{}".format(noun, val)
        return "{}的{}很{}".format(s, var, val)            # 它的觅食行为很激烈

    # 属性值类（默认）：「主体的属性是值」/「属性是值」
    noun = dec.get("noun") or var
    if subj and not dec["subj_word"]:
        return "{}的{}是{}".format(subj, noun, val)        # 三角形的内角和是180度
    return "{}是{}".format(noun, val)


# ──────────────────── 对外组句接口 ────────────────────

def _parse_then(then):
    m = re.match(r"^([^=]+)=(.+)$", then or "")
    return (m.group(1), m.group(2)) if m else (then, "")


def _conf_tone(conf):
    """置信 → 短标注（变更 #29：四句罐头尾注废止；确定档直陈，不挂尾注）。"""
    if conf >= 0.999:
        return ""
    if conf >= 0.8:
        return "九成把握"
    if conf >= 0.5:
        return "六成把握，还得验证"
    return "不太确定，只是推测"


def compose_conclusion(concl, rule_when=None, facts=None):
    """把一条推演结论组装成句子。
    concl: {then, conf, rule, chained?}；rule_when: 规则条件（找主语用）；
    facts: 本轮认出的事实。返回句子字符串。
    变更 #29：不再「X——这是确定无疑的（来自规则 G18）」整句模板；
    确定档直陈「铁会生锈（G18）」，非确定档挂短标注「（G18，九成把握）」。"""
    var, val = _parse_then(concl.get("then", ""))
    core = _assemble(var, val, _subject_of(rule_when, facts))
    if core is None:
        return None   # 装配弃权（变更 #30）：交由会话层如实说「说不好」并报生词
    if concl.get("chained"):
        core = "进一步说，" + core
    tone = _conf_tone(concl.get("conf", 0))
    rule = concl.get("rule", "?")
    return "{}（{}，{}）".format(core, rule, tone) if tone else "{}（{}）".format(core, rule)


def compose_conditions(goal, when, missing, conf, rule):
    """反问桥正面路径组句：要达成目标需要这些条件，还缺哪些。"""
    conds = "、".join(_humanize_cond(c) for c in when)
    s = "要达成「{}」，需要{}".format(goal, conds)
    if missing:
        s += "；其中{}还不知道——告诉我就能接着推".format(
            "、".join(_humanize_cond(c) for c in missing))
    tone = _conf_tone(conf)
    s += "（{}，{}）".format(rule, tone) if tone else "（{}）".format(rule)
    return s


def compose_avoid(goal, when, then, conf, rule, held):
    """逆否路径组句：规则说的是目标的反面，所以要别让条件凑齐。"""
    conds = "、".join(_humanize_cond(c) for c in when)
    var, val = _parse_then(then)
    bad = _assemble(var, val)
    s = "规则 {} 说：当{}时，{}。所以反过来——别让这些条件凑齐，拆掉任意一条就行".format(
        rule, conds, bad)
    if held:
        s += "；其中{}已经是事实，关键是别让剩下的出现".format(
            "、".join(_humanize_cond(c) for c in held))
    tone = _conf_tone(conf)
    return s + ("（{}）".format(tone) if tone else "")


def _humanize_cond(cond):
    """「环境=潮湿」→「环境潮湿」；「金属=铁」→「金属是铁」。组装而非模板。
    形容词直接谓语化（潮湿/充足……），名词要系词「是」——汉语谓语规则。"""
    m = re.match(r"^([^=<>=!]+)(=|<|<=|>|>=)(.+)$", cond)
    if not m:
        return cond
    var, op, val = m.groups()
    if op == "=":
        if val in ("是", "有", "无"):
            return var + {"是": "成立", "有": "存在", "无": "缺失"}[val]
        if val in _ADJ_VALUES:
            return var + val
        return var + "是" + val
    return "{}{}{}".format(var, op, val)


# 形容词性值词表（语法知识，可扩充）：这些值直接作谓语，不需要「是」
_ADJ_VALUES = {"潮湿", "干燥", "充足", "不足", "平衡", "均匀", "稳定", "剧烈",
               "激烈", "缓和", "极高"}
