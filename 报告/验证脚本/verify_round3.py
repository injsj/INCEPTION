# -*- coding: utf-8 -*-
"""第三轮审查修复专项验证（R3-1 ~ R3-5 + 三个信息级顺手项）。

隔离原则：先把 brain.config.STATE/ARCHIVE 与 zhishiku 的 APPLICATIONS_FILE
重定向到临时目录，再 import 其他模块——不碰真实数据、不需要知识库服务在线。
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

AI_DIR = Path(r"D:\cangku\AI")
ZK_DIR = Path(r"D:\cangku\知识库")
sys.path.insert(0, str(AI_DIR))
sys.path.insert(0, str(ZK_DIR))

TMP = Path(tempfile.mkdtemp(prefix="round3_"))

# 1) 重定向 brain 运行期目录（必须先于 brain 其他模块 import）
os.environ.pop("KB_API_KEY", None)          # 无密钥场景
import brain.config as bconfig               # noqa: E402
bconfig.STATE = TMP / "state"
bconfig.ARCHIVE = TMP / "archive"
bconfig.KEY_FILE = bconfig.STATE / "ai_key.txt"
bconfig.ensure()

from brain import atoms, discovery, kb, refine, session, state as state_mod, timenorm, toy  # noqa: E402
from brain import attention                                    # noqa: E402

# 2) 重定向 zhishiku 申请文件
from kb import config as zconfig, reqsys                       # noqa: E402
zconfig.APPLICATIONS_FILE = TMP / "applications.json"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("{} {}{}".format("PASS" if cond else "FAIL", name,
                           (" | " + str(detail)) if detail else ""))


# ── R3-1：密钥缺失不再崩，全部走断连降级文案 ──────────────────
c = kb.get_client(None)
check("R3-1a get_client 无密钥返回 None（不抛 SystemExit）", c is None)

sess = session.Session(api_key=None)   # 构造不该崩
r = sess.handle("查一下P1")
check("R3-1b Session 查库动作无密钥→降级文案", "联系不上知识库" in r, r[:40])
r2 = sess.handle("查库 P5")
check("R3-1c 原子查库无密钥→降级文案", "联系不上知识库" in r2, r2[:40])
r3 = sess.handle("提醒我晚上11点喝水")
check("R3-1d 无密钥下本地提醒照常（规格#16）", "提醒你" in r3, r3[:40])

reply, _ = toy.run("查一下P1", api_key=None)
check("R3-1e toy 查库无密钥不崩→降级文案", "联系不上知识库" in reply, reply[:40])

# refine.submit_req：队列里放一条 knowledge_gap，无密钥应返回未提交
q = {"next_id": 2, "items": [{"id": "S1", "kind": "knowledge_gap", "content": "测试缺口",
                              "why": "测试", "status": "待裁决", "evidence": []}]}
import json
(refine.QUEUE_FILE).write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
msg = refine.submit_req("S1", api_key=None)
check("R3-1f submit_req 无密钥→明确未提交且建议保留待裁决",
      "未提交" in msg and refine._load_queue()["items"][0]["status"] == "待裁决", msg[:40])

# ── R3-2：访问申请剥离控制字符（ANSI 注入失效）────────────────
ok, _ = reqsys.add_application("访客\x1b[2J\x1b[31m小红", "想学\x07习\n知识\x00", [])
apps = reqsys.list_applications()
a = apps[-1]
has_ctrl = any(ord(ch) < 0x20 and ch not in "\t\n\r" or ord(ch) == 0x7f
               for ch in a["self_name"] + a["reason"])
check("R3-2a 自报名字/理由中的 ESC/BEL/NUL 被剥离", ok and not has_ctrl,
      repr(a["self_name"]))
check("R3-2b 换行仍保留（可读性不受影响）", "\n" in a["reason"])

# ── R3-5：已处理申请修剪到最近 500 条，待审全留 ────────────────
big = [{"n": i + 1, "self_name": "旧{}".format(i), "reason": "", "ids": [],
        "time": "2026-01-01", "status": "已拒绝" if i % 2 else "已批准", "note": ""}
       for i in range(505)]
zconfig.APPLICATIONS_FILE.write_text(json.dumps(big, ensure_ascii=False), encoding="utf-8")
reqsys.add_application("新人", "测试修剪", [])
apps = reqsys.list_applications()
resolved = [x for x in apps if x["status"] != "待审"]
pending = [x for x in apps if x["status"] == "待审"]
check("R3-5 已处理申请修剪到 500 条、待审保留",
      len(resolved) == 500 and len(pending) == 1,
      "resolved={} pending={}".format(len(resolved), len(pending)))

# ── R3-3：session.json 权重逐键校验 + ±20% 钳制 ───────────────
seed = attention.SEED_WEIGHTS
k0 = sorted(seed)[0]
bad = {k0: 0, sorted(seed)[1]: 9999, "假类型": 5.0,
       sorted(seed)[2]: "abc", sorted(seed)[3]: True}
(state_mod.SESSION_FILE).write_text(json.dumps({"weights": bad}), encoding="utf-8")
st = state_mod.SessionState.load()
lo0, hi1 = round(seed[k0] * 0.8, 3), round(seed[sorted(seed)[1]] * 1.2, 3)
check("R3-3a 权重 0 被钳到下界 {:.3f}".format(lo0), st.weights[k0] == lo0, st.weights[k0])
check("R3-3b 权重 9999 被钳到上界 {:.3f}".format(hi1),
      st.weights[sorted(seed)[1]] == hi1)
check("R3-3c 未知类型/非数值/布尔被丢弃",
      "假类型" not in st.weights
      and st.weights[sorted(seed)[2]] == seed[sorted(seed)[2]]
      and st.weights[sorted(seed)[3]] == seed[sorted(seed)[3]])

# ── R3-4：wait() 遇到 401/403 立即抛，不傻等 ──────────────────
import ai_client                                     # noqa: E402
from ai_client import KBClient, KBError              # noqa: E402

cli = KBClient("fake-key", base_url="http://127.0.0.1:9")  # 端口必不通
cli.my_requests = lambda: (_ for _ in ()).throw(KBError(401, "身份不被承认"))
t0 = datetime.now()
try:
    cli.wait("REQ1", poll_seconds=0, max_polls=100)
    raised = None
except KBError as e:
    raised = e.status
dt = (datetime.now() - t0).total_seconds()
check("R3-4a 401 立即抛出（不轮询）", raised == 401 and dt < 2, "{:.2f}s".format(dt))

cli2 = KBClient("fake-key", base_url="http://127.0.0.1:9")
try:
    cli2.wait("REQ1", poll_seconds=0, max_polls=2)
    raised2 = None
except KBError as e:
    raised2 = e.status
check("R3-4b 连接失败(-1)照旧重试、耗尽后报 408 超时", raised2 == 408)

# ── 信息级 a：布尔字面量不再被当成算术 ────────────────────────
ok_b, out_b = atoms.calculate("True")
ok_n, out_n = atoms.calculate("3*3+2")
check("INFO-a1 calculate('True') 拒绝", not ok_b, out_b[:40])
check("INFO-a2 正常算术不受影响（3*3+2=11）", ok_n and "11" in out_n, out_n[:40])

# ── 信息级 b：晚上12点=次日午夜；点半=30 分 ───────────────────
now = datetime(2026, 9, 23, 23, 0)
t1, _ = timenorm.parse_time_str("晚上12点", now)
t2, _ = timenorm.parse_time_str("今晚12点半", now)
t3, w3 = timenorm.parse_time_str("12点半", datetime(2026, 9, 23, 10, 0))
t4, _ = timenorm.parse_time_str("晚上8点半", datetime(2026, 9, 23, 10, 0))
check("INFO-b1 晚上12点 → 次日 00:00",
      t1 == datetime(2026, 9, 24, 0, 0), t1)
check("INFO-b2 今晚12点半 → 次日 00:30",
      t2 == datetime(2026, 9, 24, 0, 30), t2)
check("INFO-b3 12点半 → 当天 12:30", t3 == datetime(2026, 9, 23, 12, 30), t3)
check("INFO-b4 晚上8点半 → 当天 20:30", t4 == datetime(2026, 9, 23, 20, 30), t4)

# ── 信息级 c：捷径矿工跳过条件矛盾的合并候选 ──────────────────
rules = [
    {"id": "A", "when": ["温度=高"], "then": "湿度=低", "tier": "中", "domain": "演示"},
    {"id": "B", "when": ["湿度=低", "温度=低"], "then": "警报=开", "tier": "中", "domain": "演示"},
    {"id": "C", "when": ["甲=1"], "then": "乙=2", "tier": "中", "domain": "演示"},
    {"id": "D", "when": ["乙=2", "丙=3"], "then": "丁=4", "tier": "中", "domain": "演示"},
]
traces = [("t{}.json".format(i), ["A", "B"]) for i in range(3)] + \
         [("u{}.json".format(i), ["C", "D"]) for i in range(3)]
cands = discovery.mine_shortcuts(traces, rules)
contra = [c for c in cands if "温度=高" in c["content"] and "温度=低" in c["content"]]
consistent = [c for c in cands if "丁=4" in c["content"]]
check("INFO-c1 矛盾候选（温度=高 且 温度=低）被跳过", not contra,
      "{} 个候选".format(len(cands)))
check("INFO-c2 一致候选照常产出", len(consistent) == 1,
      consistent[0]["content"] if consistent else "")

print("\n===== {} / {} 通过 =====".format(sum(1 for _, c, _ in results if c), len(results)))
sys.exit(0 if all(c for _, c, _ in results) else 1)
