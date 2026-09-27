# -*- coding: utf-8 -*-
"""变更 #29 专项验证：语言理解去模板化（用户 2026-09-25 20:08 举报的五类硬伤）。

分组：
  0. 介词短语正则边界（修复验证：「碰到水在空气中」切出 水+空气，不吞「水在空」）
  1. 分词：连续未登录字符合并、标点不报生词、登录词零误报
  2. 拆句：challenge 识别（不是…吗）；「铁不会生锈吗」不误伤（只有「不」没有「不是」）
  3. 自我认知三句（你是什么／你的名字是什么／你会什么 → 自我通路，不落检索模板）
  4. 纠正通路（「铁生锈的条件不是氧气加上水才能生锈吗」→ mock client 验 propose：
     claimed 含氧气、水；不含是/才/条件；target=G18——covered 只算被对比那条）
  5. 主体锚定相关性否决（reason 层三例基线：铁器句砍 G27 人生存／真空句保 G10／
     绿色植物保 G25+G26 链式豁免）
  6. 谓词保真（structural_analogize：challenge 句→[]；「铁器碰到水会怎样」残骸字
     「器」随主体剥→[]；「铁会感冒吗」不回归→非空）
  7. 介词短语事实进 facts（answer_question 认出 环境接触=水/空气）
  8. confirm_cond 三分支（_try_pending 单元：「算」补条件重推／「不算」诚实退出／
     答非所问不劫持）
  9. handle 集成：「铁器碰到水会怎样」不出「器水」胡话，走分支试算合理回复

自清理：session.json / suggestions.json / req_throttle.json / predictions.json
先备份后恢复；refine.auto_submit_reqs 打桩不外泄 REQ；Session 用 dummy key 构造
（catalog 401 → 种子兜底，再覆盖为夹具规则），全程离线不消耗服务限流。
"""
import re
import sys
from pathlib import Path

