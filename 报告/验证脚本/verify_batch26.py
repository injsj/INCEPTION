# -*- coding: utf-8 -*-
"""戊批专项（变更 #26）：阻塞度 + 执行三态（重试/暂停/恢复）。"""
import io
import sys

sys.path.insert(0, r"D:\cangku\AI")
from brain import attention, config, executor   # noqa: E402
from ai_client import KBError   # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = ok + bool(cond), fail + (not bool(cond))
    print("  [{}] {}".format("✓" if cond else "✗", name))


print("══ 1. 阻塞度（规格 §3 第三成分）══")
e = {"id": "E1", "type": "提醒", "state": "待办", "source": "", "object": "浇花",
     "note": "", "time": None}
base = attention.score_event(e)
check("基准分（严重度 only，无截止时间）", base == attention.SEVERITY["提醒"])
check("卡着对话主线 +0.5", attention.score_event(e, blocks_main=True) == base + 0.5)
e2 = dict(e, blocks=["E9", "E10"])
check("显式阻塞两个事件 +0.6", attention.score_event(e2) == round(base + 0.6, 3))
evs = [e, {"id": "E2", "type": "推演", "state": "待办", "source": "",
           "object": "", "note": "", "time": None}]
batch = attention.pick_batch(evs, dict(attention.SEED_WEIGHTS), {},
                             pending_event_id="E2")
scores = {x["id"]: attention.score_event(x, blocks_main=(x["id"] == "E2"))
          for x in batch}
check("pending 事件因阻塞度排到执行序前头",
      batch[0]["id"] == "E2" and scores["E2"] > scores["E1"])

print("══ 2. 执行三态（规格 §5）══")


class FakeKB:
    """可控故障序列的假知识库。"""
    def __init__(self, script):
        self.script = list(script)

    def read(self, eid):
        nxt = self.script.pop(0) if self.script else ("ok",)
        if nxt[0] == "raise":
            raise KBError(nxt[1], "模拟故障")
        return {"title": "T", "summary": "S", "usage": "U",
                "experience": "E", "limitations": "L"}


s, o, n = executor.execute_3state(
    "describe", {"target": "P1"},
    FakeKB([("raise", 500), ("raise", 502), ("ok",)]))
check("两次 5xx 后成功：重试到第 3 次拿到结果", s == "ok" and n == 3)

s, o, n = executor.execute_3state(
    "describe", {"target": "P1"},
    FakeKB([("raise", 500)] * 9))
check("一直故障：3 次封顶报 error", s == "error" and n == 3 and "3 次尝试" in o)

s, o, n = executor.execute_3state(
    "describe", {"target": "P1"}, FakeKB([("raise", 403)]))
check("403 确定性失败不重试（1 次即交差，不冤枉暂停）",
      s == "error" and n == 1 and "权限" in o)

s, o, n = executor.execute_3state(
    "describe", {"target": "P1"}, FakeKB([("ok",)]), suspended=["describe"])
check("暂停名单里的动作直接走人工通道（0 次执行）",
      s == "error" and n == 0 and "暂停" in o)

print("══ 3. 恢复口令（会话级）══")
from brain import session as sess_mod
SESSION_FILE = config.STATE / "session.json"
_backup = SESSION_FILE.read_bytes() if SESSION_FILE.exists() else None
try:
    sess = sess_mod.Session()
    sess.state.suspended_actions = ["search"]
    r = sess.handle("恢复执行 search")
    check("恢复口令解除暂停", "已恢复执行" in r and
          "search" not in sess.state.suspended_actions)
    r2 = sess.handle("恢复执行 describe")
    check("不在名单的动作如实告知", "不在暂停名单" in r2)
finally:
    if _backup is not None:
        config.atomic_write(SESSION_FILE, _backup.decode("utf-8"))

print()
print("== 戊批专项：{} 通过，{} 失败 ==".format(ok, fail))
