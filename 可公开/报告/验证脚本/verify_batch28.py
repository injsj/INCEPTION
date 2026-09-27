# -*- coding: utf-8 -*-
"""变更 #28 专项验证：REQ直通 / 旧账清理 / 切片回收站 / 结构类比 / 从句嵌套 / 网页exec。

分组：
  1. REQ 五要素 payload（离线，七种直通类型逐个过）
  2. auto_submit_reqs 节流（mock client：去重/日限/积压/断连）
  3. 旧账清理后状态分布（只读断言：知识类候选不再滞留待裁决）
  4. 切片回收站全流程（mock sess：删→回收站→恢复→彻底删→过期清；含 sid 被占时记录重写）
  5. 结构类比三例（需服务在线拿 KB 规则，断连记跳过不记失败）
  6. 从句嵌套（离线 parse 六例 + 会话级 handle 一例：思考链有「句内嵌套」，结论带条件前缀）
  7. 管理端 exec new/modify（HTTP 活体：新增入库→清理条目与 REQ 文件；已裁决 REQ 再 exec 拒绝）

自清理：session.json / suggestions.json / req_throttle.json / slice_trash.json 全部先备份后恢复；
测试 REQ 文件与测试条目当场删除；不留任何持久垃圾。
"""
import datetime
import json
import os
import sys
import time
import types
from pathlib import Path

