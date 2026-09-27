# -*- coding: utf-8 -*-
"""切片式聊天端到端验收：新建/切换/合并/历史可见范围。"""
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

ok = lambda c, msg: print(("✓" if c else "✗"), msg)

# 1. 初始：只有主切片
d = call("/slices")
ok(d["ok"] and "main" in [s["id"] for s in d["slices"]], "初始有主切片 active=" + d["active"])

# 2. 主切片说一句话（留证据）
call("/chat", {"text": "地球绕什么转"})

# 3. 新建切片「化学」，自动切为活动
d = call("/slices/new", {"name": "化学"})
new_id = d["active"]
ok(new_id != "main" and d["slices"][-1]["name"] == "化学", "新建切片并激活 id=" + new_id)

# 4. 新切片里说话
call("/chat", {"text": "铁在潮湿环境会怎样"})

# 5. 默认历史只含新切片（看不到主切片的「地球」）
h = call("/history?n=50")
texts = [i["text"] for i in h["items"]]
ok(any("潮湿" in t for t in texts) and not any("地球绕" in t for t in texts),
   "新切片视野隔离（看不到主切片）")

# 6. 合并主切片 → 两边都看到
call("/slices/merged", {"ids": ["main"]})
h = call("/history?n=50")
texts = [i["text"] for i in h["items"]]
ok(any("地球绕" in t for t in texts) and any("潮湿" in t for t in texts),
   "合并后两片记录同见")

# 7. 取消合并 → 又隔离
call("/slices/merged", {"ids": []})
h = call("/history?n=50")
texts = [i["text"] for i in h["items"]]
ok(not any("地球绕" in t for t in texts), "取消合并恢复隔离")

# 8. 切回主切片，新话落回主切片
call("/slices/active", {"id": "main"})
r = call("/chat", {"text": "十乘十等于多少"})
h = call("/history?n=2")
ok(h["items"][-1].get("slice") == "main", "切回后新话落回主切片")

# 9. 越界防护：不存在的切片
d = call("/slices/active", {"id": "nope"})
ok(not d["ok"], "不存在切片被拒")

# 10. 切片重命名
d = call("/slices/rename", {"id": new_id, "name": "化学实验室"})
ok(any(s["name"] == "化学实验室" for s in d["slices"]), "重命名生效")

print("切片验收完毕")
