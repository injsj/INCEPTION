# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【拍板】三区存储：分区=确定性程度（信任区=已证明/存疑区=能用未证/待审核区=申请候着）（变更 #7，规格 §4）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""知识库存储层：类型与字母前缀、分区、条目 CRUD、序号计数器、
索引、历史版本、服务开关。所有写操作原子落盘，所有变化记 admin 日志。"""
import json
import shutil
from datetime import datetime
from functools import wraps
from pathlib import Path

from . import config
from .audit import admin as alog
from .safeio import meta_lock, read_json, read_meta, write_json


def _serialized(fn):
    """元数据/条目写操作串行化（第二轮审查 H1）：
    修复前 counters/index 的读-改-写无锁，FastAPI 线程池并发或
    服务+CLI 跨进程并发会产生重复标记 ID、索引丢更新。"""
    @wraps(fn)
    def wrapper(*a, **kw):
        with meta_lock():
            return fn(*a, **kw)
    return wrapper


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────── 服务开关 ────────────────────

def get_state():
    """读取开关状态。文件不存在时创建默认值（默认开、仅本机、目录公开）。"""
    d = read_meta(config.STATE_FILE)
    if not isinstance(d, dict):
        d = {"enabled": True, "allow_remote": False, "catalog_public": True, "rev": 1}
        write_json(config.STATE_FILE, d)
    return d


@_serialized
def set_state(**kw):
    """修改开关。rev 每次 +1，配合 mtime 双保险保证缓存即时失效。
    None 视为"未指定"直接跳过（防御调用方把 body.get() 的缺省 None 透传过来）。"""
    d = get_state().copy()
    changes = []
    for k in ("enabled", "allow_remote", "catalog_public"):
        if k in kw and kw[k] is not None:
            v = bool(kw[k])
            if d.get(k) != v:
                d[k] = v
                changes.append("{}={}".format(k, "开" if v else "关"))
    d["rev"] = int(d.get("rev", 0)) + 1
    write_json(config.STATE_FILE, d)
    if changes:
        label = {"enabled": "总开关", "allow_remote": "对外访问", "catalog_public": "目录公开"}
        alog("切换开关：" + "，".join(label[c.split("=")[0]] + c.split("=")[1] for c in changes))
    return d


# ──────────────────── 类型与字母前缀 ────────────────────

def list_types():
    return read_meta(config.TYPES_FILE) or []


@_serialized
def add_type(name, prefix):
    name = name.strip()
    prefix = prefix.strip().upper()
    if not name or not prefix:
        return False, "类型名和前缀不能为空"
    if len(prefix) != 1 or not prefix.isalpha():
        return False, "前缀必须是单个英文字母"
    types = list_types()
    if any(t["name"] == name for t in types):
        return False, "类型「{}」已存在".format(name)
    if any(t["prefix"] == prefix for t in types):
        return False, "字母 {} 已被其他类型占用".format(prefix)
    types.append({"name": name, "prefix": prefix})
    write_json(config.TYPES_FILE, types)
    alog("新增知识类型「{}」，开头字母 {}".format(name, prefix))
    return True, "ok"


def prefix_of_type(tname):
    for t in list_types():
        if t["name"] == tname:
            return t["prefix"]
    return None


# ──────────────────── 序号计数器（只增不减、不回收）────────────────────

def _counters():
    return read_json(config.COUNTERS_FILE, {}) or {}


def _save_counters(c):
    write_json(config.COUNTERS_FILE, c)


def alloc_id(prefix):
    c = _counters()
    n = int(c.get(prefix, 0)) + 1
    c[prefix] = n
    _save_counters(c)
    return "{}{}".format(prefix, n)


def alloc_req_id():
    c = _counters()
    n = int(c.get("REQ", 0)) + 1
    c["REQ"] = n
    _save_counters(c)
    return "REQ{}".format(n)


# ──────────────────── 索引 ────────────────────

def _index():
    return read_meta(config.INDEX_FILE) or {}


def _save_index(idx):
    write_json(config.INDEX_FILE, idx)


@_serialized
def rebuild_index():
    """扫描全部分区重建索引。启动自检和外部修改侦测时调用。返回 (条数, 损坏文件列表)。"""
    idx, bad = {}, []
    for zone in config.ZONES:
        if zone == "requests":
            continue
        zdir = config.DATA_DIR / zone
        if not zdir.exists():
            continue
        for f in sorted(zdir.rglob("*.json")):
            try:
                e = json.loads(f.read_text(encoding="utf-8"))
                idx[e["id"]] = {
                    "zone": zone, "type": e["type"], "title": e.get("title", ""),
                    "file": str(f.relative_to(config.DATA_DIR)),
                }
            except Exception:
                bad.append(str(f))
    _save_index(idx)
    return len(idx), bad


def entry_path(eid):
    """标记 → 条目文件路径。索引的 file 字段做目录约束校验（第二轮审查 L6：
    meta 被手改时防止借 "../../" 越界读写到 data/ 之外）。"""
    info = _index().get(eid)
    if not info:
        return None
    try:
        p = (config.DATA_DIR / info["file"]).resolve()
        if not p.is_relative_to(config.DATA_DIR.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return p


def get_entry(eid):
    path = entry_path(eid)
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def id_exists(eid):
    return eid in _index()


def zone_of(eid):
    info = _index().get(eid)
    return info["zone"] if info else None


# ──────────────────── 历史版本 ────────────────────

def _backup(eid):
    src = entry_path(eid)
    if not src or not src.exists():
        return
    hdir = config.HISTORY_DIR / eid
    hdir.mkdir(parents=True, exist_ok=True)
    # 毫秒精度：同一秒内的连续修改各自留档（第二轮审查 M3：
    # 秒级文件名会被同秒第二次备份覆盖，回滚链出现缺口）
    ver = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    shutil.copy2(src, hdir / (ver + ".json"))


def list_history(eid):
    d = config.HISTORY_DIR / eid
    if not d.exists():
        return []
    return sorted((p.name for p in d.glob("*.json")), reverse=True)


@_serialized
def rollback(eid, version):
    src = config.HISTORY_DIR / eid / version
    if not src.exists():
        return False, "历史版本不存在"
    info = _index().get(eid)
    if not info:
        return False, "标记不存在（可能已被废弃）"
    entry = json.loads(src.read_text(encoding="utf-8"))
    # 分区以索引当前值为准：历史文件里的 zone 是备份时刻的旧值，
    # 直接写回会造成"条目 zone 与索引 zone 不一致"（授权与目录各说各话）
    entry["zone"] = info["zone"]
    entry["updated_at"] = now()
    write_json(entry_path(eid), entry)
    idx = _index()
    if eid in idx:
        idx[eid]["title"] = entry.get("title", "")
        _save_index(idx)
    alog("回滚 {} 至历史版本 {}".format(eid, version))
    return True, "ok"


# ──────────────────── 条目 CRUD ────────────────────

@_serialized
def create_entry(zone, tname, fields, source=None):
    if zone not in ("trusted", "suspect", "pending"):
        return None, "目标分区无效"
    prefix = prefix_of_type(tname)
    if not prefix:
        return None, "类型「{}」不存在，请先添加类型".format(tname)
    missing = [f for f in config.ENTRY_REQUIRED_FIELDS if not str(fields.get(f, "")).strip()]
    if missing:
        return None, "缺少必填字段：" + ",".join(missing)
    eid = alloc_id(prefix)
    entry = {
        "id": eid, "type": tname, "zone": zone,
        "title": fields["title"].strip(), "summary": fields["summary"].strip(),
        "usage": fields["usage"], "experience": fields["experience"],
        "limitations": fields["limitations"], "content": fields.get("content", ""),
        "experiences": fields.get("experiences", []),
        "created_at": now(), "updated_at": now(),
        "source": source or config.ADMIN_NAME,
    }
    path = config.DATA_DIR / zone / tname / (eid + ".json")
    write_json(path, entry)
    idx = _index()
    idx[eid] = {"zone": zone, "type": tname, "title": entry["title"],
                "file": str(path.relative_to(config.DATA_DIR))}
    _save_index(idx)
    alog("新增知识 {} · {}「{}」（{}），来源：{}".format(
        config.ZONE_LABEL[zone], eid, entry["title"], tname, entry["source"]))
    return entry, "ok"


@_serialized
def update_entry(eid, fields, source=None):
    entry = get_entry(eid)
    if not entry:
        return None, "标记不存在"
    _backup(eid)
    for k in ("title", "summary", "usage", "experience", "limitations", "content"):
        if k in fields and fields[k] is not None:
            entry[k] = fields[k]
    entry["updated_at"] = now()
    if source:
        entry["source"] = source
    write_json(entry_path(eid), entry)
    idx = _index()
    if eid in idx:
        idx[eid]["title"] = entry["title"]
        _save_index(idx)
    alog("修改知识 {}「{}」".format(eid, entry["title"]) + ("，来源：" + source if source else ""))
    return entry, "ok"


@_serialized
def delete_entry(eid, reason=""):
    info = _index().get(eid)
    entry = get_entry(eid)
    if not info or not entry:
        return False, "标记不存在"
    _backup(eid)
    src = entry_path(eid)
    if src:
        src.unlink(missing_ok=True)
    idx = _index()
    idx.pop(eid, None)
    _save_index(idx)
    alog("废弃知识 {}「{}」（序号不回收）{}".format(
        eid, entry.get("title", ""), ("，原因：" + reason) if reason else ""))
    return True, "ok"


@_serialized
def move_zone(eid, zone, note=""):
    if zone not in ("trusted", "suspect", "pending"):
        return False, "目标分区无效"
    info = _index().get(eid)
    entry = get_entry(eid)
    if not info or not entry:
        return False, "标记不存在"
    if info["zone"] == zone:
        return False, "该知识已在{}".format(config.ZONE_LABEL[zone])
    _backup(eid)
    old = info["zone"]
    entry["zone"] = zone
    entry["updated_at"] = now()
    new_path = config.DATA_DIR / zone / entry["type"] / (eid + ".json")
    write_json(new_path, entry)  # 先写新位置并校验
    src = entry_path(eid)
    if src:
        src.unlink(missing_ok=True)  # 再删旧位置
    idx = _index()
    idx[eid]["zone"] = zone
    idx[eid]["file"] = str(new_path.relative_to(config.DATA_DIR))
    _save_index(idx)
    alog("知识 {}「{}」从{}移至{}{}".format(
        eid, entry["title"], config.ZONE_LABEL[old], config.ZONE_LABEL[zone],
        ("，说明：" + note) if note else ""))
    return True, "ok"


@_serialized
def add_experience(eid, note, source=None):
    entry = get_entry(eid)
    if not entry:
        return None, "标记不存在"
    if not note.strip():
        return None, "经验内容不能为空"
    _backup(eid)
    entry.setdefault("experiences", []).append(
        {"date": now(), "note": note, "status": "unproven", "by": source or config.ADMIN_NAME})
    entry["updated_at"] = now()
    write_json(entry_path(eid), entry)
    alog("为 {} 追加经验记录，来源：{}".format(eid, source or config.ADMIN_NAME))
    return entry, "ok"


@_serialized
def set_experience_status(eid, idx_i, status):
    if status not in ("unproven", "confirmed", "disproven"):
        return False, "状态无效"
    entry = get_entry(eid)
    if not entry or not 0 <= idx_i < len(entry.get("experiences", [])):
        return False, "经验记录不存在"
    _backup(eid)
    entry["experiences"][idx_i]["status"] = status
    entry["updated_at"] = now()
    write_json(entry_path(eid), entry)
    alog("标记 {} 第 {} 条经验为 {}".format(eid, idx_i + 1, status))
    return True, "ok"


# ──────────────────── 查询与目录 ────────────────────

def list_entries(zone=None, tname=None):
    out = []
    for eid, info in sorted(_index().items()):
        if zone and info["zone"] != zone:
            continue
        if tname and info["type"] != tname:
            continue
        out.append({"id": eid, "zone": info["zone"], "type": info["type"], "title": info["title"]})
    return out


def catalog_data():
    """公开目录：按区分组，只含标记/类型/标题/简介，绝不出现正文与待审核区。"""
    idx = _index()
    zones = []
    for zone in ("trusted", "suspect"):
        items = []
        for eid, info in sorted(idx.items()):
            if info["zone"] != zone:
                continue
            entry = get_entry(eid)
            items.append({"id": eid, "type": info["type"], "title": info["title"],
                          "summary": (entry or {}).get("summary", "")})
        zones.append({"zone": zone, "label": config.ZONE_LABEL[zone], "entries": items})
    return zones


def counts():
    c = {"trusted": 0, "suspect": 0, "pending": 0}
    for info in _index().values():
        c[info["zone"]] = c.get(info["zone"], 0) + 1
    return c
