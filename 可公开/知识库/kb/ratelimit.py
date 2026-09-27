# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】限流（安全补建，非设计者提出）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""内存滑动窗口限流：每 IP 每分钟 N 次。单进程内无状态依赖，够用且不引入外部组件。"""
import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_hits = defaultdict(deque)


def allow(ip, limit, window=60):
    now = time.time()
    with _lock:
        # 惰性全表清理：防止长期运行（尤其对外开启后）已沉寂 IP 永久驻留内存
        if len(_hits) > 10000:
            for k in [k for k, q in _hits.items() if not q or q[-1] < now - window]:
                del _hits[k]
        q = _hits[ip]
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True
