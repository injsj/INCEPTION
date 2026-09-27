# -*- coding: utf-8 -*-
"""变更 #28 活体抽验：在专用「验收28」切片里聊四句，验完删切片+清空回收站，零痕迹。"""
import json
import os
import sys
import time

import requests

BASE = "http://127.0.0.1:8320"
H = {"X-Admin-Key": os.environ.get("CANGKU_ADMIN_KEY", "")}  # 密钥不入库：set CANGKU_ADMIN_KEY=...


def req(method, url, **kw):
    for i in range(3):
        r = getattr(requests, method)(url, timeout=15, **kw)
        if r.status_code != 429:
            return r
        if i < 2:
            time.sleep(65)
    return r


sid = None
try:
    r = req("post", BASE + "/admin/api/inception/slices/new",
            headers=H, json={"name": "验收28"})
    sid = r.json()["active"]
    print("测试切片:", sid)

    cases = [
        ("如果环境潮湿，铁会怎样", ["在你给的「环境潮湿」条件下", "生锈"], "嵌套条件前缀+推演"),
        ("铁会感冒吗", ["感冒", "生锈"], "结构类比跨域移植"),
        ("植物会生病吗", ["生病"], "结构类比同域锚点"),
        ("因为环境潮湿所以铁会生锈", [], "句内双标因果（看思考链）"),
    ]
    nok = 0
    for text, must, label in cases:
        r = req("post", BASE + "/admin/api/inception/chat", headers=H, json={"text": text})
        d = r.json()
        reply = d.get("reply", "")
        thinking = d.get("trace", {}).get("thinking", [])
        hit = all(m in reply or any(m in t for t in thinking) for m in must)
        print("\n── {}：{}".format(label, text))
        print("回复:", reply[:150].replace("\n", " / "))
        print("思考链尾:", " | ".join(thinking[-3:])[:200])
        print("判定:", "✓" if (hit and d.get("ok")) else "✗")
        nok += 1 if (hit and d.get("ok")) else 0
    print("\n== 活体抽验 {}/{} 符合预期 ==".format(nok, len(cases)))
finally:
    if sid:
        req("post", BASE + "/admin/api/inception/slices/del", headers=H, json={"id": sid})
        t = req("post", BASE + "/admin/api/inception/slices/trash", headers=H, json={}).json()
        for it in t.get("items", []):
            if it["name"].startswith("验收28"):
                req("post", BASE + "/admin/api/inception/slices/purge",
                    headers=H, json={"id": it["sid"]})
        print("测试切片已删除并清出回收站")
