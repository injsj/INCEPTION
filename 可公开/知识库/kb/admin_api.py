# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【拍板】裁决者接口 v1=HumanAdjudicator：审批中心/移区/授权（规格 §8）
# 【施工方】REST 实现与批准入库表单闭环（变更 #28）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""管理端点：全部仅本机可访问（127.0.0.1），且需管理员密钥。
硬隔离写死在代码里：即使对外访问开启，网络请求也永远到不了这里。"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from . import auth, config, reqsys, store
from .audit import admin as alog, tail as log_tail, verify as log_verify

router = APIRouter(prefix="/admin")


def admin_guard(request: Request, x_admin_key: str = Header(default="", alias="X-Admin-Key")):
    ip = request.client.host if request.client else ""
    if ip not in config.LOCAL_HOSTS:
        raise HTTPException(403, "管理端点仅允许本机访问")
    rec = auth.find(x_admin_key)
    if not rec or rec["role"] != "admin":
        raise HTTPException(401, "管理密钥无效")
    return rec


# ── 开关与总览 ──

@router.get("/api/state")
def get_state(_=Depends(admin_guard)):
    s = store.get_state()
    return {"ok": True, "enabled": s["enabled"], "allow_remote": s["allow_remote"],
            "catalog_public": s["catalog_public"], "rev": s.get("rev", 0)}


@router.post("/api/state")
def set_state(body: dict, _=Depends(admin_guard)):
    # 只转发请求体里实际出现的开关；缺失的键绝不下发（修复：网页单键切换会把其余开关静默关掉）
    s = store.set_state(**{k: body[k] for k in ("enabled", "allow_remote", "catalog_public")
                           if k in body})
    note = ""
    if "allow_remote" in body:
        note = "注意：对外访问开关在重启服务后生效（绑定地址在启动时决定）"
    return {"ok": True, "state": {k: s[k] for k in ("enabled", "allow_remote", "catalog_public")},
            "note": note}


@router.get("/api/overview")
def overview(_=Depends(admin_guard)):
    c = store.counts()
    return {"ok": True, "counts": c,
            "applications_pending": len([a for a in reqsys.list_applications() if a["status"] == "待审"]),
            "reqs_pending": len(reqsys.list_reqs("待审核")),
            "types": store.list_types(), "keys": auth.list_keys()}


# ── 访问申请审批 ──

@router.get("/api/applications")
def applications(_=Depends(admin_guard)):
    return {"ok": True, "applications": reqsys.list_applications()}


@router.post("/api/applications/{n}/approve")
def approve(n: int, body: dict, _=Depends(admin_guard)):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "必须为该访问方指定固定名称")
    if name in (config.ADMIN_NAME, config.AI_NAME):
        raise HTTPException(400, "该名称为管理员或 AI 保留")
    if auth.name_exists(name):
        raise HTTPException(400, "名称「{}」已被使用，固定名称必须唯一".format(name))
    a = reqsys.get_application(n)
    if not a or a["status"] != "待审":
        raise HTTPException(404, "申请不存在或已处理")
    # 第二轮审查 L8：先建密钥再改申请状态，避免"已批准但没发出密钥"的中间态
    key = auth.add_key(name, "reader", a["ids"])
    if not key:
        raise HTTPException(400, "创建密钥失败")
    if not reqsys.resolve_application(n, "已批准", body.get("note", "")):
        auth.revoke_key(name)  # 状态已被并发处理：回收刚建的密钥，不留孤儿
        raise HTTPException(409, "申请已被其他操作处理")
    return {"ok": True, "name": name, "granted_ids": a["ids"], "key": key,
            "warning": "密钥仅此一次显示，请立即复制并私下转交"}


@router.post("/api/applications/{n}/reject")
def reject(n: int, body: dict, _=Depends(admin_guard)):
    a = reqsys.resolve_application(n, "已拒绝", body.get("note", ""))
    if not a:
        raise HTTPException(404, "申请不存在")
    alog("拒绝访问申请 #{}（{}）".format(n, a["self_name"]))
    return {"ok": True}


