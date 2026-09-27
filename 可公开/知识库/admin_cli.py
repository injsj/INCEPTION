#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地管理工具（物理访问为前提，无需密钥）。

用法：
    python admin_cli.py            进入中文交互菜单（推荐，所有输入均为普通中英文）
    python admin_cli.py init       初始化：创建管理员密钥与 AI 密钥（各显示一次）
    python admin_cli.py status     查看开关状态与服务信息

总开关关闭时 HTTP 服务完全不可用，本工具仍可正常使用（逃生通道）。"""
import json
import sys

from kb import auth, config, reqsys, store
from kb.audit import tail as log_tail, verify as log_verify


# ── 输入辅助：普通输入，程序负责转换 ──

def ask(prompt, default=""):
    tip = "（直接回车保持 {}）".format(default) if default else ""
    v = input("{} {}: ".format(prompt, tip)).strip()
    return v or default


def ask_multi(prompt):
    print(prompt)
    print("（支持多行输入，单独一行输入 END 结束）")
    lines = []
    while True:
        line = input("  | ")
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def show_entry_brief(e):
    print("  [{}] {}「{}」 类型:{} 更新:{}".format(
        config.ZONE_LABEL[e["zone"]], e["id"], e["title"], e["type"], e.get("updated_at", "")))


# ── 初始化 ──

def cmd_init():
    config.validate()
    admin_key = None
    ai_key = None
    if auth.name_exists(config.ADMIN_NAME):
        print("管理员密钥已存在，跳过。")
    else:
        admin_key = auth.add_key(config.ADMIN_NAME, "admin")
    if auth.name_exists(config.AI_NAME):
        print("AI 密钥已存在，跳过。")
    else:
        ai_key = auth.add_key(config.AI_NAME, "ai")
    store.rebuild_index()
    print("\n" + "=" * 60)
    if admin_key:
        print("管理员密钥（登录管理网页用，仅此一次显示，请保存）：")
        print("    " + admin_key)
    if ai_key:
        print("AI 密钥（交给你的 AI，仅此一次显示，请保存）：")
        print("    " + ai_key)
    print("=" * 60 + "\n")


def cmd_status():
    s = store.get_state()
    c = store.counts()
    print("总开关：{}  对外访问：{}  目录公开：{}".format(
        "开" if s["enabled"] else "关（HTTP 一切访问被拒绝）",
        "开（重启后绑定 0.0.0.0）" if s["allow_remote"] else "关（仅本机）",
        "开" if s.get("catalog_public", True) else "关"))
    print("知识数量：信任区 {}，存疑区 {}，待审核区 {}".format(c["trusted"], c["suspect"], c["pending"]))
    print("密钥：")
    for k in auth.list_keys():
        print("  - {}（{}）授权 {}".format(k["name"], k["role_name"], k["granted_ids"] or "（无）"))
    print("待审访问申请：{}，待审 AI 申请：{}".format(
        len([a for a in reqsys.list_applications() if a["status"] == "待审"]),
        len(reqsys.list_reqs("待审核"))))
    for kind in ("access", "admin"):
        ok, n, at = log_verify(kind)
        print("{} 日志：{} 条，哈希链{}".format(kind, n, "完好" if ok else "在 {} 行断裂！".format(at)))


# ── 菜单项 ──

def m_switch():
    s = store.get_state()
    print("当前：总开关{}".format("开" if s["enabled"] else "关"))
    v = ask("输入 on 打开 / off 关闭", "on" if not s["enabled"] else "off")
    store.set_state(enabled=(v.lower() == "on"))
    if v.lower() == "off":
        print("已关闭。此刻起一切 HTTP 访问（含目录、调用、AI）均返回 503。")
        print("重新打开：再运行本菜单，或用 python admin_cli.py menu。")


def m_remote():
    s = store.get_state()
    print("当前：对外访问{}".format("开" if s["allow_remote"] else "关"))
    v = ask("输入 on 允许其他机器访问 / off 仅本机", "on" if not s["allow_remote"] else "off")
    store.set_state(allow_remote=(v.lower() == "on"))
    print("已记录。绑定地址在服务启动时决定，重启 python -m kb.main 后生效。")
    if v.lower() == "on" and not config.HTTPS_CERT:
        print("提醒：未配置 HTTPS 证书（config.py 的 HTTPS_CERT），密钥建议只走可信网络。")


def m_catalog_switch():
    s = store.get_state()
    store.set_state(catalog_public=(not s.get("catalog_public", True)))
    print("目录公开：{}".format("开" if store.get_state().get("catalog_public") else "关（目录接口对匿名隐藏）"))


def m_type_add():
    print("现有类型：")
    for t in store.list_types():
        print("  {} → {}".format(t["prefix"], t["name"]))
    name = ask("类型名称（如 python）")
    prefix = ask("开头字母标记（单个英文字母，如 P）").upper()
    ok, msg = store.add_type(name, prefix)
    print(msg if not ok else "已添加：{} → {}".format(prefix, name))


def _fields_input(entry=None):
    """逐字段输入；直接回车/END 保留原值（编辑场景）。"""
    fields = {}
    prompts = [("title", "标题", False), ("summary", "简介", False),
               ("usage", "详细使用方法", True), ("experience", "详细使用经验", True),
               ("limitations", "局限性", True), ("content", "正文/示例", True)]
    for key, label, multi in prompts:
        if multi:
            print("【{}】".format(label))
            if entry:
                print("  当前: " + str(entry.get(key, ""))[:80].replace("\n", " / "))
            v = ask_multi("  输入新的{}（END 保留原值）".format(label))
        else:
            v = ask("【{}】".format(label), entry.get(key, "") if entry else "")
        if v:
            fields[key] = v
    return fields


def m_entry_add():
    types = store.list_types()
    if not types:
        print("还没有知识类型，请先添加类型。")
        return
    print("选择分区：1=信任区  2=存疑区  3=待审核区（默认 3，新知建议先入待审核区）")
    z = ask("分区编号", "3")
    zone = {"1": "trusted", "2": "suspect", "3": "pending"}.get(z, "pending")
    for i, t in enumerate(types, 1):
        print("  {}. {}".format(i, t["name"]))
    tsel = types[int(ask("类型编号", "1")) - 1]
    fields = _fields_input()
    e, msg = store.create_entry(zone, tsel["name"], fields)
    if not e:
        print("失败：" + msg)
    else:
        print("已入库：{} · {}「{}」".format(config.ZONE_LABEL[zone], e["id"], e["title"]))


def m_entry_list():
    zone = {"1": "trusted", "2": "suspect", "3": "pending"}.get(
        ask("查看哪个分区？1=信任区 2=存疑区 3=待审核区 回车=全部", ""), None)
    entries = store.list_entries(zone)
    if not entries:
        print("（空）")
    for info in entries:
        print("  {} · [{}] {}（{}）".format(
            config.ZONE_LABEL[info["zone"]], info["id"], info["title"], info["type"]))


def m_entry_edit():
    eid = ask("要修改的知识标记（如 P1）").upper()
    entry = store.get_entry(eid)
    if not entry:
        print("标记不存在")
        return
    fields = _fields_input(entry)
    if not fields:
        print("未修改任何字段。")
        return
    e, msg = store.update_entry(eid, fields)
    print("已更新。" if e else "失败：" + msg)


def m_entry_move():
    eid = ask("要移动的知识标记").upper()
    entry = store.get_entry(eid)
    if not entry:
        print("标记不存在")
        return
    show_entry_brief(entry)
    print("目标分区：1=信任区 2=存疑区 3=待审核区")
    zone = {"1": "trusted", "2": "suspect", "3": "pending"}.get(ask("编号"), "")
    if not zone:
        print("已取消。")
        return
    if zone == "trusted" and entry["zone"] == "suspect":
        for i, ex in enumerate(entry.get("experiences", [])):
            print("  经验{} [{}]: {}".format(i + 1, ex["status"], ex["note"][:50]))
        s = ask("如原理已证明，输入经验编号标记 confirmed（逗号分隔，可空）", "")
        if s:
            exps = entry.get("experiences", [])
            for i in [int(x) - 1 for x in s.split(",") if x.strip().isdigit()]:
                if 0 <= i < len(exps):
                    store.set_experience_status(eid, i, "confirmed")
                else:
                    print("  忽略越界经验编号 {}".format(i + 1))
    note = ask("移动说明（可空）")
    ok, msg = store.move_zone(eid, zone, note)
    print("已移动。" if ok else "失败：" + msg)


def m_entry_delete():
    eid = ask("要废弃的知识标记").upper()
    entry = store.get_entry(eid)
    if not entry:
        print("标记不存在")
        return
    show_entry_brief(entry)
    if ask("确认废弃？输入 yes 确认") != "yes":
        print("已取消。序号不会被回收。")
        return
    reason = ask("废弃原因（记入日志）")
    ok, msg = store.delete_entry(eid, reason)
    print("已废弃。" if ok else "失败：" + msg)


def m_experience():
    eid = ask("知识标记").upper()
    entry = store.get_entry(eid)
    if not entry:
        print("标记不存在")
        return
    print("1=追加经验记录  2=标记某条经验的状态")
    if ask("选择", "1") == "1":
        note = ask_multi("输入经验内容")
        _, msg = store.add_experience(eid, note)
        print("已追加。" if _ else "失败：" + msg)
    else:
        for i, ex in enumerate(entry.get("experiences", [])):
            print("  {}. [{}] {}".format(i + 1, ex["status"], ex["note"][:60]))
        i = int(ask("经验编号")) - 1
        st = ask("状态 unproven/confirmed/disproven", "confirmed")
        ok, msg = store.set_experience_status(eid, i, st)
        print("已标记。" if ok else "失败：" + msg)


def m_applications():
    apps = reqsys.list_applications()
    pend = [a for a in apps if a["status"] == "待审"]
    if not pend:
        print("没有待审申请。")
        return
    for a in pend:
        print("#{} {} 申请标记 {}，理由：{}（{}）".format(a["n"], a["self_name"], a["ids"], a["reason"], a["time"]))
    n = int(ask("要处理的申请编号（回车取消）", "0") or 0)
    if not n:
        return
    act = ask("approve 批准 / reject 拒绝", "approve")
    a = reqsys.get_application(n)
    if not a or a["status"] != "待审":
        print("申请不存在或已处理。")
        return
    if act == "approve":
        name = ask("为该访问方指定固定名称（唯一，不得与管理员/AI 同名）")
        if name in (config.ADMIN_NAME, config.AI_NAME) or auth.name_exists(name):
            print("名称无效或已被占用。")
            return
        key = auth.add_key(name, "reader", a["ids"])
        reqsys.resolve_application(n, "已批准")
        print("已批准。密钥仅此一次显示，请立即复制并私下转交：")
        print("    " + key)
    else:
        reqsys.resolve_application(n, "已拒绝", ask("拒绝原因"))
        print("已拒绝。")


def m_keys():
    print("现有密钥：")
    keys = auth.list_keys()
    for k in keys:
        print("  - {}（{}）授权 {} 临时授权 {}".format(
            k["name"], k["role_name"], k["granted_ids"] or "（无）", k["temp"] or "（无）"))
    print("\n1=追加授权  2=收回某标记  3=临时授权待审核知识  4=吊销访问方  5=手动创建访问方")
    act = ask("选择", "")
    if act == "1":
        name = ask("访问方固定名称")
        ids = [i.strip().upper() for i in ask("标记列表（逗号分隔）").split(",") if i.strip()]
        unknown = [i for i in ids if not store.id_exists(i)]
        if unknown:
            print("忽略不存在的标记：{}".format(unknown))
        ok = auth.grant_ids(name, [i for i in ids if store.id_exists(i)])
        print("已授权。" if ok else "访问方不存在。")
    elif act == "2":
        ok = auth.revoke_id(ask("访问方名称"), ask("标记").upper())
        print("已收回（≤1 秒生效）。" if ok else "访问方或授权不存在。")
    elif act == "3":
        name, eid = ask("访问方名称"), ask("待审核区标记").upper()
        if store.zone_of(eid) != "pending":
            print("该标记不在待审核区。")
            return
        hours = max(1, min(720, int(ask("有效小时数", "24"))))  # 钳制 1~720 小时
        ok = auth.temp_grant(name, eid, hours)
        print("已临时授权。" if ok else "访问方不存在。")
    elif act == "4":
        name = ask("要吊销的访问方名称")
        if name in (config.ADMIN_NAME, config.AI_NAME):
            print("不能吊销管理员或 AI。")
            return
        print("已吊销（≤1 秒生效，对方立即失去一切权限）。" if auth.revoke_key(name) else "访问方不存在。")
    elif act == "5":
        name = ask("固定名称")
        if auth.name_exists(name) or name in (config.ADMIN_NAME, config.AI_NAME):
            print("名称无效或已存在。")
            return
        ids = [i.strip().upper() for i in ask("授权标记（逗号分隔，可空）").split(",") if i.strip()]
        key = auth.add_key(name, "reader", ids)
        print("已创建，密钥仅此一次显示：\n    " + key)


def m_reqs():
    pend = reqsys.list_reqs("待审核")
    if not pend:
        print("没有待审申请（申请区为空）。")
        return
    for r in pend:
        print("{} [{}] {} → {}，理由:{} 证明:{}（{} 提交于 {}）".format(
            r["id"], r["action_name"], r["proposer"], r["target"] or "（新增）",
            r["reason"][:40], r["proof"][:40], r["proposer"], r["created_at"]))
    rid = ask("要处理的申请编号（如 REQ1，回车取消）")
    if not rid:
        return
    req = reqsys.get_req(rid)
    if not req or req["status"] != "待审核":
        print("不存在或已处理。")
        return
    print(json.dumps(req, ensure_ascii=False, indent=2))
    entry = store.get_entry(req["target"]) if req["target"] else None
    act = req["action"]
    done = False
    if act == "modify" and entry:
        fields = _fields_input(entry)
        if fields and ask("确认按上述内容修改 {}？yes 执行".format(req["target"])) == "yes":
            store.update_entry(req["target"], fields, source="{} 申请 {}".format(req["proposer"], rid))
            done = True
    elif act == "new":
        for i, t in enumerate(store.list_types(), 1):
            print("  {}. {}".format(i, t["name"]))
        tsel = store.list_types()[int(ask("类型编号", "1")) - 1]
        print("入库分区：1=信任区 2=存疑区 3=待审核区（默认 3）")
        zone = {"1": "trusted", "2": "suspect"}.get(ask("编号", "3"), "pending")
        fields = _fields_input()
        fields.setdefault("summary", req.get("proposal", ""))
        e, msg = store.create_entry(zone, tsel["name"], fields,
                                    source="{} 申请 {}".format(req["proposer"], rid))
        if e:
            print("已入库：{} · {}".format(config.ZONE_LABEL[zone], e["id"]))
            done = True
        else:
            print("失败：" + msg)
    elif act == "append_experience" and entry:
        note = ask_multi("经验内容（默认采用申请中的 proposal）") or req["proposal"]
        e, msg = store.add_experience(req["target"], note, source="{} 申请 {}".format(req["proposer"], rid))
        if e:
            done = True
        else:
            print("失败：" + msg)
    elif act == "move_zone" and entry:
        print("目标分区：1=信任区 2=存疑区")
        zone = {"1": "trusted", "2": "suspect"}.get(ask("编号"), "")
        if zone and store.move_zone(req["target"], zone, "申请 " + rid)[0]:
            done = True
    elif act == "suspect_to_pending" and entry:
        if entry["zone"] != "suspect":
            print("该知识已不在存疑区。")
            return
        if store.move_zone(req["target"], "pending", "申请 {}：{}".format(rid, req["reason"][:50]))[0]:
            print("已移回待审核区。人类访问方对它的常规调用已自动失效，待你最终裁决。")
            done = True
    if done:
        reqsys.resolve_req(rid, "已执行", ask("处理意见（记入申请，AI 可见）"))
        print("已执行并归档。")
    else:
        if ask("拒绝该申请？yes 拒绝") == "yes":
            reqsys.resolve_req(rid, "已拒绝", ask("拒绝理由"))
            print("已拒绝。")


def m_logs():
    kind = ask("查看 access（调用）还是 admin（管理）日志？", "access")
    n = int(ask("显示最近多少条", "30"))
    for line in log_tail(kind, n):
        print("  " + line)
    ok, cnt, at = log_verify(kind)
    print("哈希链校验：{}（{} 条）".format("完好" if ok else "第 {} 行断裂！".format(at), cnt))


def m_history():
    eid = ask("知识标记").upper()
    versions = store.list_history(eid)
    if not versions:
        print("没有历史版本。")
        return
    for i, v in enumerate(versions, 1):
        print("  {}. {}".format(i, v))
    sel = int(ask("要回滚到哪个版本（回车取消）", "0") or 0)
    if sel and 1 <= sel <= len(versions):
        ok, msg = store.rollback(eid, versions[sel - 1])
        print("已回滚。" if ok else msg)


def m_doctor():
    print("自检中……")
    n, bad = store.rebuild_index()
    print("索引重建完成：{} 条".format(n))
    for f in bad:
        print("  损坏文件：{}（请人工修复或删除）".format(f))
    s = store.get_state()
    print("开关状态 rev={}：总开关{} 对外{} 目录{}".format(
        s.get("rev"), s["enabled"], s["allow_remote"], s.get("catalog_public")))
    missing_ai = not any(k["role"] == "ai" for k in auth.list_keys())
    missing_admin = not any(k["role"] == "admin" for k in auth.list_keys())
    if missing_ai:
        print("警告：尚未创建 AI 密钥（python admin_cli.py init）")
    if missing_admin:
        print("警告：尚未创建管理员密钥（python admin_cli.py init）")
    for kind in ("access", "admin"):
        ok, cnt, at = log_verify(kind)
        print("{} 日志：{} 条，{}".format(kind, cnt, "完好" if ok else "第 {} 行断裂！".format(at)))
    print("自检完成。")


MENU = [
    ("服务总开关（关闭后禁止一切 HTTP 访问）", m_switch),
    ("对外访问开关（给别人用，重启生效）", m_remote),
    ("目录公开开关", m_catalog_switch),
    ("添加知识类型 + 开头字母", m_type_add),
    ("知识录入（入待审核区）", m_entry_add),
    ("知识列表", m_entry_list),
    ("修改知识", m_entry_edit),
    ("知识移区（含存疑→信任的经验确认）", m_entry_move),
    ("废弃知识", m_entry_delete),
    ("经验管理（追加/标记状态）", m_experience),
    ("访问申请审批（人类访问方）", m_applications),
    ("密钥与授权管理", m_keys),
    ("AI 申请处理（申请区 REQ）", m_reqs),
    ("查看日志", m_logs),
    ("历史版本与回滚", m_history),
    ("系统自检", m_doctor),
]


def menu():
    config.validate()
    while True:
        print("\n══════ 知识库管理 ══════  管理员:{}  AI:{} ══════".format(config.ADMIN_NAME, config.AI_NAME))
        s = store.get_state()
        print("当前：总开关{}｜对外{}｜目录{}".format(
            "开" if s["enabled"] else "关", "开" if s["allow_remote"] else "仅本机",
            "公开" if s.get("catalog_public", True) else "隐藏"))
        for i, (label, _) in enumerate(MENU, 1):
            print("  {:>2}. {}".format(i, label))
        print("   0. 退出")
        sel = ask("请选择")
        if sel == "0":
            break
        if sel.isdigit() and 1 <= int(sel) <= len(MENU):
            try:
                MENU[int(sel) - 1][1]()
            except Exception as e:
                print("操作出错：{}".format(e))
        else:
            print("无效选择。")


if __name__ == "__main__":
    config.validate()
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "init":
            cmd_init()
        elif cmd == "status":
            cmd_status()
        elif cmd in ("menu", "-i"):
            menu()
        else:
            print("未知命令：{}（可用：init / status / menu）".format(cmd))
    else:
        menu()
