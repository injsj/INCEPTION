# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】默认只占一半内存和显存（规格 §0/§9）；【施工方】软上限监控实现
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""资源软上限（规格书第 9 节）：默认只用现有内存的一半，逼近即主动降速。
上限可在管理界面调节（写 state\\resource_cap.json，mem_cap ∈ (0,1]）。
显存上限为神经组件预留（第二三档启用），骨架版不占。"""
import ctypes
import json
import time
from ctypes import wintypes

from . import config

CAP_FILE = config.STATE / "resource_cap.json"
DEFAULT_CAP = 0.5


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


# 第四轮审查修复：64 位 Windows 上 ctypes 默认把函数返回值当 32 位 int，
# GetCurrentProcess 的伪句柄 (-1) 被截断成 0x00000000FFFFFFFF，
# GetProcessMemoryInfo 因此永远失败 → 软上限检查静默失效。
# 显式声明签名后句柄才是完整 64 位（声明须在结构体定义之后）。
if getattr(ctypes, "windll", None) is not None:
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.kernel32.GetCurrentProcess.argtypes = []
    ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wintypes.DWORD]


def total_phys_mb():
    if getattr(ctypes, "windll", None) is None:
        return None  # 非 Windows 平台（第二轮审查 L5：import 期不炸，运行期降级）
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return m.ullTotalPhys / 1024.0 / 1024.0
    return None


def process_rss_mb():
    if getattr(ctypes, "windll", None) is None:
        return None
    pm = _PROCESS_MEMORY_COUNTERS()
    pm.cb = ctypes.sizeof(pm)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pm), pm.cb):
        return pm.WorkingSetSize / 1024.0 / 1024.0
    return None


def load_cap():
    """读取内存上限比例；文件缺失或损坏回退默认 50%。"""
    if CAP_FILE.exists():
        try:
            v = float(json.loads(CAP_FILE.read_text(encoding="utf-8")).get("mem_cap", DEFAULT_CAP))
            if 0 < v <= 1:
                return v
        except (ValueError, KeyError):
            pass
    return DEFAULT_CAP


def set_cap(v):
    """管理界面调这里。v ∈ (0,1]，如 0.5 = 最多用一半内存。"""
    v = float(v)
    if not 0 < v <= 1:
        raise ValueError("上限比例必须在 (0,1] 之间")
    config.ensure()
    config.atomic_write(CAP_FILE, json.dumps({"mem_cap": v}))


def check_and_throttle():
    """每轮开头调用。返回 (状态, 说明)。超过上限 → sleep 降速并给出说明。"""
    total, rss = total_phys_mb(), process_rss_mb()
    if not total or rss is None:
        return "unknown", "读不到内存信息（非 Windows？），跳过软上限检查"
    cap = load_cap()
    limit = total * cap
    if rss > limit:
        time.sleep(0.5)
        return "throttled", "内存 {:.0f}MB 超过软上限 {:.0f}MB（上限比例 {:.0%}），已降速".format(rss, limit, cap)
    return "ok", "内存 {:.0f}MB / 软上限 {:.0f}MB".format(rss, limit)
