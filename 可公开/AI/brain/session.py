# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】上下文和语境（规格 §2）；自我认知通路（变更 #29 用户举报驱动）
# 【施工方】会话主线编排；空闲推进器（变更 #30——排序的消费者，机制为施工方补建，需求是设计者原话「动态权重排序」）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""会话主循环（第三站：接入训练层与建议队列，规格书第 2/3/5/6/7/9 节）：

每轮流程：资源检查 → 到期提醒 → 语言识别 → 分词 → 对话行为分类 → 意图解析
        → 事件抽取 → 急诊预筛 → 注意力调度（K=3）→ 白名单执行 → 回复
        → 在线统计衰减 → 训练层（到点调权重/收建议）→ 状态落盘 → 档案留痕

用法：
    python -m brain.session                交互模式（输入「退出」结束）
    python -m brain.session --script "你好" "提醒我晚上11点喝水" ...
"""
import json
import re
import sys
from datetime import datetime

from . import (attention, atoms, compose, config, discovery, dlgact, events, executor,
               intent, kb, langid, lexicon, parse, predict, reason, refine, resources,
               ruleload, segment, state as state_mod, stats, timenorm, trace, train,
               usermodel)
from ai_client import KBError

SAFETY_ADVICE = ("【安全建议】这可能是紧急情况，请立即：1) 开窗通风；2) 撤离到室外；"
                 "3) 不要开关任何电器（包括灯和手机电源键之外的电器操作）；"
                 "4) 到室外后拨打燃气公司抢修电话或 119。"
                 "我没有执行器，只能提醒，行动要靠你。")

# 变更 #24（甲 10）：社交句式也组装，不留整句写死的文案——
# 问候 = 时段词 + 在场 + 待办状态；缺槽反问 = 槽位问法 + 示例，由槽位描述数据组装
_SLOT_DESC = {"time": ("几点", "晚上8点 / 早上7点30分"),
              "thing": ("提醒我做什么", None)}


def _followup_hint(slot):
    ask, ex = _SLOT_DESC.get(slot, ("请补充" + slot, None))
    return ask + "？" + ("（例如：{}）".format(ex) if ex else "")

# 第二轮审查 L1：追问回填的退出通道
PENDING_CANCEL_WORDS = {"算了", "取消", "不用了", "没事了", "当我没说", "不用提醒了"}
PENDING_TTL_ROUNDS = 5   # 追问挂起超过 5 轮自动作废，按新输入正常处理

# 变更 #11：原子操作指令识别（计算/比较/换算时间/查库）
_ATOM_TIME_PAT = re.compile(r"^(现在几点|换算\s*\d|\d+(?:\.\d+)?(?:秒|分钟|小时|天)后是几点|还有多久到)")
# 变更 #14 增补：「1+2×3等于几」「十乘十等于多少」直写算式（中/阿数字均可；
# 只放行算式字符与「加减乘除（以）」字样，且归一化后须含运算符，防误伤日常语句。
# 「以」仅服务于「除以/乘以」，且整句必须收尾于「等于几/多少」，日常语句进不来）
_ATOM_CALC_PAT = re.compile(
    r"^([\d零一二三四五六七八九十百千万两\s+\-*/×÷%().（）加减乘除以]+?)"
    r"(?:等于|得)(?:几|多少|什么)?[?？]?$")


_EQ_HINT = re.compile(r"[xX]|未知数|某数|这个数")


def atoms_hit(text, bindings=None):
    t = text.strip()
    if (t.startswith(("计算", "算一下", "比较", "查库"))
            or bool(_ATOM_TIME_PAT.match(t))
            or bool(_ATOM_CALC_PAT.match(t))):
        return True
    # 变更 #24（甲 7）：方程句式——含未知数词 + 等号字样 + 疑问收尾
    if _EQ_HINT.search(t) and ("等于" in t or "=" in t) and \
            t.rstrip("？?").endswith(("几", "多少", "什么")):
        return True
    # 变更 #24（甲 6）：绑定名直写算式「密度乘以2等于几」——开头词须在绑定表里才放行
    # （绑定表是硬门槛，日常语句进不来）
    if bindings:
        m = re.match(r"^([一-龥]{2,6})(?:乘以|乘|除以|加|减)", t)
        if m and m.group(1) in bindings and \
                re.search(r"(?:等于|得)(?:几|多少|什么)?[?？]?$", t):
            return True
    return False


class Session:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.state = state_mod.SessionState.load()
        self.stats = stats.Stats(self.state.stats_counts)
        self.kb_client = None
        self.refiner = refine.NeuralRefiner()
        # 规则入库（变更#7）：启动时从知识库拉 G 类规则；断连/没有则种子兜底
        self.rules = None
        self.rules_source = "seed"
        try:
            self.rules = ruleload.load_rules(self._kb())
        except Exception:
            self.rules = None
        if self.rules:
            self.rules_source = "KB({})".format(len(self.rules))
        # 变更 #27：分词词典 = 本地种子 ∪ 语法词典 ∪ 规则词表 ∪ KB目录词条名
        # （规格 §1 停止线「词典=知识库词条+AI本地种子词典」——词表不进分词词典，
        #  「铁/潮湿」这种规则里明明有的词也会被误判生词）
        self._lex = lexicon.load_all()
        self._lex_update_from_knowledge()
        if not stats.LOG_FILE.exists():
            self._log_seed_weights()

    def _lex_update_from_knowledge(self):
        """把规则词表（when/then 的变量与值）和知识库目录词条标题并入分词词典。
        断连降级：只用规则词表（种子规则兜底）；目录拉不到不致命。"""
        vocab = set()
        for r in (self.rules or reason.SEED_RULES):
            for cond in list(r.get("when", [])) + [r.get("then", "")]:
                for part in re.split(r"[=;]", cond or ""):
                    w = part.strip()
                    if 1 <= len(w) <= 6 and re.match(r"^[一-龥A-Za-z0-9]+$", w):
                        vocab.add(w)
        client = self._kb()
        if client is not None:
            try:
                for z in client.catalog() or []:
                    for e in z.get("entries", []):
                        t = (e.get("title") or "").strip()
                        if 1 <= len(t) <= 6:   # 超出分词器最长词长的标题进不来
                            vocab.add(t)
            except Exception:
                pass   # 断连/无权限：目录词条缺席，规则词表已够兜底
        for w in vocab:
            if w not in self._lex:
                self._lex[w] = {"词性": "词条", "释义": "知识库条目/规则词表"}

    def _log_seed_weights(self):
        """首次运行：把施工方填的种子权重记进 weights.log（明文可查）。"""
        for t, w in sorted(self.state.weights.items()):
            stats.log_weight_change("INIT(施工方填的种子值)", None, w)

    # ---- 知识库（断连优雅降级：规格 #16——本地任务照常）----
    def _kb(self):
        if self.kb_client is None:
            self.kb_client = kb.get_client(self.api_key)
        return self.kb_client

    # ---- 类型权重调节（慢变量；±20% 界内、每次变化记 weights.log）----
    def adjust_weight(self, etype, new, signal="ADMIN"):
        seed = attention.SEED_WEIGHTS.get(etype, 1.0)
        lo, hi = round(seed * 0.8, 3), round(seed * 1.2, 3)
        new = max(lo, min(hi, round(float(new), 3)))
        old = self.state.weights.get(etype)
        if old == new:
            return
        self.state.weights[etype] = new
        stats.log_weight_change("{}:{}".format(signal, etype), old, new)
        self.state.save()   # 立即落盘，避免日志与状态不一致

    # ---- 主循环 ----
    def handle(self, text, now=None):
        now = now or datetime.now()
        task = {"round": self.state.counter + 1, "input": text}
        replies = []

        # 0) 资源软上限
        res_status, res_msg = resources.check_and_throttle()
        task["resource"] = res_msg

        # 1) 到期提醒（先于一切：时间到了就是到了）
        #    变更 #10：提醒触发是天然对账时机——顺带带出待验证的推演预测
        fired = []
        for e in events.due_events(self.state.events, now):
            e["state"] = "完成"
            self.state.aging.pop(e["id"], None)
            fired.append(e["id"])
            replies.append("⏰ 到时间了：{}".format(e["object"] or e["note"]))
        task["fired_reminders"] = fired
        if fired:
            hint = predict.fmt_pending()
            if hint:
                replies.append(hint)
                task["verify_prompted"] = True

        # 2) 语言识别
        lang = langid.detect(text)

        # 3) 分词 + 生词（变更 #27：全量词典——种子∪语法∪规则词表∪KB词条名）
        tokens = segment.segment(text, self._lex)
        unknown = [w for k, w in tokens if k == "未知"]

        # 4) 对话行为 + 意图解析
        act = dlgact.classify(text)
        cands = intent.parse(text, tokens)
        chosen, decision, conf = intent.decide(cands)
        task.update(lang=lang, act=act, tokens=tokens, unknown_words=unknown,
                    candidates=cands, decision=decision, confidence=conf)
        # 变更 #17：人话思考链——每一步推理用看得懂的中文记下，前端按时间线展示
        _ACT_CN = {"question": "疑问句", "command": "命令",
                   "statement": "陈述", "greeting": "问候"}
        task["thinking"] = [
            "分类型：这句话是{}；{}".format(
                _ACT_CN.get(act, act),
                "命中指令模板「{}」".format(chosen["action"])
                if chosen["action"] != "unknown" else "没有命中任何指令模板")]

        # 变更 #24（甲 6）：数值绑定——「密度是7.8」类陈述进绑定表，后续算式按名代入
        bound = self._capture_bindings(text)
        if bound:
            task["thinking"].append("记下数值绑定：{}".format(
                "、".join("{}={}".format(k, v) for k, v in bound.items())))

        # 变更 #26（执行三态的恢复口）：「恢复执行 search」解除动作类暂停
        m_resume = re.match(r"^恢复执行\s*([一-龥a-zA-Z]+)$", text.strip())
        if m_resume:
            name = m_resume.group(1)
            if name in self.state.suspended_actions:
                self.state.suspended_actions.remove(name)
                task["thinking"].append("你确认修复，动作「{}」恢复执行".format(name))
                reply = "好，「{}」已恢复执行，再试一次看看。".format(name)
            else:
                reply = "「{}」不在暂停名单里（当前暂停：{}）。".format(
                    name, "、".join(self.state.suspended_actions) or "无")
            # 收尾与主管线同款：计数/用户模型/历史/落盘/留痕
            self.state.counter += 1
            usermodel.update(self.state, text, act, task)
            self.state.history.append({"role": "user", "text": text,
                                       "slice": self.state.active_slice,
                                       "time": now.isoformat(timespec="seconds")})
            self.state.history.append({"role": "ai", "text": reply,
                                       "slice": self.state.active_slice,
                                       "thinking": list(task.get("thinking", [])),
                                       "time": datetime.now().isoformat(timespec="seconds")})
            self.state.save()
            task["reply"] = reply
            task["slice"] = self.state.active_slice
            task["archive"] = str(trace.record(task))
            return reply

        # 5) 追问回填：上一轮缺槽位，这轮只补一个时间/内容也算数
        pending_reply = self._try_pending(text, now)
        if pending_reply is not None:
            replies.append(pending_reply)
            task["pending_filled"] = True
            task["thinking"].append("这是对上一轮追问的补充回答，回填了缺的槽位")
        elif decision == "ask":
            task["thinking"].append("几个候选的把握都不够，选择反问澄清")
            replies.append("我不太确定你的意思（最佳候选置信 {:.0%}）。你是想：{} 吗？".format(
                conf, " / ".join(c["note"] for c in cands[:2])))
        elif chosen["action"] == "remind":
            task["thinking"].append("识别为提醒指令：抽取时间和事项两个槽位")
            replies.append(self._do_remind(chosen["slots"], text, now))
        elif text.startswith("推演"):
            task["thinking"].append("推演指令：抽出事实，沿规则链正向传播，结论强制带置信")
            concl, rtrace, derived = reason.reason_about(text, rules=self.rules)
            task["reason_trace"] = rtrace
            self._add_event("推演", "用户", "推演", obj=text[:30], source=text)
            if concl:
                replies.append("推演结果（带置信标注，仅参考，需你裁决行动）：\n  " +
                                "\n  ".join(concl))
                # 变更 #10：结论存为预测，等提醒到期时自动提醒你验证
                pid = predict.save_prediction(text, derived, rtrace)
                if pid:
                    replies.append("（已存档为预测 {}。验证方式：说「验证 {} 命中」或「验证 {} 落空」）"
                                   .format(pid, pid, pid))
                    task["prediction"] = pid
            else:
                replies.append("没有可推演的内容。试试这种格式：推演 饥饿极高 无捍卫亲友意念 友人在场")
        elif text.startswith("验证"):
            # 变更 #10：预测对账——命中/落空都会记进规则晋升计数并留痕
            parsed = predict.parse_verify(text)
            if not parsed:
                replies.append("验证格式：验证 Y1 命中 / 验证 Y1 落空（待验证清单会在提醒到期时自动列出）")
            else:
                pid, confirmed = parsed
                ok, msg = predict.verify(pid, confirmed)
                replies.append(msg)
                task["verified"] = {"id": pid, "confirmed": confirmed, "ok": ok}
        elif atoms_hit(text, self.state.num_bindings):
            task["thinking"].append("识别为原子操作：算式/比较/时间换算/查库，直接执行确定性工具")
            replies.append(self._do_atoms(text, task))
        elif chosen["action"] == "unknown":
            replies.append(self._do_unknown(act, text, unknown, task))
        elif chosen["action"] == "search" and any(
                c["intent"] not in ("none", "command")
                for c in parse.parse(text)["clauses"]):
            # 变更 #29 模板劫持禁令：「你是什么」被「X是什么」模板劫去搜目录是
            # 本批用户举报的硬伤——带疑问焦点的句子一律先走组合式理解路
            # （理解路尽头本来就有目录检索兜底，什么也不丢）。
            task["thinking"].append("带疑问焦点，不用标题检索模板劫走，转组合式理解")
            replies.append(self._do_unknown(act, text, unknown, task))
        else:
            task["thinking"].append("按指令模板执行知识库动作「{}」".format(chosen["action"]))
            replies.append(self._do_kb_action(chosen, act, text, task))

        # 6) 急诊预筛（全部未完成事件过一遍；只对新命中的发建议。
        #    第二轮审查 L2：带时间的提醒事件发完建议不结案——
        #    否则"提醒我明天检查煤气阀门"这类提醒会在到期前被急诊预筛吞掉）
        emergency = [e for e in attention.prescreen(self.state.events)
                     if not e.get("advised")]
        if emergency:
            for e in emergency:
                e["advised"] = True
                if e.get("time"):
                    continue   # 未到期提醒：建议照发，提醒照常等触发
                e["state"] = "完成"   # 建议已送达；我没有执行器，行动在用户
                self.state.aging.pop(e["id"], None)
            replies.insert(0, SAFETY_ADVICE)
            task["emergency"] = [e["id"] for e in emergency]

        # 7) 注意力老化 + 本轮调度预算 + 空闲推进（变更 #30：排序第一次有了消费者——
        #    batch 序不再只是留痕展示，每轮答复后按序取第一件可推进事件推一步）
        attention.age(self.state.aging)
        # 变更 #26：追问挂起时，最近的未完成事件卡着对话主线 → 阻塞度计入事件分
        pend_id = None
        if self.state.pending:
            open_evs = [e for e in self.state.events if e["state"] in ("待办", "进行中")]
            if open_evs:
                pend_id = open_evs[-1]["id"]
        batch = attention.pick_batch(self.state.events, self.state.weights,
                                     self.state.aging, now, pending_event_id=pend_id,
                                     context_types=self._context_types(),
                                     urgent=bool(task.get("user_state") and
                                                 "紧迫" in task["user_state"]))
        task["scan_order"] = [e["id"] for e in
                              attention.scan_order([e for e in self.state.events
                                                    if e["state"] in ("待办", "进行中")],
                                                   self.state.weights, self.state.aging,
                                                   context_types=self._context_types())]
        task["batch"] = [{"id": e["id"], "type": e["type"],
                          "score": attention.score_event(
                              e, now, blocks_main=(e["id"] == pend_id))} for e in batch]
        pushed = self._idle_push(batch, task)
        if pushed:
            replies.append(pushed)
            task["idle_pushed"] = True

        # 8) 在线统计：行为/类型/生词命中 + 每轮衰减（生词是REQ的原料）
        self.stats.bump("act:" + act)
        # 变更 #30 条文③：行为类型序列落盘（最近 5 轮），语境加权的原料
        self.state.recent_acts = (self.state.recent_acts + [act])[-5:]
        for w in unknown:
            self.stats.bump("unknown:" + w)
        if task.get("emergency"):
            self.stats.bump("emergency_hit")
        self.stats.round_decay()
        self.state.stats_counts = self.stats.counts

        # 9) 训练层：结果对账 + 到点调类型权重 + 到点收建议队列
        train.on_round(self, task)
        trained = train.maybe_train(self)
        if trained:
            task["trained"] = trained
        if self.state.counter % train.TRAIN_EVERY == 0:
            fresh = refine.collect(self, refiner=self.refiner)
            # 经验提炼（变更#8）：矿工翻档案找规律 → 候选入建议队列
            fresh += refine.add_suggestions(discovery.mine(self.rules))
            # 规则晋升检查：对账攒够阈值的规则 → 建议移区 REQ（你终审）
            for p in ruleload.promotable(self.rules):
                fresh += refine.collect_rule_promote(self, p)
            # 变更 #28：知识类候选直通待审核区——挖出即打成 REQ
            # （知识与推理过程都填好），你在管理界面一处裁决；本地队列只作台账
            submitted = refine.auto_submit_reqs(fresh, self)
            if submitted:
                task["req_submitted"] = submitted
                task["thinking"].append(
                    "经验提炼：{} 条候选已填好知识和推理过程，打进待审核区（{}），等你裁决".format(
                        len(submitted), "、".join(submitted)))
            if fresh:
                task["suggestions"] = [c["id"] for c in fresh]

        # 10) 落盘 + 留痕
        self.state.counter += 1
        reply = "\n".join(replies)
        # 用户模型（规格 §7）：稳定层从累计统计归纳，状态层只估本轮
        usermodel.update(self.state, text, act, task)
        if task.get("user_state") and "紧迫" in task["user_state"]:
            task["thinking"].append("你似乎很急——我把结论放在了最前面")
        self.state.history.append({"role": "user", "text": text,
                                   "slice": self.state.active_slice,
                                   "time": now.isoformat(timespec="seconds")})
        # 思考链随历史存档（用户设计：推理过程要能看到，不能对话一结束就消失）
        self.state.history.append({"role": "ai", "text": reply,
                                   "slice": self.state.active_slice,
                                   "thinking": list(task.get("thinking", [])),
                                   "time": datetime.now().isoformat(timespec="seconds")})
        self.state.save()
        task["reply"] = reply
        task["slice"] = self.state.active_slice
        task["open_events"] = len([e for e in self.state.events
                                   if e["state"] in ("待办", "进行中")])
        task["archive"] = str(trace.record(task))
        return reply

    # ---- 动作实现 ----
    def _add_event(self, etype, subject, action, obj="", time=None,
                   source="", note=""):
        e = events.new_event(self.state.next_id("E"), etype, subject, action,
                             obj=obj, time=time, source=source, note=note)
        self.state.events.append(e)
        self.state.aging[e["id"]] = 0.0
        self.stats.bump("type:" + etype)   # 类型命中频率（训练层原料）
        return e

    # 批二：问题拆解——「拆得开就拆，拆不开就不拆」（规格 §4）。
    # 只接无副作用的解法（算式/推演/检索）；提醒等写操作不拆，留给单句流程，防半句误登记。
    _SPLIT_PAT = re.compile(r"(?:[，,；;。]\s*)?(?:再|然后|并且|还有|接着)")

    def _do_decompose(self, text, task):
        parts = [p.strip(" ？?。，,") for p in self._SPLIT_PAT.split(text)]
        parts = [p for p in parts if len(p) >= 2]
        if len(parts) < 2:
            return None
        results = []
        for p in parts:
            try:
                if atoms_hit(p, self.state.num_bindings):
                    results.append((p, self._do_atoms(p, {"round": task["round"], "input": p}), "计算"))
                    continue
                qa = reason.answer_question(p, rules=self.rules)
                if qa and qa["conclusions"]:
                    line = "；".join("{}（置信 {:.0%}）".format(c["then"], c["conf"])
                                     for c in qa["conclusions"])
                    results.append((p, line, "推演"))
                    continue
                need = reason.answer_need(p, rules=self.rules)
                if need and need["paths"]:
                    line = "；".join("「{}」需要 {}".format(x["goal"], "、".join(x["when"]))
                                     for x in need["paths"])
                    results.append((p, line, "反向推演"))
                    continue
                rescued = executor.search_rescue(p, self._kb())
                if rescued:
                    results.append((p, rescued, "检索"))
                    continue
            except Exception:
                pass
            return None   # 有一个子句解不了 → 不硬拆，整句退回原流程
        for i, (p, _, how) in enumerate(results, 1):
            task["thinking"].append("子问题 {}：「{}」→ 用{}解决".format(i, p, how))
        return "\n".join("{}. 「{}」\n  {}".format(i, p, r)
                         for i, (p, r, _) in enumerate(results, 1))

    # ──────────────────── 数值绑定（变更 #24，甲 6）────────────────────
    _BIND_PAT = re.compile(r"([一-龥]{2,6})(?:是|等于|为)(-?\d+(?:\.\d+)?)")
    # 这些词不是变量名：「温度是25」可以绑，「我是25」不行——人称/疑问词不收
    _BIND_STOP = {"我是", "你是", "它是", "这是", "那是", "什么是", "什么是"}

    def _capture_bindings(self, text):
        """从一句话里抓「名是数字」绑定进会话绑定表。返回新绑定的 {名: 值}。"""
        new = {}
        for name, num in self._BIND_PAT.findall(text):
            if name in self._BIND_STOP or len(name) > 6:
                continue
            v = float(num)
            self.state.num_bindings[name] = v
            new[name] = v
        while len(self.state.num_bindings) > 20:   # 绑定表软上限 20（先进先出）
            self.state.num_bindings.pop(next(iter(self.state.num_bindings)))
        return new

    def _compose_analogy(self, a):
        """类比假设组句（变更 #25）：明说只是类比，产物是待验证假设。"""
        conds = [c.replace(a["sub_var"] + "=" + a["old"],
                           a["sub_var"] + "=" + a["new"]) for c in a["when"]]
        cond_txt = "、".join(compose._humanize_cond(c) for c in conds)
        var, val = compose._parse_then(a["then"])
        pred = compose._assemble(var, val, a["new"])
        return ("「{}」我不认识——假如它和「{}」是同类，规则 {} 搬过来就是："
                "当{}时，{}（这只是类比，不是知识，得你确认或事实验证才算数；"
                "确认的话告诉我，我走 REQ 申请把规律扩展到它）".format(
                    a["new"], a["old"], a["rule"], cond_txt, pred))

    def _compose_struct_analogy(self, s):
        """结构类比组句（变更 #28）：跨域搬关系结构，双重免责（可靠性低于同位类比）。"""
        conds = [c for c in s["when"]
                 if not (s["subj_var"] and c.startswith(s["subj_var"] + "="))]
        cond_txt = "、".join(compose._humanize_cond(c) for c in conds) or "条件照搬"
        mapping = []
        if s["subj_old"] and s["subj_old"] != s["subj_new"]:
            mapping.append("{}={} → 主体={}".format(
                s["subj_var"], s["subj_old"], s["subj_new"]))
        mapping.append("{} → {}".format(s["goal_var"], s["goal_word"]))
        return ("「{}」我没有规则——但规则 {}（{}域）和你的问题结构同构，搬它的关系结构猜："
                "「{}」在「{}」这类条件下，{}{}"
                "（映射：{}。这是跨域结构类比，可靠性比同位类比更低，只是有根据的猜想方向，"
                "不是知识；确认的话告诉我，我走 REQ 申请验证）".format(
                    s["goal_word"], s["rule"], s["domain"], s["subj_new"], cond_txt,
                    s["goal_word"], "可能会发生" if s["val"] in ("会", "能", "可以") else "可能有",
                    "；".join(mapping)))

    # ──────────────────── 指代回填层（规格书 §2：代词绑定到对话先行词）────────────────────
    # 只收明确的指代词——「这/那/该」单字在「这是/应该/那里」里遍地都是，收了会误绑
    _PRONOUNS = ("它", "这个", "那个")

    def _note_entities(self, facts):
        """把本轮认出的实体记入「最近提到的对象」（指代回填的先行词池）。
        泛泛值（充足/会/能……）不是实体，进池会污染绑定，跳过。
        变更 #27：条目带切片标签——先行词池是 AI 的跨轮上下文，
        切片可见域（active∪merged）之外的先行词不参与绑定，
        「勾选合并=让它同时看到那几个切片的记录」由此真正生效。"""
        ents = self.state.recent_entities
        for f in facts or []:
            val = f.get("val")
            last = ents[-1] if ents else None
            last_val = last.get("val") if isinstance(last, dict) else last
            if (isinstance(val, str) and val and val not in reason.GENERIC_VALUES
                    and last_val != val):
                ents.append({"val": val, "slice": self.state.active_slice})
        del ents[:-20]   # 先行词池软上限

    def _say(self, c, rule_when, facts, task):
        """组句或弃权（变更 #30 条文⑤）：装配器弃权（变量名拆不出登记成分、
        动词未入库）时如实说「说不好」，并把变量名报进生词统计走学习回路——
        绝不把「它会星芒闪耀」式胡拼递给你。"""
        s = compose.compose_conclusion(c, rule_when=rule_when, facts=facts)
        if s is not None:
            return s
        var = compose._parse_then(c.get("then", ""))[0]
        task["thinking"].append("组句弃权：「{}」拆不出登记成分（动词未入库），不胡拼".format(var))
        self.stats.bump("unknown:" + var)
        return "「{}」这个词我说不好——它还没登记进词典，已记进生词申请学习".format(var)

    def _context_types(self):
        """语境类型集（变更 #30 条文③）：最近 5 轮你在聊什么行为类型的事，
        扫描序里同类事件加权——聊推演的日子里，推演欠账排前头。"""
        return set((self.state.recent_acts or [])[-5:])

    def _idle_push(self, batch, task):
        """空闲推进（变更 #30 条文①）：每轮答复后按调度序取第一件可推进事件推一步——
        排序第一次有了消费者，batch 序不再只是留痕展示。
        可推进 = 推演/知识查询类未完成事件（推进 = 沿当前规则库对它试推一遍）。
        推出结论 → 附在答复尾部（带置信标注），事件结案；推不出 → 缺口记思考链。
        每事件每日限推 1 次（e["pushed"] 日期戳）；绝不替你裁决行动。"""
        from datetime import date as _date
        today = _date.today().isoformat()
        for e in batch:   # batch 已是 pick_batch 排好的序
            if e["type"] not in ("推演", "知识查询"):
                continue
            if e.get("pushed") == today:
                continue
            q = (e.get("object") or e.get("source") or "").strip()
            if q.startswith("推演"):
                q = q[2:].strip()   # 指令壳剥掉，留问题本体
            if len(q) < 2:
                continue
            e["pushed"] = today
            try:
                qa = reason.answer_question(q, rules=self.rules)
            except Exception:
                qa = None
            thinking = task["thinking"]
            if qa and qa["conclusions"]:
                lines = [self._say(c, qa["by_id"].get(c["rule"], {}).get("when"),
                                   qa["facts"], task) for c in qa["conclusions"]]
                e["state"] = "完成"
                self.state.aging.pop(e["id"], None)
                thinking.append("空闲推进：按调度序轮到 {}，试推得出结论，事件结案".format(e["id"]))
                return "顺带：排在前面的「{}」我推出来了——{}".format(
                    q[:24], "；".join(lines))
            gap = "；".join("要推出「{}」还缺 {}".format(w["then"], "、".join(w["need"]))
                            for w in qa["wishes"]) if qa and qa["wishes"] else "没认出相关事实"
            thinking.append("空闲推进：{} 试推无结论（{}），挂着等条件".format(e["id"], gap))
            return None   # 无结论不刷屏，只留痕
        return None

    def _bind_pronoun(self, text, task):
        """话里有指代词且句中没自带可认实体时，把指代词绑到最近提到的对象上。
        返回用于推理的替换文本（原文不动，仅推理用）；绑定声明进思考链。
        变更 #27：先行词只从切片可见域（active∪merged）取——没合并的切片，
        它的对话记录 AI 看不到，指代也不会越域绑过去。
        旧格式的纯字符串条目（切片功能前的遗留）视为全可见域，不溯及。"""
        if not any(p in text for p in self._PRONOUNS):
            return text
        scope = {self.state.active_slice} | set(self.state.merged)
        ents = [e for e in self.state.recent_entities
                if isinstance(e, str) or e.get("slice", "main") in scope]
        if not ents:
            task["thinking"].append("话里有指代词，但可见范围里没有可指的对象——先按原句试着处理")
            return text
        vals = [e if isinstance(e, str) else e.get("val", "") for e in ents]
        if any(v and v in text for v in vals):
            return text   # 句中自带实体，指代词另有其所，不抢
        ent = vals[-1]
        for p in self._PRONOUNS:
            if p in text:
                task["thinking"].append(
                    "话里的「{}」没有明指——按上一个提到的「{}」理解（不对就纠正我）".format(p, ent))
                return text.replace(p, ent, 1)
        return text

    # ── 变更 #29：自我认知通路 ──
    # 问句主体指向 AI 自身时，从自我事实档案作答。自我事实存 AI 本地（它是关于
    # 系统自身的陈述，不是知识库条目）。判定法：剥掉人称代词/自我相关词/疑问词后
    # 不剩任何内容词 → 问的就是「我」。「你会生锈吗」剥完剩「生锈」→ 不是自我问题。
    _SELF_STRIP = ("INCEPTION", "Inception", "inception", "你", "您", "的", "名字",
                   "叫什么", "叫啥", "是", "什么", "谁", "吗", "么", "呢", "会", "能",
                   "做", "干", "到底", "究竟", "请问", "一下", "啊", "呀", "吧")
    _SELF_FACTS = {
        "名字": "INCEPTION",
        "本质": "一套规则推演加知识库的学习系统——靠规则链推理，靠知识库存知识，"
                "靠你的裁决学新东西",
        "能力": "算术与比较、规则推演、查知识库、提醒记事、切片式对话记忆、"
                "从对话里挖经验打成申请等你批准",
        "边界": "不联网、不擅自改知识库、推演的结论只供参考——裁决权在你",
    }

    def _is_self_question(self, text):
        if not re.search(r"你|您|[Ii][Nn][Cc][Ee][Pp][Tt][Ii][Oo][Nn]", text):
            return False
        rest = text
        for w in sorted(self._SELF_STRIP, key=len, reverse=True):
            rest = rest.replace(w, "")
        rest = re.sub(r"[，。；！？,!?;．、\s]", "", rest)
        return not rest

    def _answer_self(self, text, task):
        task["thinking"].append("问的是我自己：按自我事实档案作答（不走检索模板）")
        self._add_event("闲聊", "用户", "问AI自身", source=text)
        f = self._SELF_FACTS
        if re.search(r"名字|叫什么|叫啥", text):
            return "我叫 {}。".format(f["名字"])
        if re.search(r"会什么|能做什么|会干|能干|本事|功能|会些", text):
            return "我会{}。但{}。".format(f["能力"], f["边界"])
        if "谁" in text:
            return "我是 {}，{}。".format(f["名字"], f["本质"])
        return "我是 {}——{}。我会{}；{}。".format(
            f["名字"], f["本质"], f["能力"], f["边界"])

    # ── 变更 #29：纠正/反问通路 ──
    def _do_challenge(self, text, task):
        """「不是…吗 / 难道…」= 你在质疑库里的规则。不进问答流水线：
        复述确认你的纠正 → 与现有规则对比 → 差异打成 REQ 交你裁决（我不擅自改库）。"""
        thinking = task["thinking"]
        thinking.append("纠正/反问句：你在质疑我库里的说法，不走问答，走规则异议流程")
        # 找相关规则：结论变量/条件词与原文有公共子串（≥2）算沾边；
        # 排序按 命中词数×10+命中总字数——「铁生锈…氧气…水」最相关的是 G18
        # （命中 生锈+铁），不是只命中「氧气」的 G17/G27（变更 #29 活体复测抓包）
        scored = []
        for r in (self.rules or []):
            words = ([reason._parse_cond(r.get("then", ""))[0]]
                     + [reason._parse_cond(c)[0] for c in r.get("when", [])]
                     + [reason._parse_cond(c)[2] for c in r.get("when", [])])
            if reason._var_link([w for w in words if isinstance(w, str)], text) < 2:
                continue
            hits = {w for w in words if isinstance(w, str) and w and w in text}
            scored.append((len(hits) * 10 + sum(len(w) for w in hits), r))
        scored.sort(key=lambda x: -x[0])
        related = [r for _, r in scored]
        # 你声称的补充：内容词中去掉规则已覆盖的词与语法词
        covered = set()
        for r in related[:1]:   # 只算被对比的那条：别条规则的词（G27 的氧气）不算已覆盖
            for c in list(r.get("when", [])) + [r.get("then", "")]:
                covered.update(reason._parse_cond(c)[0:3:2])
        claimed = []
        # 功能词性前缀匹配（助词 涵盖 结构助词 等细分类）；查全量词典 self._lex——
        # 「的」只在内容词典登记助词，只查语法词典会漏排（变更 #29 活体复测抓包）
        _FUNC = ("介词", "连词", "助词", "副词", "语气词", "代词", "疑问词",
                 "否定词", "模态词", "程度词", "方位词", "时间词", "数词", "量词",
                 "疑问短语", "动词短语", "条件标记", "问候核")
        for kind, w in segment.segment(text, self._lex):
            if kind != "词" and kind != "未知":
                continue
            pos = (self._lex.get(w) or {}).get("词性") or ""
            if any(pos.startswith(p) for p in _FUNC):
                continue   # 功能词不是「你声称的条件」；名词/动词要留下——氧气/水正是
            if w in covered or w in ("吗", "么"):
                continue
            if len(w) >= 1 and w not in claimed and w not in (
                    "不是", "难道", "条件", "加上", "才能", "就是", "应该",
                    "是", "有"):   # 判断/存在动词：句子的骨架，不是补充的条件内容
                claimed.append(w)
        if not related:
            thinking.append("库里没有与这个话题相关的规则，无从比起——记成知识缺口申请")
            self._add_event("推演", "用户", "规则异议", obj=text[:30], source=text)
            return ("听懂了，你在纠正我。但库里没有关于这个话题的规则，无从比起——"
                    "你可以用新增知识把它立起来，我再照着学。")
        r = related[0]
        cond_txt = "、".join(r.get("when", []))
        thinking.append("对比现有规则 {}：当{}→则{}；你补充的是「{}」".format(
            r["id"], cond_txt, r.get("then", ""), "、".join(claimed) or "（没抽出来）"))
        # 差异打 REQ（去重：同目标待审核申请里已提过的词不重复打）
        rid = None
        client = self._kb()
        if client is not None and claimed:
            try:
                pend = [q for q in client.my_requests()
                        if q.get("status") == "待审核" and q.get("target") == r["id"]]
                if not any(all(c in q.get("proposal", "") for c in claimed)
                           for q in pend):
                    rid = client.propose(
                        "modify", target=r["id"],
                        proposal="条件补充（用户纠正）：本条还应考虑「{}」。原句：{}".format(
                            "、".join(claimed), text[:60]),
                        reason="用户在对话中以反问纠正本条规则，指出条件不全。",
                        proof="推理过程：纠正句识别→对比现有条件[{}]→用户补充[{}]未在其中。"
                              "证据档案：对话原文「{}」".format(cond_txt, "、".join(claimed),
                                                              text[:60]))
            except Exception:
                rid = None
        self._add_event("推演", "用户", "规则异议", obj=text[:30], source=text)
        if rid:
            thinking.append("异议已打成申请 {}，挂在审批中心等你裁决".format(rid))
            return ("听懂了，你在纠正我：这件事恐怕少不了「{}」。"
                    "我库里的 {} 现在只写「{} → {}」，没提这些。"
                    "差异我打成申请了（{}），挂在审批中心——你点头我才改库，"
                    "在那之前我还按 {} 推。".format(
                        "、".join(claimed), r["id"], cond_txt, r.get("then", ""),
                        rid, r["id"]))
        if client is None:
            return ("听懂了，你在纠正我：这件事恐怕少不了「{}」。"
                    "但知识库现在断连，申请打不进去——这轮对话已留档，连通后我再提。".format(
                        "、".join(claimed) or "你指出的条件"))
        return ("听懂了，你在纠正我。我库里的 {} 写的是「{} → {}」——"
                "审批中心已有一条针对它的待审申请，内容覆盖了你说的，不再重复打。".format(
                    r["id"], cond_txt, r.get("then", "")))

    def _do_unknown(self, act, text, unknown, task):
        thinking = task["thinking"]
        if act == "greeting":
            thinking.append("问候语，按时段和当前状态组装回应")
            self._add_event("闲聊", "用户", "问候", source=text)
            h = datetime.now().hour
            slot = ("早上" if 5 <= h < 9 else "上午" if 9 <= h < 12 else
                    "中午" if 12 <= h < 14 else "下午" if 14 <= h < 18 else "晚上")
            parts = ["{}好，我在".format(slot)]
            open_n = sum(1 for e in self.state.events
                         if e["state"] in ("待办", "进行中"))
            if open_n:
                parts.append("手上还有 {} 件待办没办完".format(open_n))
            return "。".join(parts) + "。"
        # 批七：组合式理解——先拆句（疑问焦点/否定/条件标记），再决定走哪条路
        parsed = parse.parse(text)
        active = [c for c in parsed["clauses"] if not c.get("absorbed")]
        if active and any(c["intent"] != "none" for c in active):
            foci = {"query_condition": "问条件", "query_how": "问方式", "query_why": "问原因",
                    "query_fact": "问事实", "query_confirm": "求确认", "command": "指令",
                    "challenge": "纠正/反问", "none": "—"}
            thinking.append("拆句：{} 个分句（{}）{}".format(
                len(active),
                "、".join(foci[c["intent"]] for c in active),
                "，带否定" if parsed["any_negated"] else ""))
        # 变更 #29：从句嵌套 v1——用户把条件包在句子里给了我（如果A，B／因为A所以B／虽然A但B）
        nested = next((c for c in active if c.get("rel") and c.get("cond_part")), None)
        if nested:
            thinking.append("句内嵌套：你把「{}」当{}给了我，主句按「{}」处理".format(
                nested["cond_part"], nested["rel"], nested["main_part"] or nested["raw"]))
            task["nested_cond"] = {"rel": nested["rel"], "cond": nested["cond_part"]}
        # 变更 #29：问句主体指向 AI 自身 → 自我认知通路（不检索、不推演）
        if self._is_self_question(text):
            return self._answer_self(text, task)
        # 变更 #29：纠正/反问句——你在质疑库里的规则，不进问答流水线
        if any(c["intent"] == "challenge" for c in active):
            return self._do_challenge(text, task)
        # 批六：指代回填——「它为什么会这样」里的「它」绑到最近提到的对象
        text = self._bind_pronoun(text, task)
        # 批二：拆得开就拆——多子句逐个解决，解不了就整句退回
        if self._SPLIT_PAT.search(text):
            thinking.append("检测到疑似多个子问题，尝试拆开逐个解决（拆不开就整句处理）")
            decomposed = self._do_decompose(text, task)
            if decomposed is not None:
                self._add_event("推演", "用户", "拆解问答", obj=text[:30], source=text)
                return decomposed
        # 批二：「需要什么/怎样才能」→ 反向推演找条件（批七：焦点判定交给组合式解析）
        need = None
        try:
            need = reason.answer_need(text, rules=self.rules,
                                      force=parsed["any_need"],
                                      negated=parsed["any_negated"])
        except Exception:
            need = None
        if need:
            thinking.append("这是「需要什么/怎样才能」式问题：锁定目标变量，反向找路径")
            self._note_entities(need.get("facts"))   # 指代回填先行词
            # 反向路径也是经验：留痕供发现机制挖矿（批三：反问路径此前不留 reason_trace）
            task["reason_trace"] = sorted(
                {p["rule"] for p in need["paths"]} |
                {a["rule"] for a in need.get("avoid", [])})
            lines = []
            goal_verb = "要阻止" if need.get("prevent") else "要达成"
            for p in need["paths"]:
                thinking.append("找到路径 {}（{}档）：需要 {}".format(
                    p["rule"], need["tiers"].get(p["rule"], "?"), "、".join(p["when"])))
                # 组句器组装（批七）：主语+条件+结论+置信语气，不再是填空模板
                lines.append(compose.compose_conditions(
                    p["goal"], p["when"], p["missing"], p["conf"], p["rule"]))
            # 逆否路径：规则产出的是目标反面 → 达成目标 = 不让它的条件凑齐
            for a in need.get("avoid", []):
                held = [c for c in a["when"] if c not in a["missing"]]
                thinking.append("规则 {} 的结论是目标的反面（{}）：逆否使用——{}=不让这套条件凑齐".format(
                    a["rule"], a["then"], goal_verb))
                lines.append(compose.compose_avoid(
                    a["goal"], a["when"], a["then"], a["conf"], a["rule"], held))
            # 变更 #23：试假设攻坚——缺的条件不停在列清单，
            # 把每个缺口的候选值逐一分支试算（规格 §4：动用一切知识攻坚，耗尽方法）
            missing_all = sorted({m for p in need["paths"] for m in p["missing"]})
            if missing_all:
                branches, _by = reason.hypothesize_branches(
                    self.rules, need["facts"], missing_all)
                if branches:
                    thinking.append(
                        "缺的条件不停在这儿：把 {} 个缺口的候选值逐分支试算了一遍".format(
                            len(missing_all)))
                    lines.append("我把还缺的条件各试了一遍（假设推演）：")
                    for b in branches:
                        cond_txt = "、".join(compose._humanize_cond(v + "=" + x)
                                             for v, x in b["assign"])
                        if b["derived"]:
                            outs = "；".join(self._say(
                                d, _by.get(d["rule"], {}).get("when"),
                                need["facts"], task) for d in b["derived"][:2])
                            lines.append("如果{} → {}".format(cond_txt, outs))
                        else:
                            lines.append("如果{} → 推不出新结论".format(cond_txt))
            # 变更 #25：话里有不认识的实体词 → 类比迁移（假设，不是结论）
            analog = reason.analogize(text, self.rules, unknown)
            if analog:
                thinking.append("话里有不认识的词，补一招类比迁移（产物是假设不是结论）")
                lines += [self._compose_analogy(a) for a in analog]
            if need["paths"] or need["conclusions"]:
                self._add_event("推演", "用户", "反向推演", obj=text[:30], source=text)
                return "\n  ".join(lines)   # 变更 #29：整句框架废止，直接说话
            if lines:
                # 只有逆否路径：先给逆否推理，再补一次知识库检索（条目正文往往就有答案）
                rescued = executor.search_rescue(text, self._kb())
                thinking.append("正面路径没有，只有逆否路径；补一次知识库检索：{}".format(
                    "命中" if rescued else "没有命中"))
                if rescued:
                    lines.append("知识库相关条目：\n" + rescued)
                self._add_event("推演", "用户", "反向推演", obj=text[:30], source=text)
                return "\n  ".join(lines)   # 变更 #29：整句框架废止，直接说话
            thinking.append("认得目标但库里没有产出它的规则，反向推演无解")
        # 变更 #17：推演优先于检索（设计者定调：这是推理，不是搜索引擎）。
        # 攻坚顺序：认出事实 → 规则链正向推演 → 有结论带置信作答；
        #          条件不足 → 诚实说还缺什么（观察是合法动作）；
        #          推演无解 → 才轮到知识库全文检索 → 都不行 → 讲清试过什么再说不懂。
        qa = None
        try:
            qa = reason.answer_question(text, rules=self.rules)
        except Exception:
            qa = None   # 推演桥故障不拖垮会话：降级检索（留档可查）
        if qa:
            for f in qa["facts"]:
                thinking.append("认出事实：{} = {}（{}）".format(f["var"], f["val"], f["how"]))
            self._note_entities(qa["facts"])   # 指代回填先行词
            if qa["conclusions"]:
                lines = []
                for c in qa["conclusions"]:
                    rmeta = qa["by_id"].get(c["rule"], {})
                    thinking.append(
                        "命中规则 {}：当{}→则{}（{}档{}域），得出结论（置信 {:.0%}）{}".format(
                            c["rule"], "、".join(rmeta.get("when", ["（见条目）"])),
                            c["then"], rmeta.get("tier", "?"), rmeta.get("domain", "?"),
                            c["conf"], "——沿规则链进一步推出" if c.get("chained") else ""))
                    lines.append(self._say(c, rmeta.get("when"), qa["facts"], task))
                for c in qa["competing"]:
                    thinking.append("另有竞争假设并存：{} = {}（置信 {:.0%}，来自{}），不静默取舍".format(
                        c["var"], c["value"], c["conf"], c["rule"]))
                    lines.append("（竞争假设）{} = {}（置信 {:.0%}，来自{}）".format(
                        c["var"], c["value"], c["conf"], c["rule"]))
                for w in qa["wishes"]:
                    lines.append("（要推出「{}」还缺：{}——告诉我就能接着推）".format(
                        w["then"], "、".join(w["need"])))
                n_filtered = len(qa["derived"]) - len(qa["conclusions"])
                if n_filtered > 0:
                    thinking.append("另外还推出 {} 条与问题无关的结论，已略去不报".format(n_filtered))
                thinking.append("推演完成，结论仅参考，行动仍须你裁决")
                # 变更 #10 闭环推广：概率域结论（置信<100%）存为待验证预测，提醒到期时对账
                prob = [d for d in qa["derived"] if d["conf"] < 1.0]
                if prob:
                    pid = predict.save_prediction(text, prob, qa["chain"])
                    if pid:
                        lines.append("（这条不是百分百确定，已存为预测 {}——到应验的日子我会提醒你核对）".format(pid))
                        task["prediction"] = pid
                self._add_event("推演", "用户", "问答推演", obj=text[:30], source=text)
                task["reason_trace"] = qa["chain"]
                # 变更 #28：话里带了嵌套条件 → 结论前明示「在你给的条件下」，不把假设当事实
                # 变更 #29：「按规则推演的结果：」整句框架废止——单结论直接说话
                nc = task.get("nested_cond")
                prefix = ("在你给的「{}」条件下：".format(nc["cond"]) if nc else "")
                if len(lines) == 1:
                    return prefix + lines[0]
                return prefix + ("\n" if prefix else "") + "\n  ".join(lines)
            if qa["wishes"]:
                thinking.append("规则链条件不足：{}——先试试检索有没有现成答案".format(
                    "；".join("要推出「{}」还缺 {}".format(w["then"], "、".join(w["need"]))
                              for w in qa["wishes"])))
        else:
            thinking.append("推演层：没从话里认出规则库相关的事实，这条路不通，排除")
        # 变更 #14：整句清洗后模糊检索目录
        rescued = executor.search_rescue(text, self._kb())
        thinking.append("转去知识库目录模糊检索：{}".format("命中，直接读条目作答" if rescued else "没有命中"))
        if rescued:
            # 变更 #25：检索命中但话里有陌生实体 → 附类比注记（条目说的是铁，你问的是铜）
            analog = reason.analogize(text, self.rules, unknown)
            if analog:
                thinking.append("检索命中；话里有不认识的词，附类比注记（假设不是结论）")
                return rescued + "\n\n（类比注记）" + "\n".join(
                    self._compose_analogy(a) for a in analog)
            return rescued
        # 检索也落空：变更 #23——不算完，把缺的条件各候选值试算一遍再答（耗尽方法）
        if qa and qa["wishes"]:
            # 变更 #28（规格：答非所问优先）——愿单在推的值不是用户问的值时
            # （「铁会感冒吗」愿单在推「生锈」），先走结构类比回答真正的问题，
            # 不拿另一个问题的分支结论充数。
            if unknown and not any(any(u in w["then"] for u in unknown)
                                   for w in qa["wishes"]):
                structural = reason.structural_analogize(text, self.rules, unknown)
                if structural:
                    thinking.append(
                        "规则愿单答非所问（问的是「{}」，愿单在推「{}」）；"
                        "转结构类比，直接回答真正的问题".format(
                            "、".join(unknown),
                            "、".join(w["then"] for w in qa["wishes"])))
                    return "直答不了，但结构上有得猜：\n  " + "\n  ".join(
                        self._compose_struct_analogy(s) for s in structural)
            wish_missing = sorted({c for w in qa["wishes"] for c in w["need"]})
            # 变更 #29：认出的介词短语事实（碰到水/在空气中）也许能补缺口——
            # 不擅自等同，挂一轮确认问你一句（确认后轮 confirm_cond 补事实重推）
            contacts = [f["val"] for f in qa["facts"] if f["var"] == "环境接触"]
            branches, _by = reason.hypothesize_branches(
                self.rules, qa["facts"], wish_missing)
            if branches:
                thinking.append("检索也无果；把缺的条件逐个假设试算，给分支结论而不是空手说不懂")
                blines = []
                for b in branches:
                    cond_txt = "、".join(compose._humanize_cond(v + "=" + x)
                                         for v, x in b["assign"])
                    if b["derived"]:
                        outs = "；".join(self._say(
                            d, _by.get(d["rule"], {}).get("when"),
                            qa["facts"], task) for d in b["derived"][:2])
                        blines.append("如果{} → {}".format(cond_txt, outs))
                    else:
                        blines.append("如果{} → 推不出新结论".format(cond_txt))
                intro = ("我认出你给了「接触{}」。条件还差一点，我把几种可能各试了一遍："
                         .format("、".join(contacts)) if contacts
                         else "条件不够，我把几种可能各试了一遍：")
                return (intro + "\n  " + "\n  ".join(blines) +
                        "\n你告诉我是哪一种，我就能定。")
            thinking.append("检索也无果，如实告知推演还缺什么条件")
            wish_txt = "；".join("要推出「{}」还缺「{}」".format(
                w["then"], "、".join(compose._humanize_cond(c) for c in w["need"]))
                for w in qa["wishes"])
            if contacts:
                self.state.pending = {"action": "confirm_cond",
                                      "slots": {"origin": text, "missing": wish_missing},
                                      "round": self.state.counter}
                thinking.append("认出的接触事实也许能补缺口，挂一轮确认等你回答")
                return ("我认出你给了「接触{}」。{}——你说的这些算不算？"
                        "算的话回个「算」，我就按它推。".format("、".join(contacts), wish_txt))
            return "{}——这些事实成立吗？你补上我就能接着推。".format(wish_txt)
        # 变更 #25：常规路都断了，最后一招——类比迁移（陌生实体换进认得的规律里试）
        analog = reason.analogize(text, self.rules, unknown)
        if analog:
            thinking.append("所有正路都断了；话里有不认识的词，最后一招：类比迁移（假设不是结论）")
            return "直答不了，但类比可以试试：\n  " + "\n  ".join(
                self._compose_analogy(a) for a in analog)
        # 变更 #28：同位类比也无产出——结构类比（跨域搬关系结构，单规则移植）
        structural = reason.structural_analogize(text, self.rules, unknown)
        if structural:
            thinking.append("同位类比也没招；目标本身没规则——最后一招："
                            "跨域结构类比（搬关系结构，可靠性更低，只是猜想方向）")
            return "直答不了，同位类比也没招；但结构上有得猜：\n  " + "\n  ".join(
                self._compose_struct_analogy(s) for s in structural)
        if act == "statement":
            thinking.append("判定为陈述，记账存档")
            self._add_event("闲聊", "用户", "陈述", obj=text, source=text)
            return "我记下了：" + text
        thinking.append("所有路都走不通，如实说不懂，并把生词记下申请学习")
        return "我还不能理解这句话。" + (
            "（生词：{}，将申请录入词典）".format("、".join(unknown))
            if unknown else "")

    def _do_remind(self, slots, source, now):
        t_str, thing = slots.get("time_str", ""), slots.get("thing", "")
        t, why = timenorm.parse_time_str(t_str, now) if t_str else (None, "缺时间")
        if t is None:
            # 关键槽缺失 → 反问，并记住已拿到的槽位（规格书第 2 节）
            self.state.pending = {"action": "remind",
                                  "slots": {"time_str": t_str, "thing": thing},
                                  "round": self.state.counter}
            if not thing:
                return _followup_hint("thing")
            return "没听清时间（{}）。{}".format(why, _followup_hint("time"))
        self.state.pending = None
        self._add_event("提醒", "用户", "提醒", obj=thing,
                        time=t.isoformat(timespec="seconds"),
                        source=source, note=thing)
        # 跨天时回话带上日期，避免"明天8点"只回"08:00"造成歧义（变更 #14 增补）
        when = t.strftime("%H:%M") if t.date() == now.date() else t.strftime("%m月%d日 %H:%M")
        return "好的，{} 提醒你{}。".format(when, thing)

    def _try_pending(self, text, now):
        """上一轮缺槽反问，这轮用户补料 → 回填完成提醒登记。
        第二轮审查 L1：用户可以说「算了/取消」退出追问；
        追问挂起超 PENDING_TTL_ROUNDS 轮自动作废，不再劫持后续输入。"""
        p = self.state.pending
        if not p:
            return None
        if p["action"] == "confirm_cond":
            # 变更 #29：「这算不算潮湿」式确认的回复轮
            t = text.strip()
            if t in PENDING_CANCEL_WORDS or t in ("不算", "不对", "不成立", "不行", "不是"):
                self.state.pending = None
                return "好，那条不算数——没有它这条路我推不动，如实说不懂。"
            if t in ("算", "算数", "是", "对", "嗯", "成立", "对的", "是的", "可以",
                     "就这样", "算吧") or t.startswith("算"):
                self.state.pending = None
                origin = p["slots"]["origin"]
                extra = "；".join(p["slots"]["missing"]).replace("=", "")
                qa2 = reason.answer_question(origin + "，" + extra, rules=self.rules)
                if qa2 and qa2["conclusions"]:
                    task4 = {"thinking": []}   # 确认轮重推的组句弃权也要留痕
                    lines = [self._say(c, qa2["by_id"].get(c["rule"], {}).get("when"),
                                       qa2["facts"], task4) for c in qa2["conclusions"]]
                    return "好，按你确认的算：\n  " + "\n  ".join(lines)
                return "好，按你确认的算——但还是推不出结论，我如实说不懂。"
            return None   # 答非所问：不劫持，按新输入走正常流程
        if text.strip() in PENDING_CANCEL_WORDS:
            self.state.pending = None
            return "好，这条提醒的事先放下了。"
        if self.state.counter - p.get("round", self.state.counter) > PENDING_TTL_ROUNDS:
            self.state.pending = None
            return None   # 追问已过期，按新输入走正常流程
        if p["action"] == "remind":
            slots = p["slots"]
            if not slots.get("thing"):
                slots["thing"] = text.strip()
                self.state.pending = None
                return "好，{}。几点？".format(slots["thing"])
            t, why = timenorm.parse_time_str(text, now)
            if t is None:
                return "这个时间不行（{}）。{}".format(why, _followup_hint("time"))
            self.state.pending = None
            self._add_event("提醒", "用户", "提醒", obj=slots["thing"],
                            time=t.isoformat(timespec="seconds"),
                            source=text, note=slots["thing"])
            return "好的，{} 提醒你{}。".format(t.strftime("%H:%M"), slots["thing"])
        self.state.pending = None
        return None

    # ---- 原子操作（变更 #11：算术/比较/时间换算/查库，全部留痕）----
    def _do_atoms(self, text, task):
        t = text.strip()
        bindings = self.state.num_bindings
        # 变更 #24（甲 7）：方程优先于普通算式——「x加3等于8，x等于几」「某数乘以2等于10」
        if ("等于" in t or "=" in t) and re.search(r"[xX]|未知数|某数|这个数", t):
            solved = atoms.solve_linear(t, bindings)
            if solved:
                ok, out = solved
                task["atomic_op"] = True
                self.stats.bump("op:原子操作")
                return out
        # 绑定名代入（甲 6）：「密度*2」→「7.8*2」，代入后走既有算式通道
        for name in sorted(bindings, key=len, reverse=True):
            if name in t:
                t = t.replace(name, str(bindings[name]))
        if t.startswith(("计算", "算一下")):
            expr = re.sub(r"^(计算|算一下)", "", t).strip(" ？?=")
            if not expr:
                return "计算后面要跟表达式，例如：计算 3*3+2"
            ok, out = atoms.calculate(expr, bindings)
        elif t.startswith("比较"):
            ok, out = atoms.compare(re.sub(r"^比较", "", t))
        elif _ATOM_TIME_PAT.match(t):
            ok, out = atoms.time_convert(t)
        elif _ATOM_CALC_PAT.match(t):
            # 变更 #14 增补：直写算式（中文数字/加减乘除字样由 chinese_arith_norm 归一）
            expr = atoms.chinese_arith_norm(_ATOM_CALC_PAT.match(t).group(1))
            if expr is None:
                return "这个算式我认不全——可以说：计算 3*3+2，或 十乘十等于多少"
            ok, out = atoms.calculate(expr, bindings)
        elif t.startswith("查库"):
            target = re.sub(r"^查库", "", t).strip()
            if not target:
                return "查库后面要跟标记或主题，例如：查库 P5 / 查库 性能"
            client = self._kb()
            if client is None:
                # 第三轮审查 R3-1：密钥未配置——与断连同一降级语义（规格 #16）
                task["kb_down"] = True
                return "我现在联系不上知识库（密钥未配置或服务没启动），查库这条记在案，等库醒了再说。"
            try:
                ok, out = atoms.query_kb(target, client)
            except KBError as e:
                if e.status == -1:
                    # 知识库断连：与 _do_kb_action 同一降级语义（规格 #16）
                    task["kb_down"] = True
                    return "我现在联系不上知识库（服务没启动？），查库这条记在案，等库醒了再说。"
                raise
        else:
            return "这个原子操作我还不认识。"
        task["atomic_op"] = True
        self.stats.bump("op:原子操作")
        return out

    def _do_kb_action(self, chosen, act, source, task):
        action, slots = chosen["action"], chosen["slots"]
        topic = slots.get("target", slots.get("topic", ""))
        self._add_event("知识查询", "用户", action, obj=topic, source=source)
        client = self._kb()
        if client is None:
            # 第三轮审查 R3-1：密钥未配置——与断连同一降级语义（规格 #16）
            task["kb_down"] = True
            return "我现在联系不上知识库（密钥未配置或服务没启动），这条先记在案，等库醒了再说。"
        # 变更 #26（规格 §5 执行三态）：可重试故障自动重试 ≤3 次；
        # 仍失败 → 上报人工 + 该类动作进暂停名单（「恢复执行 X」解除）
        try:
            status, out, attempts = executor.execute_3state(
                action, slots, client, suspended=self.state.suspended_actions)
        except KBError as e:
            if e.status == -1:
                # 知识库断连：降级文案；本地任务（提醒等）不受影响（规格 #16）
                task["kb_down"] = True
                return "我现在联系不上知识库（服务没启动？），这条先记在案，等库醒了再说。"
            raise
        if attempts > 1:
            task["thinking"].append("执行故障，重试到第 {} 次才出结果".format(attempts))
        if status == "error" and attempts >= executor.MAX_ATTEMPTS:
            if action not in self.state.suspended_actions:
                self.state.suspended_actions.append(action)
            task["thinking"].append("动作「{}」连续 {} 次失败：上报人工并暂停该类动作".format(
                action, attempts))
            return out + "\n（这类动作已暂停，你过目修复后说「恢复执行 {}」我再开工）".format(action)
        task["exec_status"] = status
        if status == "miss":
            # 结果对账：未命中是训练层的原料（攒够阈值 → 建议队列）
            self.stats.bump("miss:" + topic)
        if status == "refused":
            return "[白名单拦截] " + out
        return out


