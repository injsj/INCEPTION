# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】AI 通过目录调用（规格 §0）——对外目录/读取/申请接口；【施工方】实现
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""对外接口：目录浏览（匿名）、访问申请（匿名）、知识调用（密钥）、AI 申请区。

权限矩阵：
- /api/catalog          匿名（受“目录公开”开关控制，待审核区永不出现）
- /api/apply            匿名（仅可信/存疑区的标记可申请）
- /api/kb/{id}          密钥（reader 按标记授权，AI/管理员全可读含待审核区）
- /api/req  GET/POST    仅 AI 密钥
"""
from fastapi import APIRouter, Header, HTTPException, Request

from . import auth, config, reqsys, store
from .audit import access as log_access

router = APIRouter()


def _ip(request):
    return request.client.host if request.client else "?"


@router.get("/api/catalog")
def catalog(request: Request):
    state = store.get_state()
    if not state.get("catalog_public", True):
        raise HTTPException(403, "目录已被管理员隐藏")
    zones = store.catalog_data()
    total = sum(len(z["entries"]) for z in zones)
    log_access("匿名", "浏览目录，共 {} 条 | {}".format(total, _ip(request)))
    return {"ok": True, "labels": config.ZONE_LABEL, "zones": zones}


@router.post("/api/apply")
def apply(body: dict, request: Request):
    ok, msg = reqsys.add_application(
        body.get("self_name", ""), body.get("reason", ""), body.get("ids", []))
    who = str(body.get("self_name") or "匿名")[:20]
    log_access(who, "提交访问申请，标记 {} | {}".format(body.get("ids", []), _ip(request)))
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "msg": "申请已提交，请等待管理员审批。批准后管理员会私下转交你的密钥。"}


def _reader_from(key):
    rec = auth.find(key)
    if not rec:
        raise HTTPException(401, "密钥无效或缺失（请求头 X-API-Key）")
    return rec


@router.get("/api/kb/{eid}")
def read_kb(eid: str, request: Request, x_api_key: str = Header(default="", alias="X-API-Key")):
    rec = _reader_from(x_api_key)
    entry = store.get_entry(eid)
    if not entry:
        log_access(rec["name"], "GET /kb/{} 404 | {}".format(eid, _ip(request)))
        raise HTTPException(404, "标记不存在")
    ok, why = auth.can_read(rec, entry)
    log_access(rec["name"], "GET /kb/{} {}({}) | {}".format(
        eid, "200" if ok else "403", why or "ok", _ip(request)))
    if not ok:
        raise HTTPException(403, why)
    return {"ok": True, "zone_label": config.ZONE_LABEL[entry["zone"]], "entry": entry}


# ── AI 申请区 ──

def _ai_from(key):
    rec = auth.find(key)
    if not rec or rec["role"] != "ai":
        raise HTTPException(401, "仅 AI 密钥可访问申请区（X-API-Key）")
    return rec


@router.post("/api/req")
def make_req(body: dict, request: Request, x_api_key: str = Header(default="", alias="X-API-Key")):
    rec = _ai_from(x_api_key)
    req, msg = reqsys.create_req(
        rec["name"], body.get("action", ""), body.get("target", ""),
        body.get("proposal", ""), body.get("reason", ""), body.get("proof", ""))
    log_access(rec["name"], "提交申请 {}（{}）| {}".format(
        req["id"] if req else "失败", body.get("action", ""), _ip(request)))
    if not req:
        raise HTTPException(400, msg)
    return {"ok": True, "id": req["id"], "status": req["status"]}


@router.get("/api/req")
def my_reqs(x_api_key: str = Header(default="", alias="X-API-Key")):
    rec = _ai_from(x_api_key)
    reqs = [{
        "id": r["id"], "action": r["action"], "action_name": r["action_name"],
        "target": r["target"], "status": r["status"], "admin_note": r["admin_note"],
        "created_at": r["created_at"], "resolved_at": r["resolved_at"],
    } for r in reqsys.list_reqs()]
    return {"ok": True, "reqs": reqs}