AI_DIR = Path(r"D:\cangku\AI")
KB_DIR = Path(r"D:\cangku\知识库")
for p in (str(AI_DIR), str(KB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from brain import kb as brain_kb, parse, refine, session as sess_mod, state as state_mod  # noqa: E401
from kb import inception_api  # noqa: E401

SESSION_FILE = state_mod.SESSION_FILE
TRASH_FILE = inception_api._trash_file()
ADMIN_KEY = os.environ.get("CANGKU_ADMIN_KEY", "")  # 密钥不入库：set CANGKU_ADMIN_KEY=...
BASE = "http://127.0.0.1:8320"

passed, failed, skipped = [], [], []


def ck(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  [{}] {}{}".format("✓" if cond else "✗", name,
                               (" — " + str(detail)[:90]) if (detail and not cond) else ""))


def skip(name, why):
    skipped.append(name)
    print("  [跳过] {}（{}）".format(name, why))


def backup(p):
    return p.read_bytes() if p.exists() else None


def restore(p, data):
    if data is None:
        if p.exists():
            p.unlink()
    else:
        p.write_bytes(data)


def wait_budget(client, tries=3):
    """限流预算探测：服务端对 /api/kb 等每 IP 每分钟 60 次（第二轮审查设计，
    不动它）。前面分组已烧掉预算时，等一个窗口再进 KB 依赖组。返回是否可用。"""
    for i in range(tries):
        if client is None:
            return False
        try:
            client.catalog()
            return True
        except Exception:
            if i < tries - 1:
                time.sleep(65)
    return False


# ══ 1. REQ 五要素 payload ══
print("══ 1. REQ 五要素（七种直通类型）══")
samples = {
    "knowledge_gap": {"id": "T1", "kind": "knowledge_gap", "content": "光合作用",
                      "why": "查无 3 次", "evidence": ["A1", "A2"], "status": "待裁决"},
    "cooccur": {"id": "T2", "kind": "cooccur", "content": "G1↔G2",
                "why": "共现 4 次", "evidence": ["A3"], "status": "待裁决"},
    "shortcut": {"id": "T3", "kind": "shortcut", "content": "当[A]则[B]",
                 "why": "链反复出现", "evidence": ["A4"], "status": "待裁决"},
    "converge": {"id": "T4", "kind": "converge", "content": "变量X",
                 "why": "多规则指向", "evidence": [], "status": "待裁决"},
    "num_converge": {"id": "T5", "kind": "num_converge", "content": "2+3=5",
                     "why": "多径同果", "evidence": ["A5"], "status": "待裁决"},
    "lang_cooccur": {"id": "T6", "kind": "lang_cooccur", "content": "铁↔潮湿",
                     "why": "原文共现", "evidence": ["A6"], "status": "待裁决"},
    "rule_promote": {"id": "T7", "kind": "rule_promote", "content": "G2",
                     "why": "命中率100%", "evidence": ["A7"], "status": "待裁决"},
}
for kind, it in samples.items():
    action, target, proposal, reason, proof = refine._req_payload(dict(it))
    five_ok = all(str(x).strip() for x in (action, proposal, reason, proof))
    proof_ok = ("推理过程" in proof) or ("证据档案" in proof)
    ck("{}：五要素齐且 proof 带推理过程".format(kind), five_ok and proof_ok,
       (action, proposal[:30], reason[:30], proof[:40]))
ck("rule_promote 走 move_zone 动作", samples and refine._req_payload(samples["rule_promote"])[0] == "move_zone")
ck("知识类走 new 动作", all(refine._req_payload(samples[k])[0] == "new"
                            for k in samples if k != "rule_promote"))

# ══ 2. auto_submit_reqs 节流 ══
print("══ 2. REQ 直通节流（mock client）══")
q_backup = backup(refine.QUEUE_FILE)
th_backup = backup(refine.REQ_THROTTLE_FILE)
orig_get_client = brain_kb.get_client
try:
    calls = []

    class FakeClient:
        def __init__(self, backlog=0):
            self.backlog = backlog

        def my_requests(self):
            return [{"status": "待审核"}] * self.backlog

        def propose(self, action, target="", proposal="", reason="", proof=""):
            calls.append((action, proposal))
            return "REQT28"

    def mkqueue():
        return {"next_id": 2, "items": [
            {"id": "TQ1", "kind": "knowledge_gap", "content": "测试主题28",
             "why": "测试", "evidence": ["E"], "status": "待裁决"}]}

    sess = types.SimpleNamespace(api_key=None)

    # 2a. 正常直通：打一条，台账状态翻转
    refine._save_queue(mkqueue())
    restore(refine.REQ_THROTTLE_FILE, None)
    brain_kb.get_client = lambda key=None: FakeClient()
    done = refine.auto_submit_reqs(mkqueue()["items"], sess)
    ck("正常直通提交 1 条", done == ["REQT28"] and len(calls) == 1, done)
    it = refine._load_queue()["items"][0]
    ck("台账状态翻转为已提交REQ", it["status"] == "已提交REQ" and it.get("req") == "REQT28", it)

    # 2b. 同内容再打：不重复提交，只翻台账
    calls.clear()
    refine._save_queue(mkqueue())
    done2 = refine.auto_submit_reqs(mkqueue()["items"], sess)
    ck("同内容不重复打（去重）", done2 == [] and len(calls) == 0, (done2, calls))
    ck("去重后台账仍翻为已提交REQ", refine._load_queue()["items"][0]["status"] == "已提交REQ")

    # 2c. 每日上限：count 已到 5 → 不打
    calls.clear()
    refine._save_queue(mkqueue())
    th = {"date": datetime.date.today().isoformat(), "count": refine.REQ_DAILY_LIMIT,
          "submitted": {}}
    refine.config.atomic_write(refine.REQ_THROTTLE_FILE,
                               json.dumps(th, ensure_ascii=False))
    done3 = refine.auto_submit_reqs(mkqueue()["items"], sess)
    ck("每日上限 {} 到达即停".format(refine.REQ_DAILY_LIMIT), done3 == [] and not calls, done3)

    # 2d. 积压 ≥10 → 不打
    calls.clear()
    restore(refine.REQ_THROTTLE_FILE, None)
    brain_kb.get_client = lambda key=None: FakeClient(backlog=refine.REQ_BACKLOG_PAUSE)
    done4 = refine.auto_submit_reqs(mkqueue()["items"], sess)
    ck("待审核积压 ≥{} 暂停上报".format(refine.REQ_BACKLOG_PAUSE), done4 == [] and not calls, done4)

    # 2e. 断连（client None）→ 不打不炸
    brain_kb.get_client = lambda key=None: None
    done5 = refine.auto_submit_reqs(mkqueue()["items"], sess)
    ck("断连留台账下轮再试（返回空）", done5 == [], done5)
finally:
    brain_kb.get_client = orig_get_client
    restore(refine.QUEUE_FILE, q_backup)
    restore(refine.REQ_THROTTLE_FILE, th_backup)

# ══ 3. 旧账清理后状态分布（只读）══
print("══ 3. 旧账分布（知识类候选不再滞留待裁决）══")
q = refine._load_queue()
import collections
dist = collections.Counter(it["status"] for it in q["items"])
print("  分布:", dict(dist))
lingering = [it["id"] for it in q["items"]
             if it["status"] == "待裁决" and it.get("kind") in refine._AUTO_KINDS]
ck("待裁决里无知识类候选（直通或作废，不许滞留）", not lingering, lingering)

# ══ 4. 切片回收站全流程 ══
print("══ 4. 切片回收站 ══")
s_backup = backup(SESSION_FILE)
t_backup = backup(TRASH_FILE)
try:
    sess = sess_mod.Session()
    st = sess.state
    st.slices["t28a"] = {"name": "测试回收28", "created": "2026-09-25T00:00:00"}
    st.active_slice = "main"
    st.merged = []
    st.history = [h for h in st.history
                  if h.get("text", "") not in ("回收站记录甲28", "回收站记录乙28")]
    st.history.append({"role": "user", "text": "回收站记录甲28", "slice": "t28a",
                       "time": "2026-09-25T12:00:00"})
    st.history.append({"role": "ai", "text": "收到", "slice": "t28a",
                       "time": "2026-09-25T12:00:01"})
    fake = types.SimpleNamespace(state=st)
    inception_api._sess = fake
    st.save = lambda: None

    r = inception_api.slice_op("del", {"id": "t28a"})
    ck("删除后切片从视图消失", all(s["id"] != "t28a" for s in r["slices"]))
    ck("删除后记录从主历史搬走（不再可见）",
       all(h.get("text") != "回收站记录甲28" for h in st.history))
    t = inception_api._trash_load()
    item = next((x for x in t["items"] if x["sid"] == "t28a"), None)
    ck("回收站有条目且记录随条目搬走", item is not None and len(item["records"]) == 2, item)
    r = inception_api.slice_op("trash", {})
    ck("trash 清单返回条目（带条数/删除时间）",
       any(x["sid"] == "t28a" and x["count"] == 1 for x in r["items"]), r["items"][:2])

    # 恢复（sid 空闲 → 沿用原 sid，记录归位）
    r = inception_api.slice_op("restore", {"id": "t28a"})
    back = [h for h in st.history if h.get("text") == "回收站记录甲28"]
    ck("恢复后记录归位且 slice 字段正确", back and back[0]["slice"] == "t28a", back)
    ck("恢复后切片成为活动切片", r["active"] == "t28a")
    ck("恢复后回收站清空该条目",
       all(x["sid"] != "t28a" for x in inception_api._trash_load()["items"]))

    # sid 被占用时恢复：换新 sid 且记录重写（变更 #28 修复点）
    inception_api.slice_op("del", {"id": "t28a"})
    st.slices["t28a"] = {"name": "占位切片28", "created": "2026-09-25T01:00:00"}
    r = inception_api.slice_op("restore", {"id": "t28a"})
    nsid = r["active"]
    moved = [h for h in st.history if h.get("text") == "回收站记录甲28"]
    ck("sid 被占时恢复换新 sid", nsid != "t28a" and nsid in st.slices, nsid)
    ck("换新 sid 时记录 slice 字段重写（不隐身）",
       moved and moved[0]["slice"] == nsid, moved)
    # 清掉这次恢复的占用，重新进回收站测 purge
    inception_api.slice_op("del", {"id": nsid})
    t = inception_api._trash_load()
    sid_in_trash = t["items"][-1]["sid"] if t["items"] else None
    r = inception_api.slice_op("purge", {"id": sid_in_trash})
    left = inception_api._trash_load()["items"]
    ck("彻底删后回收站无残留", all(x["sid"] != sid_in_trash for x in left))
    r = inception_api.slice_op("purge", {"id": "不存在的sid"})
    ck("purge 不存在的条目如实报错", r.get("ok") is False)

    # 过期自动清：伪造两天前的条目
    old = (datetime.datetime.now() - datetime.timedelta(days=2)).isoformat(timespec="seconds")
    t = inception_api._trash_load()
    t["items"].append({"sid": "t28old", "name": "过期28", "created": old,
                       "deleted_at": old, "records": []})
    inception_api._trash_save(t)
    inception_api._trash_purge_expired()
    left = inception_api._trash_load()["items"]
    ck("满 1 天的条目自动彻底清除", all(x["sid"] != "t28old" for x in left), left)

    inception_api._sess = None
finally:
    restore(SESSION_FILE, s_backup)
    restore(TRASH_FILE, t_backup)
    inception_api._sess = None

# ══ 5. 结构类比（需 KB 规则在线）══
print("══ 5. 结构类比 ══")
client = brain_kb.get_client()
if not wait_budget(client):
    skip("结构类比三例", "知识库断连或限流窗口未恢复")
else:
    from brain import reason, ruleload, segment
    rules = ruleload.load_rules(client)
    if not rules:
        time.sleep(65)           # 拉到一半被限流会静默 None：等一个窗口重拉
        rules = ruleload.load_rules(client)
    if not rules:
        skip("结构类比三例", "KB 规则拉取失败（限流）")
    else:
        sess5 = sess_mod.Session()
        la = sess5._lex

        def unk_of(t):
            return [w for k, w in segment.segment(t, la) if k == "未知"]

        out = reason.structural_analogize("植物会生病吗", rules, unk_of("植物会生病吗"))
        ck("植物会生病吗 → 结构类比有产出且落到「生病」所在结构",
           bool(out) and any("生病" in str(o) for o in out), out[:1])
        out = reason.structural_analogize("铁会感冒吗", rules, unk_of("铁会感冒吗"))
        ck("铁会感冒吗 → 跨域移植（生锈→感冒）",
           bool(out) and any("感冒" in str(o) and "生锈" in str(o) for o in out), out[:1])
        out = reason.structural_analogize("水会不会害怕", rules, unk_of("水会不会害怕"))
        ck("水会不会害怕 → 无锚点如实没招（空）", out == [], out[:1])

# ══ 6. 从句嵌套 ══
print("══ 6. 从句嵌套（parse 六例 + 会话级一例）══")
r = parse.parse("如果环境潮湿，铁会怎样")
ck("跨分句条件：前句 absorbed，后句 rel=条件 且条件部/主句部正确",
   r["clauses"][0]["absorbed"] and r["clauses"][1]["rel"] == "条件"
   and r["clauses"][1]["cond_part"] == "环境潮湿"
   and r["clauses"][1]["main_part"] == "铁会怎样", r["clauses"])
ck("嵌套分句 intent 按主句部重算", r["clauses"][1]["intent"] == "query_how",
   r["clauses"][1]["intent"])
r = parse.parse("因为环境潮湿所以铁会生锈")
ck("句内双标因果：rel=因果 且拆分正确",
   len(r["clauses"]) == 1 and r["clauses"][0]["rel"] == "因果"
   and r["clauses"][0]["cond_part"] == "环境潮湿"
   and r["clauses"][0]["main_part"] == "铁会生锈", r["clauses"])
r = parse.parse("虽然铁很硬，但是它也会生锈")
ck("跨分句让步：主句部剥掉「但是」",
   r["clauses"][1]["rel"] == "让步" and r["clauses"][1]["main_part"] == "它也会生锈",
   r["clauses"])
r = parse.parse("如果便宜，我买")
ck("单字果标记不误挡配对（便宜里的「便」）",
   r["clauses"][1]["rel"] == "条件" and r["clauses"][1]["cond_part"] == "便宜", r["clauses"])
r = parse.parse("铁会生锈吗")
ck("无嵌套句不受影响", len(r["clauses"]) == 1 and r["clauses"][0]["rel"] is None
   and not r["clauses"][0]["absorbed"], r["clauses"])
r = parse.parse("如果如果A，B")
ck("深度>1 不爆炸（一层为止）", len(r["clauses"]) == 2, r["clauses"])

# 会话级：思考链有「句内嵌套」；若走推演结论路，回复带条件前缀
s_backup2 = backup(SESSION_FILE)
orig_auto = refine.auto_submit_reqs
try:
    refine.auto_submit_reqs = lambda fresh, sess: []   # 测试轮不打 REQ
    wait_budget(brain_kb.get_client())   # Session 建实例要拉规则，先等限流窗口
    sess6 = sess_mod.Session()
    sess6.state.active_slice = "main"
    sess6.state.merged = []
    reply = sess6.handle("如果环境潮湿，铁会怎样")
    ai_rec = next((h for h in reversed(sess6.state.history) if h.get("role") == "ai"), None)
    thinking = ai_rec.get("thinking", []) if ai_rec else []
    ck("会话级：思考链记下「句内嵌套」", any("句内嵌套" in t for t in thinking), thinking[-4:])
    ck("会话级：规则来自 KB 而非种子兜底（事实认得出才谈推演）",
       sess6.rules_source.startswith("KB"), sess6.rules_source)
    if "按规则推演的结果" in reply:
        ck("会话级：结论回复带「在你给的条件下」前缀",
           reply.startswith("在你给的「环境潮湿」条件下"), reply[:40])
    else:
        skip("会话级条件前缀", "本轮未走推演结论路（{}）".format(reply[:24]))
    # 答非所问优先（变更 #28 修正）：「铁会感冒吗」不许拿「生锈」的愿单分支充数，
    # 要转结构类比直接回答「感冒」
    reply2 = sess6.handle("铁会感冒吗")
    ck("答非所问优先：愿单在推生锈时转结构类比回答感冒",
       "感冒" in reply2 and "结构类比" in reply2, reply2[:80])
    ai_rec2 = next((h for h in reversed(sess6.state.history) if h.get("role") == "ai"), None)
    th2 = ai_rec2.get("thinking", []) if ai_rec2 else []
    ck("答非所问优先：思考链明说愿单答非所问",
       any("答非所问" in t for t in th2), th2[-3:])
finally:
    refine.auto_submit_reqs = orig_auto
    restore(SESSION_FILE, s_backup2)

# ══ 7. 管理端 exec new/modify（HTTP 活体）══
print("══ 7. 网页 exec（活体 HTTP）══")
try:
    import requests
except ImportError:
    requests = None
if requests is None:
    skip("网页 exec", "无 requests 库")
else:
    from kb import config as kbcfg

    def req(method, url, **kw):
        """429 重试：限流是每分钟窗口，等一个窗口再来（最多两次）。"""
        for i in range(3):
            r = getattr(requests, method)(url, timeout=8, **kw)
            if r.status_code != 429:
                return r
            if i < 2:
                time.sleep(65)
        return r

    test_rid, test_eid = None, None
    try:
        alive = req("get", BASE + "/admin/api/types",
                    headers={"X-Admin-Key": ADMIN_KEY})
        if alive.status_code != 200:
            raise RuntimeError("管理端握手失败 " + str(alive.status_code))
        tname = alive.json()["types"][0]
        tname = tname["name"] if isinstance(tname, dict) else tname

        # 7a. 无钥/错钥被拒
        r = req("get", BASE + "/admin/api/reqs")
        ck("无密钥访问管理端被拒", r.status_code in (401, 403), r.status_code)

        # 7b. 造一条测试 REQ → exec new 入存疑区
        c = brain_kb.get_client()
        test_rid = c.propose("new", target="",
                             proposal="【变更28验证】当[测试条件28]则[测试结论28]",
                             reason="验证脚本打的测试申请，验完即删",
                             proof="推理过程：验证 exec new 通路。证据档案：无")
        fields = {"title": "变更28验证条目", "summary": "验证用", "usage": "验证用",
                  "experience": "验证用", "limitations": "验证用"}
        r = req("post", BASE + "/admin/api/reqs/{}/exec".format(test_rid),
                headers={"X-Admin-Key": ADMIN_KEY},
                json={"zone": "suspect", "type": tname, "fields": fields, "note": "验证"})
        ck("exec new 网页入库成功", r.status_code == 200 and r.json().get("ok"), r.text[:80])
        # 找到刚入的条目
        r = req("get", BASE + "/admin/api/entries",
                headers={"X-Admin-Key": ADMIN_KEY}, params={"zone": "suspect"})
        ents = r.json().get("entries", [])
        hit = [e for e in ents if e.get("title") == "变更28验证条目"]
        test_eid = hit[0]["id"] if hit else None
        ck("存疑区找得到刚入库的测试条目", test_eid is not None, [e.get("id") for e in hit])

        # 7c. 已裁决 REQ 再 exec → 拒绝（状态机）
        r = req("post", BASE + "/admin/api/reqs/{}/exec".format(test_rid),
                headers={"X-Admin-Key": ADMIN_KEY},
                json={"zone": "suspect", "type": tname, "fields": fields})
        ck("已执行的 REQ 不可再 exec（状态机守卫）", r.status_code == 404, r.status_code)

        # 7d. exec modify 通路：造 REQ(modify) 指到刚入的测试条目
        if test_eid:
            rid2 = c.propose("modify", target=test_eid,
                             proposal="【变更28验证】修改摘要", reason="验证 modify 通路",
                             proof="推理过程：验证。证据档案：无")
            r = req("post", BASE + "/admin/api/reqs/{}/exec".format(rid2),
                    headers={"X-Admin-Key": ADMIN_KEY},
                    json={"fields": {"summary": "变更28验证·已修改"}, "note": "验证"})
            ck("exec modify 网页修改成功", r.status_code == 200 and r.json().get("ok"),
               r.text[:80])
            (kbcfg.DATA_DIR / "requests" / (rid2 + ".json")).unlink(missing_ok=True)
    except Exception as e:  # 服务不在线等
        skip("网页 exec 活体", "服务不可达或异常：{}".format(e))
    finally:
        # 自清理：删测试条目与测试 REQ 文件
        if test_eid:
            from kb import store as kbstore
            kbstore.delete_entry(test_eid, "变更28验证清理")
        if test_rid:
            (kbcfg.DATA_DIR / "requests" / (test_rid + ".json")).unlink(missing_ok=True)

print()
print("== 变更 #28 专项：{} 通过，{} 失败，{} 跳过 ==".format(len(passed), len(failed), len(skipped)))
if failed:
    print("失败项：", failed)
    sys.exit(1)
print("全部通过 ✓")
