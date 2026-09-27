# -*- coding: utf-8 -*-
"""变更 #30 专项验证：数据保险 / 装配弃权 / 调度传动轴三件套。

分组：
  1. 数据保险：snapshot_once 产物完整（kb+ai 双前缀、zip 完好）、today_done、
     prune_old 只删本模块快照且保留最近 14 份（临时目录模拟 16 份）
  2. 装配弃权：动词未登记 → None；正常规则/被动句/程度类不拦；
     _say 弃权话术+报生词；handle 集成（未登记动词规则结论 → 说不好+记生词）
  3. 空闲推进（B1）：pending 推演事件 → handle 一轮推出结论附加+事件结案；
     同日第二轮不再推（限推一次）；推不出不刷屏只留痕
  4. outcome 接回（B2）：_success_rate 中性/纯好/纯坏；maybe_train 集成——
     同频率下成功率高者才上调
  5. 语境加权（B3）：scan_order context_types 改变排序；score_event urgent 阻塞度加倍；
     handle 集成：recent_acts 落盘截断 5

自清理：session.json / suggestions.json / req_throttle.json / predictions.json /
weights.log 全部备份恢复；Session 用 dummy key 离线构造；事件表清空隔离；
auto_submit_reqs 打桩。
"""
import sys
from pathlib import Path

