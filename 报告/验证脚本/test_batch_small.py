# -*- coding: utf-8 -*-
"""小修批端到端验收：问候组合解析 + 思考链随历史存档 + 对账人话。"""
import json
import os
import urllib.request

KEY = os.environ.get("CANGKU_ADMIN_KEY", "")  # 密钥不入库：set CANGKU_ADMIN_KEY=...
BASE = "http://127.0.0.1:8320/admin/api/inception"

def call(path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={
        "Content-Type": "application/json", "X-Admin-Key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

print("== 1. 问候组合式解析（此前全答不了）==")
for t in ["你好呀", "早", "在么", "各位好"]:
    r = call("/chat", {"text": t})
    ok = "你好" in r.get("reply", "") and "无法" not in r.get("reply", "")
    print(("✓" if ok else "✗"), t, "→", r.get("reply", "")[:40])

print("== 2. 思考链随历史存档 ==")
call("/chat", {"text": "算了"})
r = call("/chat", {"text": "地球绕什么转"})
live_th = (r.get("trace") or {}).get("thinking") or []
print("  实时回复思考链步数:", len(live_th))
h = call("/history?n=4")
ai_items = [i for i in h.get("items", []) if i.get("role") == "ai"]
last = ai_items[-1] if ai_items else {}
hist_th = last.get("thinking") or []
print("  历史回放思考链步数:", len(hist_th))
print("  一致" if hist_th == live_th else "  ✗ 不一致！", "" if hist_th == live_th else hist_th)

print("== 3. 黑话检查 ==")
r = call("/chat", {"text": "如果明天下雨提醒我带伞"})
print("  (提醒类输入 smoke)", r.get("reply", "")[:60])
bad = [w for w in ("对账", "置信") if w in r.get("reply", "")]
print("  残留黑话:", bad if bad else "无")
