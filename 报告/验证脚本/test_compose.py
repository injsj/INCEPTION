# -*- coding: utf-8 -*-
"""大工程7验收：组句器（句子组装而非剧本）+ 组合式理解（拆句路由）。"""
import json
import os
import urllib.request

KEY = os.environ.get("CANGKU_ADMIN_KEY", "")  # 密钥不入库：set CANGKU_ADMIN_KEY=...
URL = "http://127.0.0.1:8320/admin/api/inception/chat"

def ask(text):
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "X-Admin-Key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

ask("算了")
CASES = [
    "地球绕什么转",
    "怎么才能让铁生锈",
    "燃烧需要什么条件",
    "月球绕什么转",
    "铁在潮湿环境会怎样",
    "三角形内角和是多少",
    "绿色植物在光照充足时会发生什么",
    "真空能传声吗",
]
for text in CASES:
    r = ask(text)
    print("=" * 56)
    print("Q:", text)
    print("A:", r.get("reply", "")[:220])
    th = (r.get("trace") or {}).get("thinking") or []
    if th and th[0].startswith("拆句"):
        print("  [拆句]", th[0])
