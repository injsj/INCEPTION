# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】修改走申请：propose()→REQ→人类裁决（规格 §0/§4 规则入库流转，变更 #7 拍板）
# 【施工方】REQ 五要素结构与节流
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""申请体系：
- 申请区（REQ）：AI 提出的修改申请，五种动作，强制附理由+证明过程
- 访问申请：人类访问方匿名提交、管理员批准后成为只读访问方"""
import json
from functools import wraps

from . import config, store
from .audit import admin as alog
from .safeio import meta_lock, read_json, write_json

REQ_ACTIONS = {
    "modify": "修改知识",
    "new": "新增知识",
    "append_experience": "追加经验",
    "move_zone": "建议移区",
    "suspect_to_pending": "存疑转待审核",
}


def _serialized(fn):
    """申请文件读-改-写串行化（第二轮审查 H1，与 store 同一把 meta_lock）。"""
    @wraps(fn)
    def wrapper(*a, **kw):
        with meta_lock():
            return fn(*a, **kw)
    return wrapper


# ──────────────────── 申请区（AI 的修改申请）────────────────────

@_serialized
def create_req(proposer, action, target, proposal, reason, proof):
    if action not in REQ_ACTIONS:
        return None, "非法申请类型，只能是：" + ",".join(REQ_ACTIONS)
    if not str(reason).strip() or not str(proof).strip():
        return None, "必须附上理由和证明过程"
    if action != "new":
        if not target or not store.get_entry(target):
            return None, "目标标记不存在"
        if action == "suspect_to_pending" and store.zone_of(target) != "suspect":
            return None, "该知识不在存疑区，无法申请转待审核"
    rid = store.alloc_req_id()
    req = {"id": rid, "proposer": proposer, "action": action, "target": target or "",
           "action_name": REQ_ACTIONS[action],
           "proposal": proposal, "reason": reason, "proof": proof,
           "status": "待审核", "admin_note": "",
           "created_at": store.now(), "resolved_at": ""}
    write_json(config.DATA_DIR / "requests" / (rid + ".json"), req)
    alog("收到 {} 的申请 {}（{} {}）".format(proposer, rid, REQ_ACTIONS[action], target or "新增"))
    return req, "ok"


def list_reqs(status=None):
    out = []
    rdir = config.DATA_DIR / "requests"
    if not rdir.exists():
        return out
    for f in sorted(rdir.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if status and r.get("status") != status:
            continue
        out.append(r)
    return out


def get_req(rid):
    return read_json(config.DATA_DIR / "requests" / (rid + ".json"))


@_serialized
def resolve_req(rid, status, note=""):
    """状态机守卫（第二轮审查 M1）：只有「待审核」的申请才能被裁决，
    防止已执行的申请被事后覆盖为「已拒绝」造成审计与实际状态矛盾。"""
    req = get_req(rid)
    if not req or req.get("status") != "待审核":
        return False
    req["status"] = status
    req["admin_note"] = note
    req["resolved_at"] = store.now()
    write_json(config.DATA_DIR / "requests" / (rid + ".json"), req)
    alog("申请 {} 标记为「{}」{}".format(rid, status, ("，意见：" + note) if note else ""))
    return True


# ──────────────────── 访问申请（人类）────────────────────

MAX_PENDING_APPLICATIONS = 100  # 防刷：待审申请上限
MAX_RESOLVED_APPLICATIONS = 500  # 第三轮审查 R3-5：已处理申请只留最近 500 条，防文件无限膨胀

# 第三轮审查 R3-2：终端转义注入——杀死 C0 控制字符（保留 \t\n\r）与 DEL，
# 没了 ESC（\x1b）就没有 ANSI 序列，管理菜单打印申请时不会被伪装/清屏。
_CTRL_CHARS = {chr(c) for c in range(0x00, 0x20) if chr(c) not in "\t\n\r"}
_CTRL_CHARS.add("\x7f")


def _clean_text(s):
    return "".join(ch for ch in str(s) if ch not in _CTRL_CHARS)


def _trim(apps):
    """已处理（批准/拒绝）的申请只保留最近 MAX_RESOLVED_APPLICATIONS 条；待审全留。
    必须原地过滤保持既有顺序——列表尾部=最新，调用方与测试都依赖这个不变量。"""
    resolved_idx = [i for i, a in enumerate(apps) if a["status"] != "待审"]
    if len(resolved_idx) <= MAX_RESOLVED_APPLICATIONS:
        return apps
    drop = set(resolved_idx[:-MAX_RESOLVED_APPLICATIONS])   # 最旧的已处理条目
    return [a for i, a in enumerate(apps) if i not in drop]


def list_applications():
    return read_json(config.APPLICATIONS_FILE, []) or []


@_serialized
def add_application(self_name, reason, ids):
    apps = list_applications()
    if len([a for a in apps if a["status"] == "待审"]) >= MAX_PENDING_APPLICATIONS:
        return False, "申请队列已满，请稍后再试"
    valid = []
    for i in ids or []:
        z = store.zone_of(i)
        if z in config.CALLABLE_ZONES:
            valid.append(i)
    n = max([a["n"] for a in apps], default=0) + 1
    apps.append({"n": n, "self_name": _clean_text(self_name).strip()[:20],
                 "reason": _clean_text(reason).strip()[:500], "ids": valid,
                 "time": store.now(), "status": "待审", "note": ""})
    write_json(config.APPLICATIONS_FILE, _trim(apps))
    return True, "ok"


def get_application(n):
    for a in list_applications():
        if a["n"] == n:
            return a
    return None


@_serialized
def resolve_application(n, status, note=""):
    """状态机守卫（第二轮审查 M1）：只有「待审」状态才能被裁决。"""
    apps = list_applications()
    for a in apps:
        if a["n"] == n:
            if a["status"] != "待审":
                return None
            a["status"] = status
            a["note"] = note
            write_json(config.APPLICATIONS_FILE, _trim(apps))
            return a
    return None