AI_DIR = Path(r"D:\cangku\AI")
KB_DIR = Path(r"D:\cangku\知识库")
for p in (str(AI_DIR), str(KB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from brain import attention, compose, lexicon, refine, session as sess_mod, state as state_mod, train  # noqa: E401
from kb import backup  # noqa: E401

STATE = state_mod.SESSION_FILE.parent
FILES = [state_mod.SESSION_FILE, STATE / "suggestions.json",
         STATE / "req_throttle.json", STATE / "predictions.json",
         STATE / "weights.log"]

passed, failed = [], []


def ck(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  [{}] {}{}".format("✓" if cond else "✗", name,
                               (" — " + str(detail)[:110]) if (detail and not cond) else ""))


def backup_file(p):
    return p.read_bytes() if p.exists() else None


def restore_file(p, data):
    if data is None:
        if p.exists():
            p.unlink()
    else:
        p.write_bytes(data)


FIX = [
    {"id": "G18", "when": ["金属=铁", "环境=潮湿"], "then": "生锈=会",
     "tier": "高", "domain": "化学", "source": "夹具"},
    {"id": "G25", "when": ["植物=绿色植物", "光照=充足"], "then": "光合作用=进行",
     "tier": "高", "domain": "生物", "source": "夹具"},
]


def make_session():
    sess = sess_mod.Session(api_key="dummy-offline-b30")
    sess.rules = [dict(r) for r in FIX]
    sess._lex = lexicon.load_all()
    sess._lex_update_from_knowledge()
    sess.state.events = []          # 测试隔离：真实待办事件不进测试
    sess.state.recent_acts = []
    return sess


_bk = {p: backup_file(p) for p in FILES}
_orig_auto = refine.auto_submit_reqs
refine.auto_submit_reqs = lambda fresh, sess: []
try:
    # ══ 1. 数据保险 ══
    print("══ 1. 数据保险 ══")
    out, n = backup.snapshot_once()
    import zipfile
    names = zipfile.ZipFile(str(out)).namelist()
    ck("1.1 快照含知识库三区", any(x.startswith("kb/data/trusted/") for x in names))
    ck("1.2 快照含 AI 状态", any(x.startswith("ai/state/") for x in names))
    ck("1.3 zip 完好", zipfile.ZipFile(str(out)).testzip() is None)
    ck("1.4 today_done 检出", backup.today_done())
    # prune：临时目录造 16 份假快照 + 1 个外来文件，验删旧保 14、不动外来文件
    import tempfile, time
    with tempfile.TemporaryDirectory() as td:
        bdir = Path(td)
        for i in range(16):
            (bdir / "kb-202609{:02d}-000000.zip".format(i + 1)).write_bytes(b"x")
        alien = bdir / "别的东西.zip"
        alien.write_bytes(b"keep")
        orig = backup.BACKUP_DIR
        backup.BACKUP_DIR = bdir
        gone = backup.prune_old()
        left = sorted(bdir.glob("kb-*.zip"))
        backup.BACKUP_DIR = orig
        ck("1.5 删旧 2 份保留 14 份", gone == 2 and len(left) == 14, (gone, len(left)))
        ck("1.6 保留的是最新的", left[0].name == "kb-20260903-000000.zip", left[0].name)
        ck("1.7 外来文件不动", alien.exists())
    out.unlink()   # 本组测试快照当场删（启动补做的那份是真实备份，不动）

    # ══ 2. 装配弃权 ══
    print("══ 2. 装配弃权 ══")
    ck("2.1 动词未登记 → 弃权 None",
       compose.compose_conclusion({"then": "星芒闪耀=会", "rule": "T9", "conf": 0.9}) is None)
    s = compose.compose_conclusion({"then": "生锈=会", "rule": "G18", "conf": 1.0},
                                   rule_when=["金属=铁", "环境=潮湿"], facts=[{"val": "铁"}])
    ck("2.2 正常规则不拦", s is not None and "生锈" in s, s)
    s = compose.compose_conclusion({"then": "被2整除=能", "rule": "M1", "conf": 1.0})
    ck("2.3 被动句不拦", s is not None and "被2整除" in s, s)
    s = compose.compose_conclusion({"then": "觅食行为=激烈", "rule": "R1", "conf": 0.6})
    ck("2.4 程度类不拦", s is not None and "觅食" in s, s)
    sess = make_session()
    bad_rule = {"id": "T9", "when": ["金属=铁", "环境=潮湿"], "then": "星芒闪耀=会",
                "tier": "高", "domain": "化学", "source": "测试"}
    sess.rules = FIX + [bad_rule]
    r = sess.handle("环境潮湿的铁会怎样")
    ck("2.5 集成：说不好而不胡拼", "说不好" in r and "它会星芒" not in r, r[:120])
    ck("2.6 弃权报生词", sess.stats.counts.get("unknown:星芒闪耀", 0) > 0,
       sess.stats.counts.get("unknown:星芒闪耀"))

    # ══ 3. 空闲推进（B1）══
    print("══ 3. 空闲推进 ══")
    sess = make_session()
    sess._add_event("推演", "用户", "问答推演", obj="环境潮湿的铁会怎样", source="测试")
    ev = sess.state.events[-1]
    r = sess.handle("你好")   # 随便一句，触发推进器
    ck("3.1 推出结论附加答复", "顺带" in r and "生锈" in r, r[:130])
    ck("3.2 推进后事件结案", ev["state"] == "完成", ev["state"])
    sess2 = make_session()
    sess2._add_event("推演", "用户", "问答推演", obj="环境潮湿的铁会怎样", source="测试")
    sess2.state.events[-1]["pushed"] = __import__("datetime").date.today().isoformat()
    r2 = sess2.handle("你好")
    ck("3.3 同日已推过不再推", "顺带" not in r2, r2[:80])
    sess3 = make_session()
    sess3._add_event("推演", "用户", "问答推演", obj="冰淇淋在火星上会怎样", source="测试")
    ev3 = sess3.state.events[-1]
    r3 = sess3.handle("你好")
    ck("3.4 推不出不刷屏（无顺带）且事件不结案",
       "顺带" not in r3 and ev3["state"] == "待办", (r3[:60], ev3["state"]))

    # ══ 4. outcome 接回训练（B2）══
    print("══ 4. outcome 接回训练 ══")
    ck("4.1 无记录 → 0.5 中性", train._success_rate({}, "推演") == 0.5)
    ck("4.2 纯好记录 → 1.0",
       train._success_rate({"outcome:推演:命中": 3}, "推演") == 1.0)
    ck("4.3 好坏相抵 → 0.5",
       train._success_rate({"outcome:推演:命中": 2, "outcome:推演:落空": 2}, "推演") == 0.5)
    ck("4.4 别类记录不串账",
       train._success_rate({"outcome:提醒:达成": 5}, "推演") == 0.5)
    # 集成：五类同频率，推演全落空、其余无记录 → 只有推演被降权
    sess = make_session()
    sess.state.counter = 10
    sess.state.stats_counts = {"type:推演": 10.0, "type:闲聊": 10.0,
                               "type:提醒": 10.0, "type:知识查询": 10.0,
                               "type:安全告警": 10.0,
                               "outcome:推演:落空": 4.0}
    sess.stats = sess_mod.stats.Stats(sess.state.stats_counts)
    sess.state.weights = dict(attention.SEED_WEIGHTS)
    changed = dict((t, (o, n)) for t, o, n in train.maybe_train(sess))
    ck("4.5 全落空的推演被降权", "推演" in changed and changed["推演"][1] < changed["推演"][0],
       changed)
    ck("4.6 同频率无坏记录的闲聊不降", "闲聊" not in changed, changed)
    ck("4.7 安全告警零频也只升不降（M5 不破）",
       "安全告警" not in changed or changed["安全告警"][1] > changed["安全告警"][0],
       changed)

    # ══ 5. 语境加权（B3）══
    print("══ 5. 语境加权 ══")
    evs = [{"id": "E1", "type": "闲聊", "state": "待办"},
           {"id": "E2", "type": "推演", "state": "待办"}]
    w = {"闲聊": 0.6, "推演": 1.0}
    plain = [e["id"] for e in attention.scan_order(evs, w, {})]
    ctx = [e["id"] for e in attention.scan_order(evs, w, {}, context_types={"闲聊"})]
    ck("5.1 无语境推演在前", plain == ["E2", "E1"], plain)
    ck("5.2 闲聊语境下闲聊翻前", ctx == ["E1", "E2"], ctx)
    import datetime as _dt
    e_block = {"id": "E3", "type": "推演", "state": "待办"}
    s_normal = attention.score_event(e_block, _dt.datetime.now(), blocks_main=True)
    s_urgent = attention.score_event(e_block, _dt.datetime.now(), blocks_main=True,
                                     urgent=True)
    ck("5.3 紧迫态阻塞度加倍", abs(s_urgent - s_normal - 0.5) < 1e-9,
       (s_normal, s_urgent))
    sess = make_session()
    sess.handle("你好")
    ck("5.4 recent_acts 落盘维护", sess.state.recent_acts[-1] == "greeting",
       sess.state.recent_acts)
finally:
    refine.auto_submit_reqs = _orig_auto
    for p, data in _bk.items():
        restore_file(p, data)

print()
print("══ 结果：{} 过 / {} 挂 ══".format(len(passed), len(failed)))
if failed:
    print("挂掉的：")
    for f in failed:
        print("  ✗", f)
    sys.exit(1)
print("全部通过。")
