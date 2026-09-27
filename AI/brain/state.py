# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】上下文和对话记录全落盘（规格 §7）；【施工方】schema 与原子写
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""会话状态落盘（规格书第 7 节：对话状态全落盘，崩溃可恢复）。
history / events / weights / aging / stats 全部进 state\\session.json，每轮结束保存。"""
import json

from . import attention, config

SESSION_FILE = config.STATE / "session.json"


class SessionState:
    def __init__(self):
        self.history = []         # [{"role","text","time","slice","thinking"?}]
        self.events = []          # 见 events.py
        self.weights = dict(attention.SEED_WEIGHTS)   # 类型注意力权重（慢变量）
        self.aging = {}           # 事件 id → 老化增量
        self.stats_counts = {}    # 在线频率统计
        self.counter = 0          # 轮次计数（训练节奏按它走）
        self.eid = 0              # 事件编号独立计数（不能和轮次共用，否则训练节奏被打乱）
        self.pending = None       # 追问中的槽位 {"action","slots"}
        # 切片式聊天（2026-09-25）：每条历史归一个切片；active=新话落入的切片，
        # merged=与 active 合并可见的切片集合（AI 的上下文视野 = active ∪ merged）
        self.slices = {"main": {"name": "主切片", "created": ""}}
        self.active_slice = "main"
        self.merged = []
        self.recent_entities = []   # 最近提到的对象（指代回填先行词池，新→旧）
        self.recent_acts = []       # 最近 5 轮对话行为类型（变更 #30 条文③语境加权的原料）
        # 用户模型（规格 §7：稳定层慢变量 + 状态层快变量，仅限助手服务目的）
        self.usermodel = {"stable": {}, "state": {}}
        self.num_bindings = {}   # 数值绑定表（变更 #24）：「密度是7.8」→ {"密度": 7.8}
        self.suspended_actions = []   # 执行三态（变更 #26）：连续失败被暂停的动作类

    def visible_history(self):
        """AI 与用户当前可见的对话范围：活动切片 ∪ 合并切片（按时间原序）。"""
        scope = {self.active_slice} | set(self.merged)
        return [h for h in self.history if h.get("slice", "main") in scope]

    @classmethod
    def load(cls):
        st = cls()
        if SESSION_FILE.exists():
            try:
                data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                st.history = data.get("history", [])
                st.events = data.get("events", [])
                slices = data.get("slices") or {}
                st.slices = {"main": {"name": "主切片", "created": ""}}
                for sid, meta in slices.items():
                    if isinstance(meta, dict) and isinstance(meta.get("name"), str):
                        st.slices[sid] = {"name": meta["name"][:30],
                                          "created": str(meta.get("created", ""))}
                active = data.get("active_slice", "main")
                st.active_slice = active if active in st.slices else "main"
                st.merged = [s for s in (data.get("merged") or []) if s in st.slices]
                # 变更 #27：条目可为 {val, slice}（带切片标签，按可见域过滤）；
                # 旧格式的纯字符串原样保留（视为全可见域遗留，不溯及）
                pool = []
                for x in (data.get("recent_entities") or []):
                    if isinstance(x, dict) and x.get("val"):
                        pool.append({"val": str(x["val"])[:20],
                                     "slice": str(x.get("slice", "main"))[:20]})
                    elif x:
                        pool.append(str(x)[:20])
                st.recent_entities = pool[-20:]
                um = data.get("usermodel") or {}
                if isinstance(um, dict):
                    st.usermodel = {"stable": um.get("stable") or {},
                                    "state": um.get("state") or {}}
                nb = data.get("num_bindings") or {}
                if isinstance(nb, dict):
                    st.num_bindings = {str(k)[:10]: float(v) for k, v in nb.items()
                                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
                st.suspended_actions = [str(a)[:20] for a in
                                        (data.get("suspended_actions") or [])][:10]
                # 第三轮审查 R3-3：落盘的权重逐键校验——只认种子表里的类型、
                # 必须是数值、并按种子值 ±20% 钳制（与 adjust_weight 同一把尺子），
                # 防止手改/损坏的 session.json 把权重拉到 0 或无穷大绕过训练层约束。
                for k, v in (data.get("weights") or {}).items():
                    seed = attention.SEED_WEIGHTS.get(k)
                    if seed is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                        continue
                    lo, hi = round(seed * 0.8, 3), round(seed * 1.2, 3)
                    st.weights[k] = max(lo, min(hi, float(v)))
                st.aging = data.get("aging", {})
                st.stats_counts = data.get("stats_counts", {})
                st.counter = data.get("counter", 0)
                st.eid = data.get("eid", len(st.events))
                st.pending = data.get("pending")
                st.recent_acts = [str(a)[:10] for a in
                                  (data.get("recent_acts") or [])][-5:]
            except ValueError:
                pass   # 文件损坏则冷启动；知识库数据不受影响
        return st

    def save(self):
        config.ensure()
        # 截断策略（第二轮审查 L4 加固）：优先保住未完成的开放事件
        # （待办提醒绝不能因为对话多就被挤掉）， aging 同步清理不残留死 id。
        tail = self.events[-500:]
        open_extra = [e for e in self.events[:-500] if e["state"] in ("待办", "进行中")]
        self.events = open_extra + tail
        live = {e["id"] for e in self.events}
        self.aging = {k: v for k, v in self.aging.items() if k in live}
        self.history = self.history[-200:]
        config.atomic_write(SESSION_FILE, json.dumps({
            "history": self.history,            # 对话记录软上限：最近 200 轮
            "events": self.events,              # 事件软上限：最近 500 条 + 全部未完成
            "weights": self.weights,
            "aging": self.aging,
            "stats_counts": self.stats_counts,
            "counter": self.counter,
            "eid": self.eid,
            "pending": self.pending,
            "slices": self.slices,
            "active_slice": self.active_slice,
            "merged": self.merged,
            "recent_entities": self.recent_entities,
            "recent_acts": self.recent_acts[-5:],
            "num_bindings": self.num_bindings,
            "suspended_actions": self.suspended_actions,
            "usermodel": {"stable": self.usermodel.get("stable", {}),
                          "state": {}},   # 快变量不落盘：状态层只活在本轮
        }, ensure_ascii=False, indent=2))

    def next_id(self, prefix="E"):
        self.eid += 1
        return "{}{}".format(prefix, self.eid)