AI_DIR = Path(r"D:\cangku\AI")
KB_DIR = Path(r"D:\cangku\知识库")
for p in (str(AI_DIR), str(KB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from brain import (compose, lexicon, parse, reason, refine, segment,  # noqa: E401
                   session as sess_mod, state as state_mod)

STATE = state_mod.SESSION_FILE.parent
FILES = [state_mod.SESSION_FILE, STATE / "suggestions.json",
         STATE / "req_throttle.json", STATE / "predictions.json"]

passed, failed = [], []


def ck(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  [{}] {}{}".format("✓" if cond else "✗", name,
                               (" — " + str(detail)[:110]) if (detail and not cond) else ""))


def backup(p):
    return p.read_bytes() if p.exists() else None


def restore(p, data):
    if data is None:
        if p.exists():
            p.unlink()
    else:
        p.write_bytes(data)


# 夹具规则：照抄知识库信任区 G 类条目 usage 字段（变更 #29 测试与库内容解耦）
# G17 故意排在 G18 前（模拟 KB 目录序）——纠正通路必须按相关度排序选 G18，
# 不能谁排前选谁（活体复测抓包：选错规则会把「氧气」误判成已覆盖）
FIX = [
    {"id": "G17", "when": ["反应=燃烧", "氧气=不足"], "then": "燃烧状态=熄灭",
     "tier": "高", "domain": "化学", "source": "夹具"},
    {"id": "G10", "when": ["传声介质=真空"], "then": "声音传播=不能",
     "tier": "高", "domain": "物理", "source": "夹具"},
    {"id": "G18", "when": ["金属=铁", "环境=潮湿"], "then": "生锈=会",
     "tier": "高", "domain": "化学", "source": "夹具"},
    {"id": "G25", "when": ["植物=绿色植物", "光照=充足"], "then": "光合作用=进行",
     "tier": "高", "domain": "生物", "source": "夹具"},
    {"id": "G26", "when": ["光合作用=进行"], "then": "氧气释放=有",
     "tier": "高", "domain": "生物", "source": "夹具"},
    {"id": "G27", "when": ["氧气=无"], "then": "人生存=不能",
     "tier": "高", "domain": "生物", "source": "夹具"},
]


class MockClient:
    """离线知识库桩：目录空（检索救援必落空），propose 记录调用。"""

    def __init__(self):
        self.proposals = []

    def catalog(self):
        return []

    def my_requests(self):
        return []

    def propose(self, action, target=None, proposal="", reason="", proof=""):
        self.proposals.append(dict(action=action, target=target,
                                   proposal=proposal, reason=reason, proof=proof))
        return "REQ_T1"


def make_session():
    """离线 Session：dummy key 让 catalog 401 → 种子兜底，再覆盖夹具规则与词典。"""
    sess = sess_mod.Session(api_key="dummy-offline-b29")
    sess.rules = [dict(r) for r in FIX]
    sess._lex = lexicon.load_all()
    sess._lex_update_from_knowledge()
    mock = MockClient()
    sess.kb_client = mock
    return sess, mock


_bk = {p: backup(p) for p in FILES}
_orig_auto = refine.auto_submit_reqs
refine.auto_submit_reqs = lambda fresh, sess: []   # 打桩：测试不外泄 REQ
try:
    # ══ 0. 介词短语正则边界 ══
    print("══ 0. 介词短语正则边界 ══")
    PAT1 = re.compile(r"(?:碰到|接触|放在|放进|泡在|淋到|淋了|置于)"
                      r"([一-龥]{1,4}?)(?=里|中|内|在|$|[^一-龥])")
    PAT2 = re.compile(r"在([一-龥]{1,4}?)(?:里|中|内)")

    def contacts(t):
        return ([m.group(1) for m in PAT1.finditer(t)]
                + [m.group(1) for m in PAT2.finditer(t)])

    c = contacts("铁器碰到水在空气中长时间这样会如何")
    ck("0.1 碰到水在空气中 → 水+空气", c == ["水", "空气"], c)
    ck("0.2 不吞「水在空」", "水在空" not in c)
    ck("0.3 放在水里 → 水", set(contacts("放在水里")) == {"水"}, contacts("放在水里"))
    ck("0.4 淋了雨 → 雨", contacts("淋了雨") == ["雨"], contacts("淋了雨"))
    ck("0.5 无介词句不误认", contacts("环境潮湿但是无氧气铁器放里面会怎样") == [])

    # ══ 1. 分词 ══
    print("══ 1. 分词：未登录合并 / 标点不生词 ══")
    sess, _ = make_session()
    toks = segment.segment("碰到水在空气中", sess._lex)
    unk = [w for k, w in toks if k == "未知"]
    ck("1.1 碰到水在空气中 零生词", unk == [], unk)
    toks = segment.segment("铁会生锈吗？", sess._lex)
    unk = [w for k, w in toks if k == "未知"]
    ck("1.2 标点「？」不报生词", unk == [], unk)
    toks = segment.segment("钅羊会生锈吗", sess._lex)
    unk = [w for k, w in toks if k == "未知"]
    ck("1.3 连续未登录字符合并成一段", unk == ["钅羊"], unk)

    # ══ 2. 拆句：challenge 识别与不误伤 ══
    print("══ 2. 拆句：纠正/反问识别 ══")
    p = parse.parse("铁生锈的条件不是氧气加上水才能生锈吗")
    ck("2.1 「不是…吗」→ challenge",
       any(c["intent"] == "challenge" for c in p["clauses"]),
       [(c["raw"], c["intent"]) for c in p["clauses"]])
    p = parse.parse("铁不会生锈吗")
    ck("2.2 「不会…吗」仍是 query_confirm（不误伤）",
       all(c["intent"] == "query_confirm" for c in p["clauses"]),
       [(c["raw"], c["intent"]) for c in p["clauses"]])
    p = parse.parse("难道铁在干燥环境也会生锈吗")
    ck("2.3 「难道」→ challenge",
       any(c["intent"] == "challenge" for c in p["clauses"]),
       [(c["raw"], c["intent"]) for c in p["clauses"]])

    # ══ 3. 自我认知 ══
    print("══ 3. 自我认知通路 ══")
    sess, _ = make_session()
    r = sess.handle("你是什么")
    ck("3.1 你是什么 → 自我通路含 INCEPTION", "INCEPTION" in r, r[:80])
    ck("3.2 不落检索模板（无「目录里没有」）", "目录里" not in r, r[:80])
    r = sess.handle("你的名字是什么")
    ck("3.3 你的名字是什么 → 报名字", "INCEPTION" in r and "名字" not in r[:2], r[:80])
    r = sess.handle("你会什么")
    ck("3.4 你会什么 → 报能力边界", "我会" in r and "目录里" not in r, r[:80])

    # ══ 4. 纠正通路 ══
    print("══ 4. 纠正/反问通路 ══")
    sess, mock = make_session()
    r = sess.handle("铁生锈的条件不是氧气加上水才能生锈吗")
    ck("4.1 回复是纠正通路话术", "听懂了，你在纠正我" in r, r[:80])
    ck("4.2 propose 被调用一次", len(mock.proposals) == 1, mock.proposals)
    if mock.proposals:
        prop = mock.proposals[0]
        ck("4.3 申请目标是 G18", prop["target"] == "G18", prop["target"])
        ck("4.4 claimed 含氧气", "氧气" in prop["proposal"], prop["proposal"][:80])
        ck("4.5 claimed 含水", "水" in prop["proposal"], prop["proposal"][:80])
        ck("4.6 claimed 不含功能词（的/是/加/才/条件）",
           all(w not in prop["proposal"] for w in ("「的」", "「是」", "「加」", "「才」", "「条件」")),
           prop["proposal"][:80])
        ck("4.7 五要素齐全（推理过程+证据）",
           bool(prop["reason"]) and bool(prop["proof"]) and "推理过程" in prop["proof"],
           prop)

    # ══ 5. 主体锚定相关性否决 ══
    print("══ 5. 相关性否决（reason 层）══")
    qa = reason.answer_question("环境潮湿但是无氧气铁器放里面会怎样", rules=FIX)
    ck("5.0 铁器句有产出", qa is not None and bool(qa["conclusions"]),
       qa and qa["conclusions"])
    rules_out = {c["rule"] for c in qa["conclusions"]}
    ck("5.1 砍掉 G27 人生存（问铁附送人是噪音）", "G27" not in rules_out, rules_out)
    ck("5.2 保住 G18 生锈", "G18" in rules_out, rules_out)
    qa = reason.answer_question("真空能传声吗", rules=FIX)
    ck("5.3 真空句保 G10", qa and any(c["rule"] == "G10" for c in qa["conclusions"]),
       qa and qa["conclusions"])
    qa = reason.answer_question("绿色植物光照充足会怎样", rules=FIX)
    rules_out = {c["rule"] for c in qa["conclusions"]}
    ck("5.4 绿色植物保 G25+G26 链式豁免",
       rules_out == {"G25", "G26"}, rules_out)

    # ══ 6. 谓词保真 ══
    print("══ 6. 谓词保真（结构类比弃权）══")
    s = reason.structural_analogize("铁生锈的条件不是氧气加上水才能生锈吗", FIX, [])
    ck("6.1 纠正句结构类比弃权（不产垃圾谓词）", s == [], s)
    s = reason.structural_analogize("铁器碰到水会怎样", FIX, ["器"])
    ck("6.2 残骸字「器」随主体剥，弃权", s == [], s)
    s = reason.structural_analogize("铁会感冒吗", FIX, ["感冒"])
    ck("6.3 感冒句不回归（正常结构类比有产出）", len(s) >= 1, s)
    if s:
        ck("6.4 感冒谓词保真", s[0]["goal_word"] == "感冒", s[0].get("goal_word"))

    # ══ 7. 介词短语事实 ══
    print("══ 7. 介词短语事实进 facts ══")
    qa = reason.answer_question("铁器碰到水在空气中长时间这样会如何", rules=FIX)
    facts = {(f["var"], f["val"]) for f in (qa["facts"] if qa else [])}
    ck("7.1 认出 环境接触=水", ("环境接触", "水") in facts, facts)
    ck("7.2 认出 环境接触=空气", ("环境接触", "空气") in facts, facts)
    ck("7.3 介词事实标来源", qa and any(
        f["var"] == "环境接触" and f["how"] == "介词短语" for f in qa["facts"]))

    # ══ 8. confirm_cond 三分支 ══
    print("══ 8. 条件确认轮（confirm_cond）══")
    sess, _ = make_session()
    sess.state.pending = {"action": "confirm_cond",
                          "slots": {"origin": "铁器碰到水会怎样",
                                    "missing": ["环境=潮湿"]},
                          "round": sess.state.counter}
    import datetime as _dt
    r = sess._try_pending("算", _dt.datetime.now())
    ck("8.1 「算」→ 补条件重推出结论",
       r is not None and "按你确认的算" in r and "生锈" in r, r and r[:90])
    ck("8.1b pending 已清", sess.state.pending is None)
    sess.state.pending = {"action": "confirm_cond",
                          "slots": {"origin": "铁器碰到水会怎样",
                                    "missing": ["环境=潮湿"]},
                          "round": sess.state.counter}
    r = sess._try_pending("不算", _dt.datetime.now())
    ck("8.2 「不算」→ 诚实退出不瞎推",
       r is not None and "推不动" in r, r and r[:80])
    sess.state.pending = {"action": "confirm_cond",
                          "slots": {"origin": "铁器碰到水会怎样",
                                    "missing": ["环境=潮湿"]},
                          "round": sess.state.counter}
    r = sess._try_pending("今天天气怎么样", _dt.datetime.now())
    ck("8.3 答非所问不劫持（返回 None）", r is None, r)

    # ══ 9. handle 集成：残骸字不造胡话 ══
    print("══ 9. handle 集成：铁器句无胡话 ══")
    sess, _ = make_session()
    r = sess.handle("铁器碰到水会怎样")
    ck("9.1 回复不含垃圾谓词「器水」", "器水" not in r, r[:110])
    ck("9.2 走分支试算给出合理回复（含生锈结论）", "生锈" in r and "如果" in r,
       r[:110])
finally:
    refine.auto_submit_reqs = _orig_auto
    for p, data in _bk.items():
        restore(p, data)

print()
print("══ 结果：{} 过 / {} 挂 ══".format(len(passed), len(failed)))
if failed:
    print("挂掉的：")
    for f in failed:
        print("  ✗", f)
    sys.exit(1)
print("全部通过。")
