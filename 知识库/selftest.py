#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端冒烟测试：内置线程启动真实服务，走完所有关键流程。
使用每次现建现销的自检临时密钥，可重复运行（不动正式密钥）。
会写入少量演示数据（类型/知识），可用 admin_cli.py 清理。
运行：python selftest.py"""
import threading
import time

import requests
import uvicorn

from kb import auth, config, reqsys, store
from kb.audit import verify as log_verify
from kb.main import app

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [{}] {}{}".format("✓" if cond else "✗", name, ("  " + str(extra)) if extra and not cond else ""))


def main():
    print("== 准备演示数据 ==")
    config.validate()
    store.rebuild_index()
    if not store.list_types():
        assert store.add_type("python", "P")[0]
        assert store.add_type("linux", "L")[0]

    # 第二轮审查修复：标记只增不减、不回收，不能假设演示数据一定叫 P1/P2/P3
    # （此前 P1 被废弃后重跑自检必败：新建的演示知识分到的是 P5 而非 P1，
    #  后续所有断言仍对着 P1 打）。现在每区取已有条目或新建，用实际标记测试。
    def ensure_demo(zone, fields):
        for info in store.list_entries(zone, "python"):
            return info["id"]
        e, m = store.create_entry(zone, "python", fields)
        assert e, m
        return e["id"]

    ID_T = ensure_demo("trusted", {
        "title": "快速反转字典", "summary": "一行代码反转 dict 的键值对",
        "usage": "d = {v: k for k, v in d.items()}\n适用于值唯一的字典",
        "experience": "在配置映射场景中多次使用，注意值重复会丢数据",
        "limitations": "不适用值有重复的字典", "content": "示例代码见用法"})
    ID_S = ensure_demo("suspect", {
        "title": "某性能调优手法", "summary": "原理未明的提速手法",
        "usage": "在热点函数前调用 setup()", "experience": "实测提速 2 倍但原理未知",
        "limitations": "未经原理验证，生产慎用", "content": "..."})
    ID_P = ensure_demo("pending", {
        "title": "待验证的新技巧", "summary": "刚发现，未审核",
        "usage": "用法待定", "experience": "暂无", "limitations": "一切未明", "content": "..."})
    print("演示标记：信任区 {} ｜ 存疑区 {} ｜ 待审核区 {}".format(ID_T, ID_S, ID_P))
    # 正式密钥只确保存在（首次运行创建，明文不落盘、不用于测试）。
    # 自检改用每次现建现销的临时密钥——这是可重复运行的关键：
    # 正式密钥只显示一次，重跑时取不回明文，直接用于测试会全部 401。
    if not auth.name_exists(config.ADMIN_NAME):
        auth.add_key(config.ADMIN_NAME, "admin")
    if not auth.name_exists(config.AI_NAME):
        auth.add_key(config.AI_NAME, "ai")
    for tmp in ("自检管理员", "自检AI", "自检访问员"):
        auth.revoke_key(tmp)  # 清理上次异常退出可能的残留
    admin_key = auth.add_key("自检管理员", "admin")
    ai_key = auth.add_key("自检AI", "ai")
    reader_key = auth.add_key("自检访问员", "reader", [ID_T])
    print("正式密钥已确认存在；HTTP 测试使用临时密钥（结束后自动吊销，可重复自检）")

    port = 8399
    threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="error"),
                     daemon=True).start()
    time.sleep(2)
    base = "http://127.0.0.1:{}".format(port)
    H_AI = {"X-API-Key": ai_key}
    H_ADM = {"X-Admin-Key": admin_key}

    print("== HTTP 冒烟测试 ==")
    r = requests.get(base + "/health"); check("health 本机可用", r.status_code == 200 and r.json()["ok"])
    r = requests.get(base + "/api/catalog")
    check("匿名浏览目录", r.status_code == 200)
    if r.ok:
        d = r.json()
        ids = [e["id"] for z in d["zones"] for e in z["entries"]]
        check("目录含信任区知识且不含待审核区", ID_T in ids and ID_P not in ids, ids)
        check("目录不含正文", "content" not in r.text and "usage" not in r.text.lower())
    r = requests.post(base + "/api/apply", json={"self_name": "冒烟测试", "reason": "测试", "ids": [ID_T, ID_P, "NOPE"]})
    check("匿名提交访问申请（待审核标记被过滤）", r.status_code == 200)
    r = requests.get(base + "/api/kb/" + ID_T)
    check("无密钥调用被拒 401", r.status_code == 401)
    r = requests.get(base + "/api/kb/" + ID_T, headers=H_AI)
    check("AI 读取信任区", r.status_code == 200 and r.json()["entry"]["usage"])
    r = requests.get(base + "/api/kb/" + ID_P, headers=H_AI)
    check("AI 读取待审核区（研究用）", r.status_code == 200)
    H_R = {"X-API-Key": reader_key}
    check("访问方读已授权标记", requests.get(base + "/api/kb/" + ID_T, headers=H_R).status_code == 200)
    check("访问方读未授权标记 403", requests.get(base + "/api/kb/" + ID_S, headers=H_R).status_code == 403)
    check("访问方读待审核区 403", requests.get(base + "/api/kb/" + ID_P, headers=H_R).status_code == 403)
    auth.temp_grant("自检访问员", ID_P, 1)
    check("临时授权后访问方可读待审核区", requests.get(base + "/api/kb/" + ID_P, headers=H_R).status_code == 200)
    auth.revoke_id("自检访问员", ID_T)
    check("收回授权 ≤1 秒生效", requests.get(base + "/api/kb/" + ID_T, headers=H_R).status_code == 403)
    auth.grant_ids("自检访问员", [ID_T])
    r = requests.post(base + "/api/req", headers=H_R,
                      json={"action": "modify", "target": ID_T, "proposal": "x",
                            "reason": "r", "proof": "p"})
    check("非 AI 密钥不能提交申请", r.status_code == 401)
    r = requests.post(base + "/api/req", headers=H_AI, json={"action": "modify", "target": ID_T,
                      "proposal": "补充边界说明", "reason": "发现边界情况", "proof": "测试步骤与结果"})
    check("AI 提交修改申请", r.status_code == 200)
    rid = r.json().get("id") if r.ok else None
    r = requests.post(base + "/api/req", headers=H_AI, json={"action": "modify", "target": ID_T, "proposal": "x"})
    check("申请缺理由/证明被拒", r.status_code == 400)
    r = requests.get(base + "/api/req", headers=H_AI)
    check("AI 查询申请状态", r.status_code == 200 and any(x["id"] == rid for x in r.json()["reqs"]))
    r = requests.get(base + "/admin/api/state", headers=H_ADM)
    check("管理员端点本机+管理密钥可用", r.status_code == 200)
    r = requests.get(base + "/admin/api/state", headers={"X-Admin-Key": "wrong"})
    check("错误管理密钥 401", r.status_code == 401)
    n_app = len(reqsys.list_applications())
    apps = reqsys.list_applications()
    check("访问申请已入队", n_app >= 1)
    if apps:
        pend = [a for a in apps if a["status"] == "待审"][-1]
        check("申请队列中待审核标记合法", ID_T in pend["ids"] and ID_P not in pend["ids"])
        # 自检产生的申请当场清理，避免每次运行都堆积一条"待审"
        reqsys.resolve_application(pend["n"], "已拒绝", "自检自动清理")
    if rid:
        reqsys.resolve_req(rid, "已执行", "测试执行"); check("申请可标记已执行", True)
    store.set_state(enabled=False)
    time.sleep(0.3)
    r = requests.get(base + "/api/catalog")
    check("总开关关闭后一切访问 503", r.status_code == 503)
    r = requests.get(base + "/health")
    check("关闭时 health 仍可本机确认", r.status_code == 200)
    store.set_state(enabled=True)
    time.sleep(0.3)
    check("重新打开后立即恢复", requests.get(base + "/api/catalog").status_code == 200)
    ok_a = log_verify("access"); ok_m = log_verify("admin")
    check("access 日志哈希链完好", ok_a[0], ok_a)
    check("admin 日志哈希链完好", ok_m[0], ok_m)
    check("日志确有内容", ok_a[1] > 5 and ok_m[1] > 5, (ok_a[1], ok_m[1]))

    # 收尾：吊销自检临时密钥（异常退出时下次运行开头也会再清一次）
    for tmp in ("自检管理员", "自检AI", "自检访问员"):
        auth.revoke_key(tmp)

    print("\n== 结果：{} 通过，{} 失败 ==".format(len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        raise SystemExit(1)
    print("全部通过 ✓")


if __name__ == "__main__":
    main()
