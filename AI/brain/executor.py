# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】思想自由，行动受控——推演输出≠执行许可，执行只过白名单（规格 §0/§5）
# 【施工方】白名单清单与三态失败处理
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""白名单执行器（规格书第 5 节：思想自由，行动受控）。
清单之外的动作一律拒绝并请示——推演输出 ≠ 执行许可。"""
from ai_client import KBError


def _describe(slots, kb):
    """读取知识条目并陈述（带置信标注的输出格式）。"""
    eid = slots.get("target", "").upper()
    try:
        entry = kb.read(eid)
    except KBError as e:
        if e.status in RETRYABLE_STATUS:
            raise      # 瞬时故障抛出，交给三态包装重试（变更 #26）
        if e.status == 404:
            return "miss", "没有找到 {}。可能标记记错了，可以浏览目录确认。".format(eid)
        if e.status == 403:
            return "error", "{} 存在，但我还没有权限阅读（可申请授权）。".format(eid)
        if e.status == -1:
            return "error", "我现在联系不上知识库（服务未启动？）。"
        return "error", "读取失败：[{}] {}".format(e.status, e.message)
    parts = ["{}「{}」：{}".format(eid, entry["title"], entry["summary"]),
             "【详细使用方法】{}".format(entry["usage"]),
             "【使用经验】{}".format(entry["experience"]),
             "【局限性】{}".format(entry["limitations"])]
    return "ok", "\n".join(parts)


def _search(slots, kb):
    """按主题浏览目录（轻量层，对应目录调用）。"""
    hits = _match(slots.get("topic", ""), kb)
    if not hits:
        return "miss", "目录里没有找到与「{}」相关的内容。".format(slots.get("topic", ""))
    lines = ["找到 {} 条相关：".format(len(hits))]
    for s, e in hits:
        lines.append("[{}] {} —— {}".format(e["id"], e["title"], e.get("summary", "")))
    return "ok", "\n".join(lines)


# ──────────────────── 模糊检索（变更 #14 增补）────────────────────
# 原实现是整串子串匹配：用户问「四则混合运算的顺序是什么」，提取的主题带"的"字，
# 永远匹配不上标题「四则混合运算顺序」。改为二元字组重叠打分，问法随便变都能中。
_FILLERS = ("为什么", "是什么", "什么意思", "怎么", "怎样", "如何", "是不是",
            "有没有", "请问", "告诉我", "什么", "哪些", "的", "了", "吗",
            "呢", "吧", "啊", "呀", "嘛", "哪")


def _clean_topic(text):
    t = (text or "").strip().strip("？?。！!，,、；; ")
    for f in _FILLERS:
        t = t.replace(f, "")
    return t.strip("？?。！!，,、；; ")


def _bigrams(s):
    if len(s) >= 2:
        return {s[i:i + 2] for i in range(len(s) - 1)}
    return {s} if s else set()


def _match(topic, kb):
    """主题与「标题+简介」的二元字组重合率打分，返回 [(分数, 条目)] 降序。"""
    topic = _clean_topic(topic)
    if not topic:
        return []
    tb = _bigrams(topic)
    scored = []
    for zone in kb.catalog():
        for e in zone["entries"]:
            hay = _bigrams(e["title"] + e.get("summary", ""))
            if not hay:
                continue
            hit = len(tb & hay)
            if hit:
                scored.append((hit / max(len(tb), 1), e))
    scored.sort(key=lambda x: -x[0])
    return [(s, e) for s, e in scored if s >= 0.3][:5]


def search_rescue(text, kb):
    """unknown 兜底检索：整句清洗后找目录；有货就直接读最好的一条并列出其余。
    知识库断连/读不到时静默返回空串，由上层走原有兜底——不制造新故障。"""
    try:
        hits = _match(text, kb)
    except KBError:
        return ""
    if not hits:
        return ""
    _, best = hits[0]
    try:
        e = kb.read(best["id"])
    except KBError:
        return ""
    out = ["目录里找到一条最相关的——{}「{}」：{}".format(best["id"], best["title"], e["summary"]),
           "【详细使用方法】{}".format(e["usage"]),
           "【使用经验】{}".format(e["experience"]),
           "【局限性】{}".format(e["limitations"])]
    if len(hits) > 1:
        out.append("另外还相关：" + "、".join(
            "{}「{}」".format(x["id"], x["title"]) for _, x in hits[1:]))
    return "\n".join(out)


WHITELIST = {
    "describe": _describe,
    "search": _search,
}


def execute(action, slots, kb):
    """只执行白名单内动作；清单外返回拒绝（需要请示）。"""
    fn = WHITELIST.get(action)
    if fn is None:
        return "refused", "动作「{}」不在白名单内，我需要请示后才能学习它。".format(action)
    return fn(slots, kb)


# ──────────────────── 执行结果三态（变更 #26 补账，规格 §5）────────────────────
# 成功 / 失败可重试（≤3 次）/ 失败需人工（上报并暂停该类动作——连续失败的动作类进
# suspended 名单，后续同类直接提示人工，由你确认后恢复）。
RETRYABLE_STATUS = (408, 500, 502, 503, 504)   # 超时/服务端错误才值得重试；404/403 重试无意义
MAX_ATTEMPTS = 3


def execute_3state(action, slots, kb, suspended=None):
    """三态执行包装。返回 (status, out, attempts)。
    suspended：会话状态里的暂停名单（list），同类动作在名单里直接走人工通道。"""
    if action in (suspended or []):
        return "error", ("动作「{}」此前连续失败，已按规矩暂停——"
                         "这类问题需要你过目；确认修复后说「恢复执行 {}」我再开工".format(
                             action, action)), 0
    attempts = 0
    while True:
        attempts += 1
        try:
            status, out = execute(action, slots, kb)
        except KBError as e:
            if e.status in RETRYABLE_STATUS and attempts < MAX_ATTEMPTS:
                continue                    # 瞬时故障：重试
            if e.status == -1:
                raise                       # 断连语义不变（规格 #16：立即降级）
            return "error", "执行失败（{} 次尝试）：[{}] {}".format(
                attempts, e.status, e.message), attempts
        else:
            # 动作返回的 error 多是确定性失败（403 无权限等），重试无意义——直接交差；
            # 只有抛出来的瞬时故障（408/5xx）才走上面的重试循环
            return status, out, attempts
