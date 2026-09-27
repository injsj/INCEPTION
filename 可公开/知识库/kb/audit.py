# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】我要看到现有的数据，每次权重变化都记录——全程留痕可审计（规格 §0）
# 【施工方】哈希链与跨进程文件锁实现
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""双日志 + 哈希链防篡改（修复审查问题 4）。
- access.log：每一次调用（谁在什么时候调用/尝试了哪个标记，结果如何）
- admin.log：每一次管理行为（开关、审批、授权、修改、移区……）
每条日志带上一条的哈希，校验可定位篡改点。
跨进程安全：服务与管理 CLI 可能同时写日志（说明书支持服务运行中使用菜单），
logstate 的读-改-写用 Windows 文件锁互斥，否则两进程并发会产生引用同一
前哈希的两行，导致校验误报"断裂"。"""
import contextlib
import hashlib
import threading
from datetime import datetime
from pathlib import Path

from . import config
from .safeio import read_json, write_json

try:
    import msvcrt  # Windows 跨进程文件锁
except ImportError:  # 非 Windows 平台退化为仅进程内锁
    msvcrt = None

_lock = threading.Lock()


@contextlib.contextmanager
def _xlock():
    """跨进程互斥锁（锁定 logs/.xlock 的第 0 字节，最多等待约 10 秒）。"""
    if msvcrt is None:
        yield
        return
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    f = open(config.LOG_DIR / ".xlock", "a+b")
    try:
        if f.seek(0, 2) == 0:
            f.write(b"\0")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        f.close()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _chain_hash(prev, content):
    return hashlib.sha256(("{}|{}".format(prev, content)).encode("utf-8")).hexdigest()[:32]


def _oneline(s):
    """第二轮审查 H3：外部可控字段（申请人自报名称等）可能含换行符，
    写进日志会产生伪造物理行，导致哈希链校验永久报"断裂"（匿名审计 DoS）。
    所有入链内容统一压成单行。"""
    return str(s).replace("\r", " ").replace("\n", " ")


def _log(kind, who, message):
    with _xlock():  # 跨进程互斥：logstate 读-改-写与日志追加必须是一个整体
        log_file = config.LOG_DIR / "{}.log".format(kind)
        state = read_json(config.LOG_STATE_FILE, {}) or {}
        prev = state.get(kind, "GENESIS")
        content = "{} | {} | {}".format(_now(), _oneline(who), _oneline(message))
        h = _chain_hash(prev, content)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("{} | {}\n".format(h, content))
        state[kind] = h
        write_json(config.LOG_STATE_FILE, state)


def access(who, message):
    """记录一次调用。"""
    with _lock:
        _log("access", who, message)


def admin(message, who=None):
    """记录一次管理行为。"""
    with _lock:
        _log("admin", who or config.ADMIN_NAME, message)


def tail(kind, n=50):
    log_file = config.LOG_DIR / "{}.log".format(kind)
    if not log_file.exists():
        return []
    lines = log_file.read_text(encoding="utf-8").splitlines()
    return lines[-n:]


def verify(kind):
    """校验哈希链。返回 (是否完好, 已校验条数, 断裂位置)。"""
    log_file = config.LOG_DIR / "{}.log".format(kind)
    if not log_file.exists():
        return True, 0, -1
    prev, count = "GENESIS", 0
    for i, line in enumerate(log_file.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        h, _, content = line.partition(" | ")
        if _chain_hash(prev, content) != h:
            return False, count, i + 1
        prev, count = h, count + 1
    state = read_json(config.LOG_STATE_FILE, {}) or {}
    return state.get(kind) == prev, count, -1
