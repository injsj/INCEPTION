# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】开设存储区存每件任务的详细思考和计算过程（规格 §7）；AI 直接写工作记录，晋升知识必须走 REQ（铁律）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""决策追踪：每一件任务的过程落档案区（规格书第 7 节：全程留痕）。
档案 = AI 的工作记录，直接写；晋升知识必须走 REQ（铁律）。"""
import json
import time

from . import config


def record(task):
    """第二轮审查 L3：档案按月进子目录（archive/YYYYMM/），防单目录无限膨胀；
    文件名碰撞时追加序号，同毫秒两次记录不再互相覆盖。"""
    config.ensure()
    month_dir = config.ARCHIVE / time.strftime("%Y%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    base = "task_" + time.strftime("%Y%m%d_%H%M%S") + "_{:03d}".format(
        int(time.time() * 1000) % 1000)
    path, n = month_dir / (base + ".json"), 0
    while path.exists():
        n += 1
        path = month_dir / ("{}_{}.json".format(base, n))
    task["archived_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    config.atomic_write(path, json.dumps(task, ensure_ascii=False, indent=2))
    return path
