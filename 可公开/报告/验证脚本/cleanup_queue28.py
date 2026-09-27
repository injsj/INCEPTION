# -*- coding: utf-8 -*-
"""旧账清理（变更 #28 用户拍板 A 方案：挑达标的打 REQ，不够格作废）。"""
import sys
sys.path.insert(0, r"D:\cangku\AI")
from brain import refine

# 达标名单：S12/S49（捷径候选规则）、S10（对账晋升）；S46 已是 REQ35，挂接不重复打
QUALIFY = ["S12", "S49", "S10"]
LINK_EXISTING = {"S46": "REQ35"}

q = refine._load_queue()
by_id = {it["id"]: it for it in q["items"]}

class _FakeSess:
    api_key = None   # 走 config.load_api_key()

fresh = [by_id[s] for s in QUALIFY if s in by_id and by_id[s]["status"] == "待裁决"]
done = refine.auto_submit_reqs(fresh, _FakeSess())
print("补打 REQ:", done or "（无——可能断连/达上限）")

q = refine._load_queue()
for it in q["items"]:
    if it["id"] in LINK_EXISTING and it["status"] == "待裁决":
        it["status"] = "已提交REQ"
        it["req"] = LINK_EXISTING[it["id"]]
        print("挂接已有申请: {} -> {}".format(it["id"], LINK_EXISTING[it["id"]]))
    elif it["status"] == "待裁决" and it["id"] not in QUALIFY:
        it["status"] = "已作废"
refine._save_queue(q)

import collections
c = collections.Counter(it["status"] for it in q["items"])
print("清理后状态分布:", dict(c))
