# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【隐含】事件槽位里的时间需要归一化；【施工方】中文时间解析子集
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""中文时间归一化：把「晚上11点」「早上8点30分」转成今日 datetime。
骨架版只负责这一个子集；解析不了就返回 None（上层反问追问槽位）。"""
import re
from datetime import datetime, timedelta

PERIODS = {
    "凌晨": (0, 5),
    "早上": (6, 11), "上午": (6, 11),
    "中午": (11, 13),
    "下午": (13, 18),
    "晚上": (18, 23), "今晚": (18, 23),
}
# 第三轮审查：支持「点半」（=30 分），与分钟二选一
PAT = re.compile(r"^(?:(明天|后天))?(早上|上午|中午|下午|晚上|今晚|凌晨)?"
                 r"(\d{1,2})点(?:(\d{1,2})分|半)?$")


def parse_time_str(s, now=None):
    """返回 (datetime 或 None, 说明)。
    时间已过 → (None, "时间已过")；格式不认 → (None, "格式无法识别")。
    变更 #14 增补：支持「明天/后天」前缀（在原钟点基础上加天数）。"""
    now = now or datetime.now()
    m = PAT.match(s.strip())
    if not m:
        return None, "格式无法识别"
    day = {"明天": 1, "后天": 2}.get(m.group(1), 0)
    period, hh = m.group(2), int(m.group(3))
    mm = int(m.group(4)) if m.group(4) else (30 if m.group(0).endswith("半") else 0)
    if mm > 59:
        return None, "分钟数不对"
    # 第三轮审查：「晚上12点(半)」口语指当天午夜，即次日 0 点，不该报"时段对不上"
    if period in ("晚上", "今晚") and hh == 12:
        t = now.replace(hour=0, minute=mm, second=0, microsecond=0) + timedelta(days=1 + day)
        return t, "ok"
    if period:
        lo, hi = PERIODS[period]
        if lo <= hh <= hi:
            hour = hh
        elif lo <= hh + 12 <= hi:
            hour = hh + 12
        elif lo <= hh - 12 <= hi:
            hour = hh - 12
        else:
            return None, "时段与钟点对不上"
    else:
        if not 0 <= hh <= 23:
            return None, "钟点不对"
        hour = hh
    t = now.replace(hour=hour, minute=mm, second=0, microsecond=0) + timedelta(days=day)
    if t <= now:
        return None, "时间已过"
    return t, "ok"