# ── 密钥与授权 ──

@router.get("/api/keys")
def keys(_=Depends(admin_guard)):
    return {"ok": True, "keys": auth.list_keys()}


def _check_ids(ids):
    known, unknown = [], []
    for i in ids:
        (known if store.id_exists(i) else unknown).append(i)
    return known, unknown


@router.post("/api/keys/grant")
def grant(body: dict, _=Depends(admin_guard)):
    ids = body.get("ids", [])
    known, unknown = _check_ids(ids)
    if not known:
        raise HTTPException(400, "没有有效标记（不存在的：{}）".format(unknown))
    if not auth.grant_ids(body.get("name", ""), known):
        raise HTTPException(404, "访问方不存在")
    warnings = []
    for i in known:
        if store.zone_of(i) == "pending":
            warnings.append("{} 在待审核区，需另加临时授权才能调用".format(i))
    return {"ok": True, "granted": known, "unknown": unknown, "warnings": warnings}


@router.post("/api/keys/revoke-id")
def revoke_id(body: dict, _=Depends(admin_guard)):
    if not auth.revoke_id(body.get("name", ""), body.get("id", "")):
        raise HTTPException(404, "访问方或授权标记不存在")
    return {"ok": True}


@router.post("/api/keys/temp")
def temp(body: dict, _=Depends(admin_guard)):
    eid = body.get("id", "")
    if store.zone_of(eid) != "pending":
        raise HTTPException(400, "临时授权只针对待审核区的知识（{} 不在待审核区）".format(eid))
    # 第二轮审查 M4：时长钳制在 1~720 小时，非法输入返回 400 而不是 500
    try:
        hours = int(body.get("hours", 24))
    except (TypeError, ValueError):
        raise HTTPException(400, "小时数必须是整数")
    if not 1 <= hours <= 720:
        raise HTTPException(400, "临时授权时长须在 1~720 小时之间")
    if not auth.temp_grant(body.get("name", ""), eid, hours):
        raise HTTPException(404, "访问方不存在")
    return {"ok": True}


@router.post("/api/keys/revoke")
def revoke(body: dict, _=Depends(admin_guard)):
    name = body.get("name", "")
    if name in (config.ADMIN_NAME, config.AI_NAME):
        raise HTTPException(400, "不能吊销管理员或 AI 的密钥")
    if not auth.revoke_key(name):
        raise HTTPException(404, "访问方不存在")
    return {"ok": True}


# ── 类型 ──

@router.get("/api/types")
def types(_=Depends(admin_guard)):
    return {"ok": True, "types": store.list_types()}


@router.post("/api/types")
def add_type(body: dict, _=Depends(admin_guard)):
    ok, msg = store.add_type(body.get("name", ""), body.get("prefix", ""))
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


# ── 知识管理 ──

@router.get("/api/entries")
def entries(zone: str = "", type: str = "", _=Depends(admin_guard)):
    if zone and zone not in config.ZONES[:3]:
        raise HTTPException(400, "分区无效")
    return {"ok": True, "entries": store.list_entries(zone or None, type or None),
            "labels": config.ZONE_LABEL}


@router.get("/api/entries/{eid}")
def entry(eid: str, _=Depends(admin_guard)):
    e = store.get_entry(eid)
    if not e:
        raise HTTPException(404, "标记不存在")
    return {"ok": True, "entry": e, "zone_label": config.ZONE_LABEL[e["zone"]],
            "history": store.list_history(eid)}


@router.post("/api/entries")
def create_entry(body: dict, _=Depends(admin_guard)):
    fields = {k: body.get(k, "") for k in
              ("title", "summary", "usage", "experience", "limitations", "content")}
    e, msg = store.create_entry(body.get("zone", "pending"), body.get("type", ""), fields)
    if not e:
        raise HTTPException(400, msg)
    return {"ok": True, "id": e["id"]}


