# -*- coding: utf-8 -*-
"""批二端到端复测：反问桥(认事实)、拆解、检索不回归、批一不回归。"""
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

# 先清 pending
ask("算了")

CASES = [
    ("怎么才能让铁生锈", "反问桥：应认出金属=铁/环境=潮湿，直接给结论"),
    ("绿色植物进行光合作用需要什么", "需要类：走检索或反问桥均可，但不能乱"),
    ("十乘十等于多少，再告诉我地球绕什么转", "拆解：两子句都应有答案"),
    ("燃烧需要什么条件", "检索不回归：应给知识库内容"),
    ("地球绕什么转", "批一正向问答桥不回归"),
]

for text, expect in CASES:
    r = ask(text)
    print("=" * 60)
    print("Q:", text)
    print("期望:", expect)
    print("A:", r.get("reply", "")[:400])
    th = (r.get("trace") or {}).get("thinking") or []
    print("思考链:")
    for i, t in enumerate(th, 1):
        print("  {}. {}".format(i, t))
    print("act={} decision={}".format((r.get("trace") or {}).get("act"),
                                        (r.get("trace") or {}).get("decision")))
