# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】每日快照数据保险（变更 #30，用户质问审计驱动）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""每日快照备份（变更 #30 条文④·数据保险）。

范围：知识库 data/ 全区（三区+申请+元数据+审计日志）+ AI 侧 state/ 与 archive/。
位置：D:\\机密\\备份\\kb-YYYYmmDD-HHMMSS.zip（单文件，含 kb/ 与 ai/ 两个前缀）。
节奏：服务启动时若今天还没有快照立即补一份；之后每小时检查一次日期翻转。
保留：最近 KEEP 份，超出删旧（只删本模块命名前缀的文件，不动目录里别的东西）。
纪律：失败记管理日志，不静默、绝不影响服务本体。
"""
import asyncio
import zipfile
from datetime import datetime
from pathlib import Path

from . import config

BACKUP_DIR = config.SECRET_DIR / "备份"   # 变更 #33：快照含 ai/state（有密钥），随机密目录移出仓库树
KEEP = 14
CHECK_SECONDS = 3600          # 每小时看一次日期翻转
AI_ROOT = config.ROOT.parent / "AI"


def _stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _add_tree(zf, root, prefix):
    if not root.exists():
        return 0
    n = 0
    for f in sorted(root.rglob("*")):
        if f.is_file():
            try:
                zf.write(f, "{}/{}".format(prefix, f.relative_to(root).as_posix()))
                n += 1
            except OSError:
                pass   # 单文件被占用跳过，不拖垮整份快照
    return n


def snapshot_once():
    """立即做一份快照。返回 (路径, 文件数)；失败抛异常由调用方记日志。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / "kb-{}.zip".format(_stamp())
    n = 0
    with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
        n += _add_tree(zf, config.DATA_DIR, "kb/data")
        n += _add_tree(zf, AI_ROOT / "state", "ai/state")
        n += _add_tree(zf, AI_ROOT / "archive", "ai/archive")
    return out, n


def prune_old():
    """只保留最近 KEEP 份快照（按文件名时间序）。返回删除份数。"""
    snaps = sorted(BACKUP_DIR.glob("kb-*.zip"))
    gone = 0
    for p in snaps[:-KEEP]:
        try:
            p.unlink()
            gone += 1
        except OSError:
            pass
    return gone


def today_done():
    today = datetime.now().strftime("%Y%m%d")
    return any(BACKUP_DIR.glob("kb-{}-*.zip".format(today)))


async def watch_backup(alog):
    """后台协程：启动补快照 + 每小时检查日期翻转。异常绝不外溢。"""
    try:
        if not today_done():
            out, n = snapshot_once()
            alog("自动快照：{}（{} 个文件）".format(out.name, n))
            gone = prune_old()
            if gone:
                alog("快照轮换：删除最旧 {} 份（保留最近 {} 份）".format(gone, KEEP))
    except Exception as e:
        alog("自动快照失败（启动补做）：{}".format(e))
    while True:
        await asyncio.sleep(CHECK_SECONDS)
        try:
            if not today_done():
                out, n = snapshot_once()
                alog("自动快照：{}（{} 个文件）".format(out.name, n))
                gone = prune_old()
                if gone:
                    alog("快照轮换：删除最旧 {} 份".format(gone))
        except Exception as e:
            alog("自动快照失败：{}".format(e))