@router.put("/api/entries/{eid}")
def update_entry(eid: str, body: dict, _=Depends(admin_guard)):
    fields = {k: body[k] for k in
              ("title", "summary", "usage", "experience", "limitations", "content") if k in body}
    e, msg = store.update_entry(eid, fields)
    if not e:
        raise HTTPException(404, msg)
    return {"ok": True}


@router.delete("/api/entries/{eid}")
def delete_entry(eid: str, body: dict = None, _=Depends(admin_guard)):
    reason = (body or {}).get("reason", "")
    ok, msg = store.delete_entry(eid, reason)
    if not ok:
        raise HTTPException(404, msg)
    return {"ok": True}


@router.post("/api/entries/{eid}/move")
def move(eid: str, body: dict, _=Depends(admin_guard)):
    ok, msg = store.move_zone(eid, body.get("zone", ""), body.get("note", ""))
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/api/entries/{eid}/experience")
def add_experience(eid: str, body: dict, _=Depends(admin_guard)):
    e, msg = store.add_experience(eid, body.get("note", ""))
    if not e:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/api/entries/{eid}/rollback")
def rollback(eid: str, body: dict, _=Depends(admin_guard)):
    ok, msg = store.rollback(eid, body.get("version", ""))
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


# ── 申请区处理 ──

@router.get("/api/reqs")
def reqs(_=Depends(admin_guard)):
    return {"ok": True, "reqs": reqsys.list_reqs()}


@router.post("/api/reqs/{rid}/exec")
def exec_req(rid: str, body: dict, _=Depends(admin_guard)):
    """一键执行申请。移区/追加经验简单直执行；新增/修改带表单字段——
    变更 #28：网页端逐字段确认后入库（信任区/存疑区由你选），CLI 不再是唯一通道。"""
    req = reqsys.get_req(rid)
    if not req or req["status"] != "待审核":
        raise HTTPException(404, "申请不存在或已处理")
    note = body.get("note", "")
    if req["action"] == "move_zone":
        ok, msg = store.move_zone(req["target"], body.get("zone", ""), "申请 " + rid)
    elif req["action"] == "suspect_to_pending":
        ok, msg = store.move_zone(req["target"], "pending", "申请 " + rid + "：" + note)
    elif req["action"] == "append_experience":
        _, msg = store.add_experience(req["target"], body.get("note") or req["proposal"],
                                      source=req["proposer"] + " 申请 " + rid)
        ok = _ is not None
    elif req["action"] == "new":
        fields = body.get("fields") or {}
        entry, msg = store.create_entry(body.get("zone", ""), body.get("type", ""),
                                        fields, source=req["proposer"] + " 申请 " + rid)
        ok = entry is not None
    elif req["action"] == "modify":
        entry, msg = store.update_entry(req["target"], body.get("fields") or {},
                                        source=req["proposer"] + " 申请 " + rid)
        ok = entry is not None
    else:
        raise HTTPException(400, "未知申请类型：" + req["action"])
    if not ok:
        raise HTTPException(400, msg)
    reqsys.resolve_req(rid, "已执行", note)
    return {"ok": True}


@router.post("/api/reqs/{rid}/reject")
def reject_req(rid: str, body: dict, _=Depends(admin_guard)):
    if not reqsys.resolve_req(rid, "已拒绝", body.get("note", "")):
        raise HTTPException(404, "申请不存在或已处理（已执行/已拒绝的申请不可再改）")
    return {"ok": True}


# ── 日志 ──

@router.get("/api/logs")
def logs(type: str = "access", n: int = 50, _=Depends(admin_guard)):
    if type not in ("access", "admin"):
        raise HTTPException(400, "日志类型无效")
    return {"ok": True, "lines": log_tail(type, min(n, 500))}


@router.get("/api/logs/verify")
def verify(_=Depends(admin_guard)):
    a = log_verify("access")
    m = log_verify("admin")
    return {"ok": a[0] and m[0], "access": a, "admin": m}
