# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】原子写/备份/锁（工程可靠性补建）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""安全的文件 IO：
- 原子写入（临时文件 + os.replace + 失败重试），写后强制刷新 mtime
- 带缓存的元数据读取：同时比对 (mtime, size)，外部修改 ≤1 秒生效
  （修复审查问题 1：单看 mtime 在 Windows 上可能失效）
- meta_lock：元数据写操作的全局互斥（进程内线程锁 + 跨进程 Windows 文件锁）。
  修复第二轮审查 H1：counters/index/keys/applications 的读-改-写此前无锁，
  FastAPI 线程池并发或服务+CLI 跨进程并发会产生重复标记 ID、丢更新。
  （audit.py 的日志锁只护住了 logstate，数据文件同样需要。）"""
import contextlib
import json
import os
import threading
import time
from pathlib import Path

try:
    import msvcrt  # Windows 跨进程文件锁
except ImportError:  # 非 Windows 退化为仅进程内锁
    msvcrt = None

RETRIES = 5
RETRY_WAIT = 0.05


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    err = None
    for i in range(RETRIES):
        try:
            os.replace(tmp, path)
            os.utime(path, None)  # 强制刷新 mtime，确保缓存失效
            return
        except PermissionError as e:  # Windows 上杀毒软件/句柄占用
            err = e
            time.sleep(RETRY_WAIT)
    tmp.unlink(missing_ok=True)
    raise err


def write_json(path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


_cache = {}


def read_meta(path):
    """读取小型 JSON 元数据，带 (mtime_ns, size) 缓存。文件被外部修改后自动重读。"""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    data = read_json(path)
    _cache[key] = (st.st_mtime_ns, st.st_size, data)
    return data


# ──────────────────── 元数据写操作全局锁 ────────────────────

_metalock_thread = threading.Lock()
_metalock_local = threading.local()


@contextlib.contextmanager
def meta_lock():
    """元数据写互斥：进程内线程锁 + 跨进程文件锁（meta/.metalock 第 0 字节）。

    可重入：同一线程嵌套进入时直接放行（最外层已持锁），避免
    create_entry → alloc_id 这类调用链自锁。
    锁序约定：只存在 meta_lock → audit._xlock 方向，反向不存在，无死锁环。
    """
    depth = getattr(_metalock_local, "depth", 0)
    if depth:
        _metalock_local.depth = depth + 1
        try:
            yield
        finally:
            _metalock_local.depth -= 1
        return
    with _metalock_thread:
        _metalock_local.depth = 1
        try:
            if msvcrt is None:
                yield
                return
            from . import config  # 惰性导入，避免模块加载顺序问题
            config.META_DIR.mkdir(parents=True, exist_ok=True)
            f = open(config.META_DIR / ".metalock", "a+b")
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
        finally:
            _metalock_local.depth = 0
