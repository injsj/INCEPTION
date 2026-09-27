# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】密钥签发/吊销/校验（安全补建，非设计者提出）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""密钥体系：哈希存储（不存明文）、三种角色、按标记授权、
临时授权（待审核区验证用）、即时吊销（mtime+rev 缓存失效）。"""
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps

from . import config, store
from .audit import admin as alog
from .safeio import meta_lock, read_meta, write_json

ROLE_NAMES = {"admin": "管理员", "ai": "AI", "reader": "访问方"}


def _serialized(fn):
    """密钥文件读-改-写串行化（第二轮审查 H1，与 store 同一把 meta_lock）。"""
    @wraps(fn)
    def wrapper(*a, **kw):
        with meta_lock():
            return fn(*a, **kw)
    return wrapper


def _hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _keys():
    d = read_meta(config.KEYS_FILE)
    return (d or {}).get("keys", {})


def _save(keys):
    d = read_meta(config.KEYS_FILE) or {"rev": 0}
    d["keys"] = keys
    d["rev"] = int(d.get("rev", 0)) + 1
    write_json(config.KEYS_FILE, d)


def make_key():
    return secrets.token_urlsafe(24)


def list_keys():
    """返回可展示的密钥列表（不含哈希）。"""
    out = []
    for rec in _keys().values():
        out.append({"name": rec["name"], "role": rec["role"],
                    "role_name": ROLE_NAMES.get(rec["role"], rec["role"]),
                    "granted_ids": rec.get("granted_ids", []),
                    "temp": rec.get("temp", {}),
                    "created": rec.get("created", "")})
    return out


def name_exists(name):
    return any(r["name"] == name for r in _keys().values())


@_serialized
def add_key(name, role, granted_ids=None):
    """创建密钥，明文只在返回中出现这一次。role: admin / ai / reader"""
    if role not in ROLE_NAMES:
        return None
    if name in (config.ADMIN_NAME, config.AI_NAME) and role == "reader":
        return None  # 保留名不可分配给访问方
    if name_exists(name):
        return None
    key = make_key()
    keys = _keys()
    keys[_hash(key)] = {
        "name": name, "role": role,
        "granted_ids": list(granted_ids or []),
        "temp": {}, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save(keys)
    alog("创建{}密钥，固定名称「{}」{}".format(
        ROLE_NAMES[role], name,
        ("，授权标记 {}".format(list(granted_ids or []))) if granted_ids else ""))
    return key


def find(key):
    """根据调用方提交的密钥查找记录，找不到返回 None。"""
    if not key:
        return None
    return _keys().get(_hash(key))


def _update_by_name(name, fn):
    keys = _keys()
    for h, rec in keys.items():
        if rec["name"] == name:
            fn(rec)
            _save(keys)
            return True
    return False


@_serialized
def grant_ids(name, ids):
    def fn(rec):
        for i in ids:
            if i not in rec["granted_ids"]:
                rec["granted_ids"].append(i)
    ok = _update_by_name(name, fn)
    if ok:
        alog("为「{}」追加授权标记 {}".format(name, ids))
    return ok


@_serialized
def revoke_id(name, eid):
    ok = _update_by_name(name, lambda r: r["granted_ids"].remove(eid) if eid in r["granted_ids"] else None)
    if ok:
        alog("收回「{}」的 {} 访问权".format(name, eid))
    return ok


@_serialized
def temp_grant(name, eid, hours):
    exp = (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    ok = _update_by_name(name, lambda r: r.setdefault("temp", {}).update({eid: exp}))
    if ok:
        alog("临时授权「{}」调用 {}（{} 小时，至 {}），用于验证审核".format(name, eid, hours, exp))
    return ok


@_serialized
def revoke_key(name):
    keys = _keys()
    h = next((k for k, r in keys.items() if r["name"] == name), None)
    if h is None:
        return False
    del keys[h]
    _save(keys)
    alog("吊销访问方「{}」，密钥立即失效".format(name))
    return True


def _clean_temp(rec):
    """惰性清理过期临时授权。"""
    t = rec.get("temp", {})
    nows = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    expired = [k for k, v in t.items() if v <= nows]
    for k in expired:
        del t[k]
    return expired


def can_read(rec, entry):
    """返回 (是否允许, 说明)。调用前请先 get_entry 确认条目存在。"""
    eid, zone = entry["id"], entry["zone"]
    if rec["role"] in ("admin", "ai"):
        return True, ""
    if zone in config.CALLABLE_ZONES:
        if eid in rec.get("granted_ids", []):
            return True, ""
        return False, "未授权该标记"
    if zone == "pending":
        _clean_temp(rec)
        exp = rec.get("temp", {}).get(eid)
        if exp:
            return True, "临时授权"
        return False, "待审核区知识需管理员临时授权方可调用"
    return False, "该区域不可调用"
