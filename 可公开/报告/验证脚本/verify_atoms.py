# -*- coding: utf-8 -*-
"""变更 #11 原子操作清单 + 数值趋同矿工 专项验证（临时目录，不碰真实状态）。"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

CANGKU = Path(r"D:\cangku")
tmp = Path(tempfile.mkdtemp(prefix="atomtest_"))
os.environ["KB_API_KEY"] = "atom-test-key"
sys.path.insert(0, str(CANGKU / "AI"))

from brain import config as bcfg
bcfg.STATE = tmp / "state"
bcfg.ARCHIVE = tmp / "archive"
bcfg.STATE.mkdir(parents=True)

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond)))
    print("  [{}] {}{}".format("✓" if cond else "✗", name,
                               ("  " + str(extra)) if (extra and not cond) else ""))


from brain import atoms, discovery
from brain.session import Session

# ── 算术 ──
ok, out = atoms.calculate("3*3")
check("算术 3*3=9", ok and "= 9" in out, out)
ok, out = atoms.calculate("3+3+3")
check("算术 3+3+3=9", ok and "= 9" in out, out)
ok, out = atoms.calculate("3×3")
check("全角乘号归一化", ok and "= 9" in out, out)
ok, out = atoms.calculate("1/0")
check("除零给明确错误", not ok and "除数为零" in out, out)
ok, out = atoms.calculate("import os")
check("注入表达式被拒", not ok, out)
ok, out = atoms.calculate("2**1000000")
check("乘方算力炸弹被拒", not ok and "超限" in out, out)
ok, out = atoms.calculate("(2+3)*4")
check("括号优先级", ok and "= 20" in out, out)

# ── 比较（设计者原话示例：3×3 = 3+3+3）──
ok, out = atoms.compare("3*3 和 3+3+3")
check("比较 3*3 与 3+3+3 相等", ok and "相等" in out, out)
ok, out = atoms.compare("5 和 3")
check("比较 5>3", ok and "大于" in out, out)
ok, out = atoms.compare("苹果 和 苹果")
check("字符串退化比较", ok and "完全相同" in out, out)
ok, out = atoms.compare("只有一个")
check("比较缺边给提示", not ok, out)

# ── 时间换算 ──
from datetime import datetime
now = datetime(2026, 9, 23, 12, 0, 0)
ok, out = atoms.time_convert("换算 90分钟", now)
check("换算 90分钟", ok and "1 小时 30 分钟" in out, out)
ok, out = atoms.time_convert("现在几点", now)
check("现在几点", ok and "12:00" in out, out)
ok, out = atoms.time_convert("45分钟后是几点", now)
check("45分钟后是几点", ok and "12:45" in out, out)
ok, out = atoms.time_convert("还有多久到晚上8点", now)
check("还有多久到晚上8点", ok and "8 小时" in out, out)
ok, out = atoms.time_convert("还有多久到早上7点", now)
check("时间已过给明确提示", not ok, out)

# ── 留痕 ──
log = atoms.load_log()
check("原子操作留痕 JSONL", len(log) >= 10 and log[0]["op"] == "计算", len(log))

# ── 会话接入 ──
s = Session(api_key="atom-test-key")
r = s.handle("计算 3*3")
check("会话：计算", "= 9" in r, r)
r = s.handle("比较 3*3 和 3+3+3")
check("会话：比较", "相等" in r, r)
r = s.handle("换算 90分钟")
check("会话：换算", "1 小时 30 分钟" in r, r)
r = s.handle("查库 P5")   # 知识库没启动 → 优雅降级
check("会话：查库断连降级", "联系不上知识库" in r, r)
r = s.handle("计算")
check("会话：空表达式提示", "表达式" in r, r)

# ── 矿工 D：数值趋同（3*3、3+3+3、9 三条路径都得 9）──
atoms.calculate("9")
cands = discovery.mine_numeric_convergence()
nine = [c for c in cands if "9" in c["content"]]
check("数值趋同矿工发现 9 的等式", any("3*3" in c["content"] and "3+3+3" in c["content"]
                                   for c in nine), [c["content"] for c in nine])
check("候选类型 num_converge", all(c["kind"] == "num_converge" for c in nine))

# ── 建议队列接入（refine.add_suggestions 去重入队）──
from brain import refine
fresh = refine.add_suggestions(nine[:1])
check("趋同候选进建议队列", len(fresh) == 1 and fresh[0]["status"] == "待裁决")
q = json.loads((bcfg.STATE / "suggestions.json").read_text(encoding="utf-8"))
check("队列落盘且含 num_converge",
      any(i["kind"] == "num_converge" for i in q["items"]))
dup = refine.add_suggestions(nine[:1])
check("重复候选不去重入队", len(dup) == 0)

shutil.rmtree(tmp, ignore_errors=True)
bad = [n for n, ok in RESULTS if not ok]
print("\n== 原子操作专项：{} 通过，{} 失败 ==".format(len(RESULTS) - len(bad), len(bad)))
if bad:
    print("失败项：" + ", ".join(bad))
    raise SystemExit(1)
print("全部通过 ✓")
