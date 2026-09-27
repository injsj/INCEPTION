# -*- coding: utf-8 -*-
"""批二漏洞修复后复测：极性分流（逆否路径）+ 原五问不回归。"""
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
    ("燃烧需要什么条件", "逆否：氧气不足会熄灭→要避免；且补检索给三条件"),
    ("怎么才能让铁生锈", "正面路径不回归：只缺环境=潮湿"),
    ("怎么才能让铁不生锈", "阻止意图：G18变避免对象"),
    ("绿色植物进行光合作用需要什么", "检索模板不回归"),
    ("十乘十等于多少，再告诉我地球绕什么转", "拆解不回归"),
    ("地球绕什么转", "批一正向桥不回归"),
]

for text, expect in CASES:
    r = ask(text)
    print("=" * 60)
    print("Q:", text)
    print("期望:", expect)
    print("A:", r.get("reply", "")[:500])
    th = (r.get("trace") or {}).get("thinking") or []
    for i, t in enumerate(th, 1):
        print("  {}. {}".format(i, t))
