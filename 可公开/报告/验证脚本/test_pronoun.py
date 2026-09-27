# -*- coding: utf-8 -*-
"""指代回填端到端验收：先说铁→再问「它」→应绑到铁。"""
import json
import os
import urllib.request

KEY = os.environ.get("CANGKU_ADMIN_KEY", "")  # 密钥不入库：set CANGKU_ADMIN_KEY=...
BASE = "http://127.0.0.1:8320/admin/api/inception"

def ask(text):
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(BASE + "/chat", data=body, headers={
        "Content-Type": "application/json", "X-Admin-Key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

ok = lambda c, msg: print(("✓" if c else "✗"), msg)

ask("算了")
r1 = ask("怎么才能让铁生锈")
print("  铺垫:", r1["reply"][:40].replace("\n", " "))

r2 = ask("那它的条件齐全了吗")
th = (r2.get("trace") or {}).get("thinking") or []
bound = any("铁" in s and ("理解" in s or "明指" in s) for s in th)
ok(bound, "指代绑定进思考链：" + (th[1][:50] if len(th) > 1 else str(th)))
ok("铁" in r2["reply"] or "生锈" in r2["reply"], "回复围绕铁/生锈：" + r2["reply"][:60].replace("\n", " "))

r3 = ask("这个需要注意什么")
th3 = (r3.get("trace") or {}).get("thinking") or []
print("  「这个」案例思考链:", th3[:3])

r4 = ask("你好")
ok("你好" in r4["reply"], "问候不被指代干扰")

print("指代验收完毕")
