# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【拍板】切片式聊天与思考过程输出（设计者提的现代 AI 聊天模式：切片可合并、思考过程文字可见）
# 【施工方】REST 实现与回收站
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""INCEPTION 聊天端点（变更 #14）：把 AI 本体 brain 接进知识库服务。

与安全边界的关系：聊天就是管理员本人与 AI 对话——沿用 admin_guard
（仅本机 + 管理密钥），不新增任何对外暴露面。

思考过程可见（设计者要求）：brain 每轮 handle() 本来就产出完整 task 档案
（语言/分词/意图候选/置信/注意力扫描序/推演链/预测编号……），此处原样捕获，
随回复一并返回，由前端折叠展示——数据现成，只是开窗。
"""
import sys
from pathlib import Path

from fastapi import APIRouter, Depends

from .admin_api import admin_guard

AI_DIR = Path(__file__).resolve().parents[2] / "AI"   # D:\cangku\AI
if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))

router = APIRouter(prefix="/admin/api/inception", dependencies=[Depends(admin_guard)])

_sess = None
_brain_err = None


def _session():
    """单例会话；构造失败（如密钥/状态损坏）记为断连，下一轮重试。"""
    global _sess, _brain_err
    if _sess is not None:
        return _sess
    try:
        from brain.session import Session
        _sess = Session()
        _brain_err = None
    except Exception as e:
        _brain_err = "{}: {}".format(type(e).__name__, e)
    return _sess


# 思考链捕获：session.handle 内部调 trace.record(task) 留档，
# 包一层把 task 原样接住（非侵入，brain 代码零改动）。
_captured = {}


def _wrap_trace():
    try:
        from brain import trace
    except Exception:
        return
    if getattr(trace, "_inception_wrapped", False):
        return
    orig = trace.record

    def recording(task):
        p = orig(task)
        _captured["task"] = task
        return p

    trace.record = recording
    trace._inception_wrapped = True


# 前端"思考过程"面板展示的字段白名单（有就带，没有就不带）
TRACE_KEYS = ["thinking", "lang", "act", "decision", "confidence", "candidates",
              "unknown_words", "fired_reminders", "reason_trace", "prediction",
              "verified", "scan_order", "batch", "suggestions", "trained",
              "exec_status", "atomic_op", "kb_down", "resource", "open_events",
              "archive"]


@router.post("/chat")
def chat(body: dict):
    text = str(body.get("text", "")).strip()
    if not text:
        return {"ok": False, "error": "空消息"}
    if len(text) > 2000:
        return {"ok": False, "error": "消息过长（>2000 字）"}
    sess = _session()
    if sess is None:
        return {"ok": False, "error": "AI 本体不可用：{}".format(_brain_err)}
    _wrap_trace()
    _captured.clear()
    try:
        reply = sess.handle(text)
    except Exception as e:
        return {"ok": False, "error": "处理异常：{}: {}".format(type(e).__name__, e)}
    task = _captured.get("task", {})
    trace_out = {k: task[k] for k in TRACE_KEYS if task.get(k) not in (None, [], {}, "")}
    return {"ok": True, "reply": reply, "trace": trace_out,
            "round": sess.state.counter}


@router.get("/history")
def history(n: int = 20):
    """最近对话（刷新页面后恢复聊天流）。n≤50。只回当前可见范围（活动切片∪合并切片）。"""
    sess = _session()
    if sess is None:
        return {"ok": False, "error": "AI 本体不可用：{}".format(_brain_err), "items": []}
    items = sess.state.visible_history()[-min(max(n, 1), 50):]
    return {"ok": True, "items": items, "round": sess.state.counter,
            "active": sess.state.active_slice, "merged": sess.state.merged}


# ──────────────────── 切片式聊天（2026-09-25，用户设计：多分区 + 可合并上下文）────────────────────

def _trash_file():
    from brain import config as _bcfg
    return _bcfg.STATE / "slice_trash.json"


def _trash_load():
    import json as _json
    f = _trash_file()
    if f.exists():
        try:
            return _json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"items": []}


def _trash_save(t):
    import json as _json
    from brain import config as _bcfg
    _bcfg.ensure()
    _bcfg.atomic_write(_trash_file(), _json.dumps(t, ensure_ascii=False, indent=2))


TRASH_KEEP_SECONDS = 86400   # 回收站保留 1 天（用户拍板 2026-09-25），过期自动清


def _trash_purge_expired():
    """满 1 天的回收站条目自动彻底清除。返回是否有清理发生。"""
    import datetime as _dt
    t = _trash_load()
    now = _dt.datetime.now()
    keep = []
    for it in t["items"]:
        try:
            gone = now - _dt.datetime.fromisoformat(it["deleted_at"])
        except ValueError:
            gone = _dt.timedelta(days=999)
        if gone.total_seconds() < TRASH_KEEP_SECONDS:
            keep.append(it)
    if len(keep) != len(t["items"]):
        t["items"] = keep
        _trash_save(t)
        return True
    return False

def _slice_view(sess):
    """切片视图：名字/条数之外，带最后一条用户消息的预览与时间——
    侧栏的「最近记录」就是切片列表本身（变更 #27，用户设计：
    最近记录要变成切片，不再是原始消息流）。"""
    h = sess.state.history
    out = []
    for sid, meta in sess.state.slices.items():
        recs = [x for x in h if x.get("slice", "main") == sid]
        last_u = next((x for x in reversed(recs) if x["role"] == "user"), None)
        out.append({"id": sid, "name": meta["name"],
                    "count": sum(1 for x in recs if x["role"] == "user"),
                    "last": (last_u["text"][:24] if last_u else ""),
                    "last_time": (recs[-1].get("time", "") if recs
                                  else meta.get("created", ""))})
    return {"ok": True, "active": sess.state.active_slice, "merged": sess.state.merged,
            "slices": out}


def _unique_name(st, name, exclude=None):
    """切片名去重：撞名自动加序号（化学实验室 → 化学实验室 2）——
    三个同名切片排在一起，合并勾选时根本分不清谁是谁。"""
    names = {meta["name"] for sid, meta in st.slices.items() if sid != exclude}
    if name not in names:
        return name
    i = 2
    while "{} {}".format(name, i) in names:
        i += 1
    return "{} {}".format(name, i)


@router.get("/slices")
def slices():
    sess = _session()
    if sess is None:
        return {"ok": False, "error": "AI 本体不可用：{}".format(_brain_err)}
    return _slice_view(sess)


def _fresh_sid(st):
    """生成不与现存切片、回收站条目冲突的 sid——
    len()+1 在删除后会撞号；回收站按 sid 认领条目，撞号会找错对象。"""
    used = set(st.slices) | {it["sid"] for it in _trash_load()["items"]}
    n = len(st.slices) + 1
    while "s{}".format(n) in used:
        n += 1
    return "s{}".format(n)


@router.post("/slices/{op}")
def slice_op(op: str, body: dict):
    """切片操作：new{name} / active{id} / merged{ids[]} / rename{id,name} /
    del{id} / trash / restore{id} / purge{id}。"""
    sess = _session()
    if sess is None:
        return {"ok": False, "error": "AI 本体不可用：{}".format(_brain_err)}
    st = sess.state
    if op == "new":
        name = str(body.get("name", "")).strip()[:30] or "切片 {}".format(len(st.slices) + 1)
        name = _unique_name(st, name)
        sid = _fresh_sid(st)
        import datetime as _dt
        st.slices[sid] = {"name": name, "created": _dt.datetime.now().isoformat(timespec="seconds")}
        st.active_slice = sid          # 新切片立即成为活动切片
        st.merged = []
    elif op == "active":
        sid = str(body.get("id", ""))
        if sid not in st.slices:
            return {"ok": False, "error": "没有这个切片：" + sid}
        st.active_slice = sid
    elif op == "merged":
        ids = body.get("ids") or []
        st.merged = [s for s in ids if s in st.slices and s != st.active_slice]
    elif op == "rename":
        sid = str(body.get("id", ""))
        name = str(body.get("name", "")).strip()[:30]
        if sid not in st.slices:
            return {"ok": False, "error": "没有这个切片：" + sid}
        if name:
            st.slices[sid]["name"] = _unique_name(st, name, exclude=sid)
    elif op == "del":
        sid = str(body.get("id", ""))
        if sid == "main":
            return {"ok": False, "error": "主切片不能删"}
        if sid not in st.slices:
            return {"ok": False, "error": "没有这个切片：" + sid}
        # 回收站（用户拍板）：删除 → 留 1 天，期间可恢复或立刻彻底删，过期自动清
        import datetime as _dt
        _trash_purge_expired()
        t = _trash_load()
        t["items"].append({
            "sid": sid, "name": st.slices[sid]["name"],
            "created": st.slices[sid].get("created", ""),
            "deleted_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "records": [h for h in st.history if h.get("slice", "main") == sid],
        })
        _trash_save(t)
        st.history = [h for h in st.history if h.get("slice", "main") != sid]
        del st.slices[sid]
        st.merged = [s for s in st.merged if s != sid]
        if st.active_slice == sid:
            st.active_slice = "main"
    elif op == "trash":
        # 回收站清单（先清过期）
        _trash_purge_expired()
        t = _trash_load()
        return {"ok": True, "items": [
            {"sid": it["sid"], "name": it["name"], "deleted_at": it["deleted_at"],
             "count": sum(1 for r in it["records"] if r.get("role") == "user")}
            for it in reversed(t["items"])]}
    elif op == "restore":
        sid = str(body.get("id", ""))
        t = _trash_load()
        it = next((x for x in t["items"] if x["sid"] == sid), None)
        if not it:
            return {"ok": False, "error": "回收站里没有这个切片"}
        nsid = sid if sid not in st.slices else _fresh_sid(st)
        st.slices[nsid] = {"name": _unique_name(st, it["name"]), "created": it.get("created", "")}
        # 记录里的 slice 字段要重写到新 sid——否则 sid 被占用时记录归到旧名下，永远隐身
        st.history.extend(dict(h, slice=nsid) for h in it["records"])
        st.history.sort(key=lambda h: h.get("time", ""))
        t["items"] = [x for x in t["items"] if x["sid"] != sid]
        _trash_save(t)
        st.active_slice = nsid
        st.merged = []
    elif op == "purge":
        sid = str(body.get("id", ""))
        t = _trash_load()
        n0 = len(t["items"])
        t["items"] = [x for x in t["items"] if x["sid"] != sid]
        if len(t["items"]) == n0:
            return {"ok": False, "error": "回收站里没有这个切片"}
        _trash_save(t)
    else:
        return {"ok": False, "error": "未知操作：" + op}
    st.save()
    return _slice_view(sess)


@router.get("/usermodel")
def usermodel_view():
    """用户模型查看（规格 §7：每个变量带出处；设置页可见）。"""
    sess = _session()
    if sess is None:
        return {"ok": False, "error": "AI 本体不可用：{}".format(_brain_err)}
    from brain import usermodel
    return {"ok": True, "model": sess.state.usermodel,
            "text": usermodel.fmt_for_settings(sess.state.usermodel)}


# ──────────────────── 建议队列（批三：发现机制产出 → 审批中心可见可裁决）────────────────────
# 队列在 AI 侧 state/suggestions.json（refine.py 管），此前只能命令行裁决；
# 这里开窗到管理界面。采纳路径沿用现有设计：词典类本地生效，其余走 REQ 通道终审。

def _refine():
    from brain import refine
    return refine


@router.get("/suggestions")
def suggestions():
    """列出 AI 建议队列（待裁决排前，新的排前）。"""
    try:
        q = _refine()._load_queue()
    except Exception as e:
        return {"ok": False, "error": "建议队列读取失败：{}: {}".format(type(e).__name__, e),
                "items": [], "pending": 0}
    items = sorted(q["items"],
                   key=lambda it: (it["status"] != "待裁决",
                                   -int("".join(ch for ch in it["id"] if ch.isdigit()) or 0)))
    pend = sum(1 for it in q["items"] if it["status"] == "待裁决")
    return {"ok": True, "items": items, "pending": pend}


@router.post("/suggestions/{sid}/apply")
def apply_suggestion(sid: str):
    """采纳建议：词典类直接录入本地词典；缺口/提炼/晋升类走 REQ 通道提交知识库终审。"""
    try:
        refine = _refine()
        it = refine._find(refine._load_queue(), sid)
        if it is None:
            return {"ok": False, "error": "没有找到 " + sid}
        if it["status"] != "待裁决":
            return {"ok": False, "error": "{} 已是「{}」，不能重复处置".format(sid, it["status"])}
        msg = refine.apply_lexicon(sid) if it["kind"] == "lexicon" else refine.submit_req(sid)
        return {"ok": True, "msg": msg}
    except Exception as e:
        return {"ok": False, "error": "处置失败：{}: {}".format(type(e).__name__, e)}


@router.post("/suggestions/{sid}/drop")
def drop_suggestion(sid: str):
    """拒绝建议（留档，不删记录）。"""
    try:
        refine = _refine()
        it = refine._find(refine._load_queue(), sid)
        if it is None:
            return {"ok": False, "error": "没有找到 " + sid}
        if it["status"] != "待裁决":
            return {"ok": False, "error": "{} 已是「{}」，不能重复处置".format(sid, it["status"])}
        return {"ok": True, "msg": refine.drop(sid)}
    except Exception as e:
        return {"ok": False, "error": "处置失败：{}: {}".format(type(e).__name__, e)}
