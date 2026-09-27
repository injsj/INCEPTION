# -*- coding: utf-8 -*-
"""变更 #10 预测闭环专项验证（状态重定向到临时目录，不碰真实数据）。"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

CANGKU = Path(r"D:\cangku")
tmp = Path(tempfile.mkdtemp(prefix="predtest_"))
os.environ["KB_API_KEY"] = "闭环测试假密钥"
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


from brain import predict
from brain.session import Session

s = Session(api_key="闭环测试假密钥")

# 1) 推演 → 结论自动存为预测
r = s.handle("推演 饥饿极高 无捍卫亲友意念 友人在场")
pend = predict.pending()
check("推演产出预测并存档", len(pend) == 1 and pend[0]["id"] == "Y1", pend)
check("回复告知预测编号与验证方式", "Y1" in r and "验证" in r)
check("预测带规则链", pend and pend[0]["rules"] == ["R1", "R2", "R3"], pend[0]["rules"] if pend else None)

# 2) 无提醒触发时不打扰
r2 = s.handle("你好")
check("无提醒到期时不出现对账提示", "对账" not in r2, r2)

# 3) 提醒到期 → 自动带出待验证清单
from datetime import datetime, timedelta
past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
s._add_event("提醒", "用户", "提醒", obj="喝水", time=past)
r3 = s.handle("你好")
check("提醒到期触发对账提示", "到时间了" in r3 and "待验证" in r3 and "Y1" in r3, r3)

# 4) 验证命中 → 状态翻转 + 规则链计数 + 留痕
r4 = s.handle("验证 Y1 命中")
preds = json.loads((bcfg.STATE / "predictions.json").read_text(encoding="utf-8"))["items"]
counts = json.loads((bcfg.STATE / "rule_reconcile.json").read_text(encoding="utf-8"))
check("验证指令被接受", "已验证命中" in r4, r4)
check("预测状态翻转", preds[0]["status"] == "已验证-命中" and preds[0]["verified_at"])
check("规则链各记一次命中", all(counts.get(r, {}).get("hit") == 1 for r in ("R1", "R2", "R3")), counts)

# 5) 重复验证被拒
r5 = s.handle("验证 Y1 命中")
check("重复验证被拒", "已经验证过" in r5, r5)

# 6) 落空路径 + 格式容错
s.handle("推演 受力平衡")
r6 = s.handle("验证 Y2 落空")
counts = json.loads((bcfg.STATE / "rule_reconcile.json").read_text(encoding="utf-8"))
check("落空路径记 miss", counts.get("R7", {}).get("miss") == 1, counts)
r7 = s.handle("验证一下")
check("格式不对给提示", "验证格式" in r7, r7)
r8 = s.handle("验证 Y99 命中")
check("不存在编号被拒", "没有找到" in r8, r8)

# 7) weights.log 哈希链完好
from brain import stats
ok, n = stats.verify_log()
check("weights.log 哈希链完好", ok and n >= 2, (ok, n))

shutil.rmtree(tmp, ignore_errors=True)
bad = [n for n, ok in RESULTS if not ok]
print("\n== 闭环专项：{} 通过，{} 失败 ==".format(len(RESULTS) - len(bad), len(bad)))
if bad:
    print("失败项：" + ", ".join(bad))
    raise SystemExit(1)
print("全部通过 ✓")
