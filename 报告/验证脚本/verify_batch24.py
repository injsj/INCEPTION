# -*- coding: utf-8 -*-
"""丙批专项（变更 #24）：数值绑定 / 方程求解 / 矿工E / 句式组装 / 词典审计。
自清理：session.json 备份恢复，测试档案用后删除，不留残留。"""
import io
import json
import sys

sys.path.insert(0, r"D:\cangku\AI")
from brain import atoms, config, discovery, session as sess_mod   # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = ok + bool(cond), fail + (not bool(cond))
    print("  [{}] {}".format("✓" if cond else "✗", name))


SESSION_FILE = config.STATE / "session.json"
_backup = SESSION_FILE.read_bytes() if SESSION_FILE.exists() else None

try:
    print("══ 1. 数值绑定（甲 6）══")
    s = sess_mod.Session()
    s.state.num_bindings = {}
    s.handle("密度是7.8")
    check("「密度是7.8」进绑定表", s.state.num_bindings.get("密度") == 7.8)
    r = s.handle("计算 密度*2")
    check("「计算 密度*2」= 15.6", "15.6" in r, ) if False else check("「计算 密度*2」= 15.6", "15.6" in r)
    r2 = s.handle("密度乘以2等于几")
    check("「密度乘以2等于几」= 15.6（绑定名直写算式）", "15.6" in r2)
    r3 = s.handle("计算 密度*2+1")
    check("绑定参与混合运算 = 16.6", "16.6" in r3)
    # 持久化
    s.state.save()
    s2 = sess_mod.Session()
    check("绑定表随会话落盘恢复", s2.state.num_bindings.get("密度") == 7.8)

    print("══ 2. 一元一次方程（甲 7）══")
    r = atoms.solve_linear("x加3等于8，x等于几")
    check("x+3=8 → x=5", r and r[0] and "x = 5" in r[1])
    r = atoms.solve_linear("某数乘以2减4等于10")
    check("某数*2-4=10 → x=7", r and r[0] and "x = 7" in r[1])
    r = atoms.solve_linear("x加1等于x加2")
    check("x+1=x+2 → 矛盾无解明说", r and not r[0] and "无解" in r[1])
    r = atoms.solve_linear("十乘十等于多少")
    check("无未知数普通算式不归方程管", r is None)
    # 端到端（走会话路由）
    s3 = sess_mod.Session()
    s3.state.num_bindings = {}
    r = s3.handle("x加3等于8，x等于几")
    check("会话路由方程 → x = 5", "x = 5" in r)

    print("══ 3. 矿工 E：语词共现（甲 8）══")
    rules = [{"id": "GT", "when": ["金属=铁", "环境=潮湿"], "then": "生锈=会",
              "tier": "高", "domain": "化学", "source": "测试"}]
    # 真实档案里可能已有这对词——以「注入后计数恰好 +3」为判据，不看绝对有无
    def _pair_count(cands):
        for c in cands:
            if c["kind"] == "lang_cooccur" and "铁" in c["content"] and "潮湿" in c["content"]:
                return int(c["why"].split("你的 ")[1].split(" 份")[0])
        return 0
    c0 = _pair_count(discovery.mine_language_cooccur(rules))
    subdir = config.ARCHIVE / "test_miner_e"
    subdir.mkdir(exist_ok=True)
    for i in range(3):
        (subdir / "task_test_minere_{}.json".format(i)).write_text(json.dumps(
            {"input": "铁在潮湿环境里会怎样（测试样本{}）".format(i), "reply": "…"},
            ensure_ascii=False), encoding="utf-8")
    c1 = _pair_count(discovery.mine_language_cooccur(rules))
    check("注入 3 份共现档案后计数 +3（{}→{}）".format(c0, c1), c1 == c0 + 3)
    import shutil
    shutil.rmtree(subdir, ignore_errors=True)
    c2 = _pair_count(discovery.mine_language_cooccur(rules))
    check("清理后计数回落到基线", c2 == c0)

    print("══ 4. 句式组装（甲 10）══")
    s4 = sess_mod.Session()
    g = s4._do_unknown("greeting", "你好", [], {"thinking": [], "round": 1})
    check("问候回复含时段+在场（组装产物）", "好，我在" in g)
    h = sess_mod._followup_hint("time")
    check("时间槽反问组装（问法+示例）", "几点？" in h and "例如" in h)
    h2 = sess_mod._followup_hint("thing")
    check("事项槽反问组装", h2 == "提醒我做什么？")

    print("══ 5. 词典批准审计（乙 3）══")
    from brain import refine
    # 预清理：上轮崩溃可能留下的残留建议
    _q = refine._load_queue()
    _q["items"] = [i for i in _q["items"] if i.get("content") != "测试词丙批"]
    refine._save_queue(_q)
    audit = refine.LEXICON_AUDIT
    before = audit.read_text(encoding="utf-8") if audit.exists() else ""
    ids = refine.add_suggestions([{"kind": "lexicon", "content": "测试词丙批",
                                   "why": "验证审计流水", "evidence": []}])
    sid = ids[0]["id"] if ids else None
    msg = refine.apply_lexicon(sid) if sid else ""
    after = audit.read_text(encoding="utf-8") if audit.exists() else ""
    check("批准动作写入审计流水", "测试词丙批" in after and len(after) > len(before))
    check("批准文案正常", "已录入词典" in msg)
    # 清理：词典扩展与队列、审计行
    import re as _re
    if refine.LEXICON_EXTRA.exists():
        words = [w for w in json.loads(refine.LEXICON_EXTRA.read_text(encoding="utf-8"))
                 if w.get("词") != "测试词丙批"]
        config.atomic_write(refine.LEXICON_EXTRA,
                            json.dumps(words, ensure_ascii=False, indent=2))
    _q = refine._load_queue()
    _q["items"] = [i for i in _q["items"] if i.get("content") != "测试词丙批"]
    refine._save_queue(_q)
    kept = _re.sub(r".*测试词丙批.*\n?", "", after)
    audit.write_text(kept, encoding="utf-8")
    print("  （测试词已清理，审计行已撤）")
finally:
    if _backup is not None:
        config.atomic_write(SESSION_FILE, _backup.decode("utf-8"))

print()
print("== 丙批专项：{} 通过，{} 失败 ==".format(ok, fail))
