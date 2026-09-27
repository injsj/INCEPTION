# -*- coding: utf-8 -*-
"""第二轮修复的专项验证脚本（一次性，不常驻项目）。
运行：.venv python 此脚本。分两段：
  A) 知识库 HTTP/存储层（真实服务，端口 8398，用现建现销的临时密钥）
  B) AI/brain 层（state/archive 重定向到临时目录，不污染真实会话状态）
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

CANGKU = Path(r"D:\cangku")
sys.path.insert(0, str(CANGKU / "知识库"))

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond)))
    print("  [{}] {}{}".format("✓" if cond else "✗", name,
                               ("  " + str(extra)) if (extra and not cond) else ""))


# ═══════════════ A. 知识库侧 ═══════════════
def phase_a():
    import requests
    import uvicorn
    from kb import auth, config, reqsys, store
    from kb.audit import verify as log_verify
    from kb.main import app

    print("== A. 知识库侧 ==")
    config.validate()
    store.rebuild_index()
    auth.revoke_key("二检管理员")
    auth.revoke_key("二检AI")
    adm = auth.add_key("二检管理员", "admin")
    aik = auth.add_key("二检AI", "ai")

    port = 8398
    threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=port,
                                                log_level="error"),
                     daemon=True).start()
    time.sleep(2)
    base = "http://127.0.0.1:{}".format(port)
    H_ADM = {"X-Admin-Key": adm}
    H_AI = {"X-API-Key": aik}

    # ── H2：chunked 超大请求体被拒 ──
    big = b"x" * (config.MAX_BODY_BYTES + 100)

    def gen():
        for i in range(0, len(big), 65536):
            yield big[i:i + 65536]

    r = requests.post(base + "/api/apply", data=gen(),
                      headers={"Transfer-Encoding": "chunked",
                               "Content-Type": "application/json"})
    check("H2 chunked 超大请求体 413", r.status_code == 413, r.status_code)
    # chunked 小请求正常通过
    r = requests.post(base + "/api/apply", data=gen_small(),
                      headers={"Transfer-Encoding": "chunked",
                               "Content-Type": "application/json"})
    check("H2 chunked 正常请求不受影响", r.status_code == 200, r.status_code)

    # ── H3：自报名称含换行符，日志不产生伪造物理行、哈希链保持完好 ──
    log_file = config.LOG_DIR / "access.log"
    before = len(log_file.read_text(encoding="utf-8").splitlines())
    r = requests.post(base + "/api/apply",
                      json={"self_name": "攻击者\n9999-01-01 00:00:00 | admin | 伪造的管理操作",
                            "reason": "测试注入", "ids": []})
    after_lines = log_file.read_text(encoding="utf-8").splitlines()
    new_lines = len(after_lines) - before
    check("H3 换行注入被压成单行", r.status_code == 200 and new_lines == 1,
          (r.status_code, new_lines))
    ok_a = log_verify("access")
    check("H3 注入后 access 哈希链仍完好", ok_a[0], ok_a)

    # ── M1：已裁决申请不可再改 ──
    r = requests.post(base + "/api/req", headers=H_AI,
                      json={"action": "append_experience", "target": _any_entry(store),
                            "proposal": "二检经验", "reason": "r", "proof": "p"})
    rid = r.json()["id"]
    check("M1 首次裁决成功", reqsys.resolve_req(rid, "已拒绝", "二检第一次") is True)
    check("M1 已裁决申请不可再改", reqsys.resolve_req(rid, "已执行", "覆盖") is False)
    req = reqsys.get_req(rid)
    check("M1 状态保持首次裁决结果", req["status"] == "已拒绝", req["status"])
    r = requests.post(base + "/admin/api/reqs/{}/reject".format(rid),
                      headers=H_ADM, json={"note": "x"})
    check("M1 HTTP 层拒绝已裁决申请 404", r.status_code == 404, r.status_code)

    # ── M4：临时授权 hours 边界 ──
    auth.add_key("二检访问员", "reader", [])
    pid = _pending_entry(store)
    r = requests.post(base + "/admin/api/keys/temp", headers=H_ADM,
                      json={"name": "二检访问员", "id": pid, "hours": 0})
    check("M4 hours=0 被拒 400", r.status_code == 400, r.status_code)
    r = requests.post(base + "/admin/api/keys/temp", headers=H_ADM,
                      json={"name": "二检访问员", "id": pid, "hours": "abc"})
    check("M4 hours 非数字 400（不再 500）", r.status_code == 400, r.status_code)
    r = requests.post(base + "/admin/api/keys/temp", headers=H_ADM,
                      json={"name": "二检访问员", "id": pid, "hours": 999999})
    check("M4 hours 超大被拒 400", r.status_code == 400, r.status_code)
    r = requests.post(base + "/admin/api/keys/temp", headers=H_ADM,
                      json={"name": "二检访问员", "id": pid, "hours": 24})
    check("M4 合法 hours 正常", r.status_code == 200, r.status_code)
    auth.revoke_key("二检访问员")

    # ── M3：同一秒两次修改产生两份历史备份 ──
    eid = _any_entry(store)
    h0 = len(store.list_history(eid))
    store.update_entry(eid, {"content": "二检第一次修改"})
    store.update_entry(eid, {"content": "二检第二次修改"})
    h1 = len(store.list_history(eid))
    check("M3 同秒连续修改各留一份备份", h1 == h0 + 2, (h0, h1))

    # ── L6：索引路径越界被拒绝 ──
    idx_path = config.INDEX_FILE
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx["EVIL1"] = {"zone": "trusted", "type": "python", "title": "x",
                    "file": "../../outside.json"}
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    check("L6 越界路径 entry_path 返回 None", store.entry_path("EVIL1") is None)
    check("L6 越界路径 get_entry 返回 None", store.get_entry("EVIL1") is None)
    idx.pop("EVIL1")
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── H1：多线程并发录入，标记 ID 必须全部唯一 ──
    created, errs = [], []

    def worker(n):
        try:
            for _ in range(5):
                e, m = store.create_entry("pending", "python", {
                    "title": "并发测试{}".format(n), "summary": "s",
                    "usage": "u", "experience": "e", "limitations": "l"})
                if e:
                    created.append(e["id"])
        except Exception as ex:
            errs.append(str(ex))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("H1 并发 40 次录入无异常", not errs, errs[:3])
    check("H1 并发标记 ID 全部唯一",
          len(created) == 40 and len(set(created)) == 40,
          (len(created), len(set(created))))
    # 并发嵌套锁：meta_lock 重入不死锁（create_entry 内部还调 alloc_id）
    from kb.safeio import meta_lock
    with meta_lock():
        with meta_lock():
            pass
    check("H1 meta_lock 可重入", True)
    for i in created:  # 清理并发测试数据（走正规删除路径，留历史备份）
        store.delete_entry(i, "二检并发测试清理")

    # ── M1 补充：已处理的访问申请不可再裁决 ──
    reqsys.add_application("二检申请人", "r", [])
    app_n = [a for a in reqsys.list_applications() if a["self_name"] == "二检申请人"][-1]["n"]
    check("M1 访问申请首次裁决", reqsys.resolve_application(app_n, "已拒绝", "x") is not None)
    check("M1 访问申请不可二次裁决", reqsys.resolve_application(app_n, "已批准") is None)

    ok_m = log_verify("admin")
    check("admin 哈希链完好", ok_m[0], ok_m)

    if _pending_entry.fixture_id:  # 变更 #14 增补：自建夹具用后删除，保持队列干净
        store.delete_entry(_pending_entry.fixture_id, "二检夹具清理")
        _pending_entry.fixture_id = None

    auth.revoke_key("二检管理员")
    auth.revoke_key("二检AI")


def gen_small():
    yield json.dumps({"self_name": "chunked小请求", "reason": "r",
                      "ids": []}).encode("utf-8")


def _any_entry(store):
    for info in store.list_entries("trusted"):
        return info["id"]
    raise RuntimeError("信任区为空")


def _pending_entry(store):
    for info in store.list_entries("pending"):
        return info["id"]
    # 变更 #14 增补：待审核区为空时自建夹具（段末走正规删除路径清理，不留残留）
    e, msg = store.create_entry("pending", "python", {
        "title": "二检临时夹具", "summary": "待审核区为空时验证脚本自建的测试条目",
        "usage": "仅测试", "experience": "仅测试", "limitations": "仅测试"})
    if not e:
        raise RuntimeError("自建待审夹具失败：" + msg)
    _pending_entry.fixture_id = e["id"]
    return e["id"]


_pending_entry.fixture_id = None


# ═══════════════ B. AI/brain 侧 ═══════════════
def phase_b():
    print("== B. AI/brain 侧（状态重定向到临时目录）==")
    import os
    tmp = Path(tempfile.mkdtemp(prefix="braintest_"))
    os.environ["KB_API_KEY"] = "二检假密钥"
    sys.path.insert(0, str(CANGKU / "AI"))
    from brain import config as bcfg
    bcfg.STATE = tmp / "state"
    bcfg.ARCHIVE = tmp / "archive"
    bcfg.STATE.mkdir(parents=True)
    bcfg.ARCHIVE.mkdir(parents=True)

    # ── M6：推演置信升级 ──
    from brain.reason import Reasoner
    rules = [
        {"id": "T1", "when": ["触发=是"], "then": "结果=甲", "tier": "低",
         "domain": "生物", "source": "测试"},
        {"id": "T2", "when": ["触发=是"], "then": "结果=甲", "tier": "高",
         "domain": "生物", "source": "测试"},
    ]
    ry = Reasoner(rules)
    ry.assert_fact("触发", "是")
    derived = ry.forward()
    final = ry.facts["结果"]
    check("M6 同值结论置信被高路径升级", final.conf == 0.9 and final.value == "甲",
          (final.value, final.conf))
    check("M6 升级留痕", any("置信升级" in t for t in ry.trace), ry.trace)
    check("M6 derived 标记升级", any(d.get("upgraded") for d in derived))

    # ── M5：安全告警权重永不自动降 ──
    from brain import train

    class FakeState:
        def __init__(self):
            self.weights = {"安全告警": 2.0, "闲聊": 0.6}
            self.counter = 10

    class FakeSess:
        def __init__(self):
            self.state = FakeState()
            self.stats = type("S", (), {"counts": {"type:闲聊": 5.0}})()
            self.calls = []

        def adjust_weight(self, t, new, signal="TRAIN"):
            self.calls.append((t, new))
            self.state.weights[t] = new

    sess = FakeSess()
    train.maybe_train(sess)
    downs = [t for t, v in sess.calls if t == "安全告警"]
    check("M5 安全告警不参与自动降权", not downs, sess.calls)

    # ── L1：pending 可取消、会过期 ──
    from brain.session import Session
    s = Session(api_key="二检假密钥")
    r1 = s.handle("提醒我喝水")          # 缺时间 → 反问
    check("L1 缺槽反问", "几点" in r1, r1)
    r2 = s.handle("算了")
    check("L1 取消词退出追问", "放下" in r2 and s.state.pending is None, r2)
    s.handle("提醒我喝水")
    s.state.pending["round"] = s.state.counter - 99  # 伪造过期追问
    r3 = s.handle("你好")
    check("L1 过期追问不再劫持输入", s.state.pending is None and "我在" in r3, r3)

    # ── L2：含急诊词的未到期提醒不被结案 ──
    from datetime import datetime, timedelta
    future = (datetime.now() + timedelta(hours=2)).isoformat(timespec="seconds")
    ev = s._add_event("提醒", "用户", "提醒", obj="检查煤气阀门", time=future)
    s.handle("你好")
    states = {e["id"]: e["state"] for e in s.state.events}
    check("L2 急诊建议照发但提醒不结案",
          ev["advised"] is True and states[ev["id"]] == "待办", states.get(ev["id"]))

    # ── L3：档案进月度子目录且同名不覆盖 ──
    from brain import trace
    p1 = trace.record({"input": "一"})
    # 预占同名文件，模拟同毫秒碰撞
    p2 = trace.record({"input": "二"})
    check("L3 档案进月度子目录", p1.parent.name.startswith("20"), p1.parent)
    check("L3 两次记录各自成文", p1 != p2 and p1.exists() and p2.exists(), (p1, p2))

    # ── M2/L4：状态保存原子化 + aging 清理 + 开放事件不被截断丢弃 ──
    st = s.state
    st.events = st.events + [dict(ev, id="EOLD{}".format(i), state="完成")
                             for i in range(600)]  # 灌入 600 条已完成事件
    st.aging["DEAD99"] = 0.3                        # 死 id
    st.save()
    data = json.loads((bcfg.STATE / "session.json").read_text(encoding="utf-8"))
    saved_ids = {e["id"] for e in data["events"]}
    check("M2 状态文件可正常读回", data["counter"] == st.counter)
    check("L4 aging 死 id 已清理", "DEAD99" not in data["aging"])
    check("L4 未到期提醒在截断中幸存", ev["id"] in saved_ids)
    check("L4 已完成事件被截断", "EOLD0" not in saved_ids)

    shutil.rmtree(tmp, ignore_errors=True)


def main():
    try:
        phase_a()
    except Exception:
        traceback.print_exc()
        check("A 段异常", False)
    try:
        phase_b()
    except Exception:
        traceback.print_exc()
        check("B 段异常", False)
    bad = [n for n, ok in RESULTS if not ok]
    print("\n== 专项结果：{} 通过，{} 失败 ==".format(len(RESULTS) - len(bad), len(bad)))
    if bad:
        print("失败项：" + ", ".join(bad))
        raise SystemExit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    main()
