# -*- coding: utf-8 -*-
"""变更 #30 活体抽验：
① 闲聊一句，观察回复尾部是否挂载「顺带：」空闲推进（真实队列 E1 是推演欠账）；
② 问「铁在潮湿环境会怎样」，确认正常问答未被传动轴干扰；
③ 查管理端日志，确认快照模块登记记录；
④ 事后核对 session.json 里 E1 的 pushed 日期戳/结案状态。
验完删除「验收30」切片并清回收站，零痕迹。"""
import json
import os
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8320"
H = {"X-Admin-Key": os.environ.get("CANGKU_ADMIN_KEY", "")}  # 密钥不入库：set CANGKU_ADMIN_KEY=...
SESSION = Path(r"D:\cangku\AI\state\session.json")


def req(method, url, **kw):
    for i in range(3):
        r = getattr(requests, method)(url, timeout=15, **kw)
        if r.status_code != 429:
            return r
        if i < 2:
            time.sleep(65)
    return r


sid = None
nok = 0
try:
    r = req("post", BASE + "/admin/api/inception/slices/new",
            headers=H, json={"name": "验收30"})
    sid = r.json()["active"]
    print("测试切片:", sid)

    # ① 空闲推进：闲聊一句，看尾部「顺带：」
    r = req("post", BASE + "/admin/api/inception/chat", headers=H,
            json={"text": "你好"})
    d = r.json()
    reply = d.get("reply", "")
    thinking = d.get("trace", {}).get("thinking", [])
    pushed = "顺带：" in reply
    print("\n── ① 空闲推进（你好）")
    print("回复:", reply[:260].replace("\n", " / "))
    print("思考链尾:", " | ".join(thinking[-3:])[:220])
    print("判定:", "✓ 挂载了推进附加" if pushed else "○ 未挂载（E1 推不出结论时也属设计行为）")
    nok += 1  # 两种走向都合规，此步只做观察记录

    # ② 正常问答不受干扰
    r = req("post", BASE + "/admin/api/inception/chat", headers=H,
            json={"text": "铁在潮湿环境会怎样"})
    d = r.json()
    reply = d.get("reply", "")
    thinking = d.get("trace", {}).get("thinking", [])
    ok = ("锈" in reply) and ("我还不能理解" not in reply) and d.get("ok")
    print("\n── ② 正常问答（铁在潮湿环境会怎样）")
    print("回复:", reply[:260].replace("\n", " / "))
    print("思考链尾:", " | ".join(thinking[-3:])[:220])
    print("判定:", "✓" if ok else "✗")
    nok += 1 if ok else 0

    # ③ 管理端日志里的快照记录（alog 写 admin.log，须 type=admin）
    r = req("get", BASE + "/admin/api/logs", headers=H,
            params={"type": "admin", "n": 500})
    lines = r.json().get("lines") or []
    hits = [l for l in lines if ("快照" in l or "backup" in l.lower())][:3]
    has_backup = bool(hits)
    print("\n── ③ 快照日志登记")
    for h in hits:
        print("  ·", json.dumps(h, ensure_ascii=False)[:160])
    print("判定:", "✓" if has_backup else "✗")
    nok += 1 if has_backup else 0

    print("\n== 活体抽验 {}/3 符合预期（①为观察项） ==".format(nok))
finally:
    if sid:
        req("post", BASE + "/admin/api/inception/slices/del", headers=H,
            json={"id": sid})
        t = req("post", BASE + "/admin/api/inception/slices/trash", headers=H,
                json={}).json()
        for it in t.get("items", []):
            if it["name"].startswith("验收30"):
                req("post", BASE + "/admin/api/inception/slices/purge",
                    headers=H, json={"id": it["sid"]})
        print("测试切片已删除并清出回收站")

# ④ 事后核对 E1 状态（服务外直读文件）
try:
    s = json.loads(SESSION.read_text(encoding="utf-8"))
    e1 = next((e for e in s.get("events", []) if e.get("id") == "E1"), None)
    if e1:
        print("\n── ④ E1 事后状态: state={} pushed={} result={}".format(
            e1.get("state"), e1.get("pushed"),
            str(e1.get("result"))[:80]))
    print("recent_acts 尾3:", (s.get("recent_acts") or [])[-3:])
except Exception as e:
    print("④ 读取 session.json 失败:", e)