def _dump_state(sess):
    print("\n===== 状态速览 =====")
    print("规则来源：", sess.rules_source)
    print("类型权重：", sess.state.weights)
    print("未完成事件：", [(e["id"], e["type"], e["object"], e["state"])
                        for e in sess.state.events
                        if e["state"] in ("待办", "进行中")])
    print("统计 Top：", sess.stats.top(8))
    ok, n = stats.verify_log()
    print("weights.log：{} 条，哈希链{}".format(n, "完整" if ok else "【断裂】"))
    for line in stats.LOG_FILE.read_text(encoding="utf-8").splitlines()[-6:]:
        e = json.loads(line)["entry"]
        print("  {} | {} | {} -> {}".format(e["time"], e["signal"],
                                            e["old"], e["new"]))


def main():
    api_key = None
    argv = sys.argv[1:]
    if argv and argv[0] == "--script":
        api_key = config.load_api_key()
        sess = Session(api_key)
        for s in argv[1:]:
            print("你：", s)
            print("AI ：", sess.handle(s))
            print("-" * 40)
        _dump_state(sess)
        return
    # 交互模式：密钥缺失不致命（只是知识库功能不可用）
    try:
        api_key = config.load_api_key()
    except SystemExit as e:
        print(e)
        print("（未配置密钥仍可聊天，知识库功能将提示断连）\n")
    sess = Session(api_key)
    print("骨架版会话开始。输入「退出」结束。")
    while True:
        try:
            text = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text in ("退出", "quit", "exit"):
            break
        if not text:
            continue
        print("AI ：", sess.handle(text))
    sess.state.save()
    print("状态已保存。")


if __name__ == "__main__":
    main()
