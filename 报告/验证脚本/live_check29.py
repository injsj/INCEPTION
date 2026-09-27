# -*- coding: utf-8 -*-
"""变更 #29 活体抽验：在专用「验收29」切片里聊用户举报的六句原话，验完删切片+
清回收站；第 6 句（纠正）会真实打 REQ，验完定位并删除该测试 REQ 文件。零痕迹。"""
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8320"
H = {"X-Admin-Key": os.environ.get("CANGKU_ADMIN_KEY", "")}  # 密钥不入库：set CANGKU_ADMIN_KEY=...
REQ_DIR = Path(r"D:\cangku\知识库\data\requests")


def req(method, url, **kw):
    for i in range(3):
        r = getattr(requests, method)(url, timeout=15, **kw)
        if r.status_code != 429:
            return r
        if i < 2:
            time.sleep(65)
    return r


sid = None
test_req = None
try:
    r = req("post", BASE + "/admin/api/inception/slices/new",
            headers=H, json={"name": "验收29"})
    sid = r.json()["active"]
    print("测试切片:", sid)

    cases = [
        # (原话, 回复必须含, 回复不许含, 说明)
        ("你是什么", ["INCEPTION"], ["目录里", "我还不能理解"],
         "自我认知（原举报①：被 search 模板劫去搜目录）"),
        ("你的名字是什么", ["INCEPTION"], ["目录里", "我还不能理解"],
         "自我认知-名字（原举报①变体）"),
        ("铁器碰到水在空气中长时间这样会如何",
         ["接触"], ["器水", "我还不能理解"],
         "介词短语事实认出+分支试算（原举报③场景）"),
        ("环境潮湿但是无氧气铁器放里面会怎样",
         ["生锈"], ["不能生存", "人"],
         "主体锚定否决：只报铁结论，不附送人生存（原举报③）"),
        ("铁不会生锈吗", ["生锈"], ["听懂了，你在纠正我"],
         "不误伤：只有「不」没有「不是」，走正常问答"),
        ("铁生锈的条件不是氧气加上水才能生锈吗",
         ["听懂了，你在纠正我", "氧气", "水"], ["我还不能理解"],
         "纠正通路：打成 REQ 等你裁决（原举报②）"),
    ]
    nok = 0
    for text, must, mustnt, label in cases:
        r = req("post", BASE + "/admin/api/inception/chat", headers=H,
                json={"text": text})
        d = r.json()
        reply = d.get("reply", "")
        thinking = d.get("trace", {}).get("thinking", [])
        ok = (all(m in reply for m in must)
              and all(m not in reply for m in mustnt) and d.get("ok"))
        print("\n── {}：{}".format(label, text))
        print("回复:", reply[:170].replace("\n", " / "))
        print("思考链尾:", " | ".join(thinking[-2:])[:190])
        print("判定:", "✓" if ok else "✗")
        nok += 1 if ok else 0
    print("\n== 活体抽验 {}/{} 符合预期 ==".format(nok, len(cases)))

    # 定位纠正句打出的测试 REQ（proposal 含「条件补充（用户纠正）」的最新一条）
    cands = sorted(REQ_DIR.glob("REQ*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands[:5]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if "条件补充（用户纠正）" in (d.get("proposal") or ""):
            test_req = p
            print("纠正句打出的申请:", p.name, "→", (d.get("proposal") or "")[:60])
            break
finally:
    if sid:
        req("post", BASE + "/admin/api/inception/slices/del", headers=H,
            json={"id": sid})
        t = req("post", BASE + "/admin/api/inception/slices/trash", headers=H,
                json={}).json()
        for it in t.get("items", []):
            if it["name"].startswith("验收29"):
                req("post", BASE + "/admin/api/inception/slices/purge",
                    headers=H, json={"id": it["sid"]})
        print("测试切片已删除并清出回收站")
    if test_req and test_req.exists():
        test_req.unlink()
        print("测试 REQ 文件已删除:", test_req.name)
