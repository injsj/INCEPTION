# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】根据实际使用和经验申请修改目录——候选走 REQ 通道（规格 §6）；【施工方】候选生成与节流三闸
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""神经提炼·起步档（规格书第 6 节 离线速）：从统计与档案提炼候选规律。

候选来源（本期规则兜底；LLM 外挂见 NeuralRefiner 插槽）：
- 高频生词（≥3 次）        → 建议录入词典（AI 本地，你批准后即生效）
- 高频知识缺口（≥2 次查无） → 建议新增知识（走 REQ 通道，你裁决后入库）

建议队列 = state\\suggestions.json，你是裁决者（规格书第 8 节）：
    python -m brain.refine              查看队列
    python -m brain.refine --apply ID   批准词典类建议（写入 lexicon_extra）
    python -m brain.refine --req ID     批准知识缺口类（提交 REQ 到知识库）
    python -m brain.refine --drop ID    拒绝
"""
import hashlib
import json
import sys

from . import config

QUEUE_FILE = config.STATE / "suggestions.json"
LEXICON_EXTRA = config.STATE / "lexicon_extra.json"
LEXICON_AUDIT = config.STATE / "lexicon_audit.jsonl"

THRESH_LEXICON = 3.0    # 生词出现频次阈值（衰减后）
THRESH_GAP = 2.0        # 知识缺口阈值


def _is_noise(word):
    """纯数字/标点是分词的天然碎片，不是词，不进建议队列。"""
    return not any('\u4e00' <= ch <= '\u9fff' for ch in word)


def _is_morpheme(word):
    """单字是语素不是词（批三自查发现：队列曾塞进 25 条单字垃圾建议）。
    词典服务的对象是分词器，单字条目无法改善切分，一律不收。"""
    return len(word) < 2


class NeuralRefiner:
    """LLM 外挂插槽（离线速①）。未配置时返回空，规则兜底照常工作。
    接入方法（将来）：实现 refine(archives) 返回候选列表
    [{"kind": ..., "content": ..., "evidence": [...]}]，collect() 会自动并入队列。"""

    def available(self):
        return False

    def refine(self, archives):
        return []


def _load_queue():
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"next_id": 1, "items": []}


def _save_queue(q):
    config.ensure()
    config.atomic_write(QUEUE_FILE, json.dumps(q, ensure_ascii=False, indent=2))


def _existing_keys(q):
    """已在队列里的 (kind, content)——待裁决与已提交REQ都算（变更 #28：
    直通过的内容不再重复挖掘上报，被拒的也不年年复读）。"""
    return {(it["kind"], it["content"]) for it in q["items"]
            if it["status"] in ("待裁决", "已提交REQ")}


def collect(sess, refiner=None):
    """扫描统计层，把达到阈值的新候选并入建议队列。返回新加入的候选。"""
    q = _load_queue()
    pending_keys = _existing_keys(q)
    fresh = []
    for k, v in sorted(sess.stats.counts.items()):
        cand = None
        if k.startswith("unknown:") and v >= THRESH_LEXICON:
            if _is_noise(k[len("unknown:"):]) or _is_morpheme(k[len("unknown:"):]):
                continue
            cand = {"kind": "lexicon", "content": k[len("unknown:"):],
                    "why": "生词出现 {:.2f} 次（衰减计数）".format(v)}
        elif k.startswith("miss:") and v >= THRESH_GAP:
            cand = {"kind": "knowledge_gap", "content": k[len("miss:"):],
                    "why": "查询未命中 {:.2f} 次（衰减计数）".format(v)}
        if cand and (cand["kind"], cand["content"]) not in pending_keys:
            fresh.append(cand)
    if refiner is not None and refiner.available():
        for c in refiner.refine(config.ARCHIVE):
            if (c["kind"], c["content"]) not in pending_keys:
                fresh.append(c)
    for c in fresh:
        c["id"] = "S{}".format(q["next_id"])
        q["next_id"] += 1
        c["status"] = "待裁决"
        c["evidence"] = _recent_archives(3)
        q["items"].append(c)
    if fresh:
        _save_queue(q)
    return fresh


def collect_rule_promote(sess, promo):
    """规则晋升建议（变更#7）：对账攒够阈值的 G 类规则 → 建议移区 REQ，管理员终审。"""
    q = _load_queue()
    if any(it["kind"] == "rule_promote" and it["content"] == promo["id"]
           and it["status"] == "待裁决" for it in q["items"]):
        return []
    cand = {"kind": "rule_promote", "content": promo["id"],
            "why": "对账命中 {} 次 / 落空 {} 次，命中率 {:.0%}（阈值 ≥3 且 ≥70%）".format(
                promo["hit"], promo["miss"], promo["rate"])}
    cand["id"] = "S{}".format(q["next_id"])
    q["next_id"] += 1
    cand["status"] = "待裁决"
    cand["evidence"] = _recent_archives(3)
    q["items"].append(cand)
    _save_queue(q)
    return [cand]


def add_suggestions(cands):
    """通用入队（经验提炼矿工等产出）：去重后附证据编号，返回新加入的候选。"""
    q = _load_queue()
    pending_keys = _existing_keys(q)
    fresh = []
    for c in cands:
        if (c["kind"], c["content"]) in pending_keys:
            continue
        c = dict(c)
        c["id"] = "S{}".format(q["next_id"])
        q["next_id"] += 1
        c["status"] = "待裁决"
        c["evidence"] = _recent_archives(3)
        q["items"].append(c)
        fresh.append(c)
    if fresh:
        _save_queue(q)
    return fresh


def _recent_archives(n):
    # rglob：兼容旧版平铺档案与月度子目录（第二轮审查 L3）
    files = sorted(config.ARCHIVE.rglob("task_*.json"))
    return [f.name for f in files[-n:]]


def _find(q, sid):
    for it in q["items"]:
        if it["id"] == sid:
            return it
    return None


def apply_lexicon(sid):
    """批准词典建议：写入 state\\lexicon_extra.json，下轮分词即生效。"""
    q = _load_queue()
    it = _find(q, sid)
    if not it:
        return "没有找到 " + sid
    if it["kind"] != "lexicon":
        return "{} 是 {} 类，不能用 --apply（词典类才能本地生效）".format(sid, it["kind"])
    if LEXICON_EXTRA.exists():
        try:
            words = json.loads(LEXICON_EXTRA.read_text(encoding="utf-8"))
        except ValueError:
            words = []
    else:
        words = []
    if not any(w.get("词") == it["content"] for w in words):
        words.append({"词": it["content"], "词性": "待补",
                      "释义": "AI 建议录入（{}）".format(it["why"])})
        config.atomic_write(LEXICON_EXTRA, json.dumps(words, ensure_ascii=False, indent=2))
    # 变更 #24（乙类账 3）：词典批准留痕——时间/编号/词/理由追加进审计流水。
    # 词典迁入知识库后此通道退役，改走 REQ 正式通道（规格书 §1「将来入知识库」）。
    audit = {"time": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
             "id": sid, "word": it["content"], "why": it.get("why", ""), "by": "admin"}
    with open(LEXICON_AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps(audit, ensure_ascii=False) + "\n")
    it["status"] = "已采纳"
    _save_queue(q)
    return "已录入词典：" + it["content"]


def submit_req(sid, api_key=None):
    """批准建议：走 REQ 通道提交知识库（你裁决后才真正生效）。
    knowledge_gap → 新增知识；rule_promote → 规则移区（存疑→信任）。"""
    q = _load_queue()
    it = _find(q, sid)
    if not it:
        return "没有找到 " + sid
    from . import kb
    client = kb.get_client(api_key)
    if client is None:
        # 第三轮审查 R3-1：密钥未配置——REQ 未提交，建议保持待裁决，可稍后重试
        return "知识库密钥未配置或服务断连，{} 的 REQ 未提交；配置好后可重试 --req。".format(sid)
    if it["kind"] == "knowledge_gap":
        rid = client.propose(
            "new", "",
            proposal="【AI 建议新增】主题：{}（正文待你补全或驳回）".format(it["content"]),
            reason="AI 多次查询「{}」未命中（{}），判断为知识缺口。".format(it["content"], it["why"]),
            proof="证据档案：" + "、".join(it.get("evidence", [])))
    elif it["kind"] in ("cooccur", "shortcut", "converge", "num_converge", "lang_cooccur"):
        # 经验提炼候选（变更#8/#11）：作为新知识申请入库，管理员终审
        if it["kind"] == "shortcut":
            proposal = "【AI 经验提炼·候选规则】{}（格式同 G 类用法字段）".format(it["content"])
        elif it["kind"] == "num_converge":
            proposal = "【AI 经验提炼·数值趋同】{}（两条计算路径结果一致，疑似等式规律）".format(it["content"])
        else:
            proposal = "【AI 经验提炼】{}：{}".format(it["kind"], it["content"])
        rid = client.propose(
            "new", "",
            proposal=proposal,
            reason=it["why"],
            proof="证据档案：" + "、".join(it.get("evidence", [])))
    elif it["kind"] == "rule_promote":
        # 第二轮审查·设计观察 2：附上对账计数文件指纹，防 AI 状态文件被手改后骗晋升
        fp = ""
        try:
            from . import ruleload as _rl
            if _rl.COUNT_FILE.exists():
                fp = hashlib.sha256(_rl.COUNT_FILE.read_bytes()).hexdigest()[:16]
        except OSError:
            pass
        rid = client.propose(
            "move_zone", it["content"],
            proposal="移入信任区",
            reason="规则 {} 对账达标（{}），申请晋升。".format(it["content"], it["why"]),
            proof="证据档案：" + "、".join(it.get("evidence", [])) +
                  "；对账明细见 AI 侧 weights.log 与 rule_reconcile.json" +
                  ("；rule_reconcile.json 指纹 " + fp if fp else ""))
    else:
        return "{} 是 {} 类，不能用 --req".format(sid, it["kind"])
    it["status"] = "已提交REQ"
    it["req"] = rid
    _save_queue(q)
    return "已提交 {}，请在管理菜单里裁决。".format(rid)


def drop(sid):
    q = _load_queue()
    it = _find(q, sid)
    if not it:
        return "没有找到 " + sid
    it["status"] = "已拒绝"
    _save_queue(q)
    return "已拒绝 " + sid


# ════════════════════ REQ 直通（变更 #28，用户拍板 2026-09-25）════════════════════
# 用户设计：自动挖掘知识 → 知识与推理过程填进申请表 → 直接打到待审核区 →
# 用户一处裁决（废弃 / 存疑区 / 其他区）。本地队列对知识类从此只是上报台账，
# 不再是裁决点（词典类是 AI 本地事，仍留本地队列）。

REQ_THROTTLE_FILE = config.STATE / "req_throttle.json"
REQ_DAILY_LIMIT = 5        # 每日上限（用户拍板：宁少而精）
REQ_BACKLOG_PAUSE = 10     # 待审核积压 ≥10 暂停上报（保护裁决者不被淹）

# 直通的知识类候选；lexicon 留本地
_AUTO_KINDS = ("knowledge_gap", "cooccur", "shortcut", "converge",
               "num_converge", "lang_cooccur", "rule_promote")

_KIND_CN = {"knowledge_gap": "知识缺口", "cooccur": "规则共现", "shortcut": "候选规则",
            "converge": "趋同", "num_converge": "数值趋同", "lang_cooccur": "概念共现",
            "rule_promote": "规则晋升"}


def _req_payload(it):
    """候选 → REQ 五要素（action, target, proposal, reason, proof）。
    reason=为什么挖它；proof=推理过程与证据——两样必填，缺一打不进去。"""
    kind, content, why = it["kind"], it["content"], it.get("why", "")
    ev = "、".join(it.get("evidence", [])) or "（无）"
    if kind == "rule_promote":
        fp = ""
        try:
            from . import ruleload as _rl
            if _rl.COUNT_FILE.exists():
                fp = hashlib.sha256(_rl.COUNT_FILE.read_bytes()).hexdigest()[:16]
        except OSError:
            pass
        return ("move_zone", content, "移入信任区",
                "规则 {} 对账达标（{}），申请晋升。".format(content, why),
                "证据档案：{}；对账明细见 AI 侧 weights.log 与 rule_reconcile.json{}".format(
                    ev, "；rule_reconcile.json 指纹 " + fp if fp else ""))
    mining = {
        "knowledge_gap": "统计层发现该主题反复查询未命中，判定为知识缺口",
        "cooccur": "两条规则在同一批推演档案里反复一起触发，疑有共同上游或隐藏联系",
        "shortcut": "规则链在推演档案中反复出现，合并为直达规则候选（条件已做矛盾检查）",
        "converge": "多条规则指向同一结论变量，疑为同一现象的不同描述",
        "num_converge": "多条不同计算路径算出同一结果，疑似等式规律（原子操作留痕）",
        "lang_cooccur": "两个概念在多份对话原文里反复一起出现，疑有隐藏联系",
    }[kind]
    if kind == "knowledge_gap":
        proposal = "【AI 自动挖掘·知识缺口】主题：{}（请补全正文或驳回）".format(content)
    elif kind == "shortcut":
        proposal = "【AI 自动挖掘·候选规则】{}（格式同 G 类用法字段）".format(content)
    else:
        proposal = "【AI 自动挖掘·{}】{}".format(_KIND_CN[kind], content)
    return ("new", "", proposal, why,
            "推理过程：{}。证据档案：{}".format(mining, ev))


def _throttle_load():
    if REQ_THROTTLE_FILE.exists():
        try:
            return json.loads(REQ_THROTTLE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"date": "", "count": 0, "submitted": {}}


def auto_submit_reqs(fresh, sess):
    """知识类候选直通待审核区：挖出即打成 REQ（知识+理由+推理过程填好）。
    节流：同内容不重复打 / 每日 ≤{} / 待审核积压 ≥{} 暂停。
    断连不打（候选留本地台账，下轮再试）。返回提交的 REQ 编号列表。""".format(REQ_DAILY_LIMIT, REQ_BACKLOG_PAUSE)
    todo = [it for it in fresh if it.get("kind") in _AUTO_KINDS]
    if not todo:
        return []
    from . import kb
    client = kb.get_client(getattr(sess, "api_key", None))
    if client is None:
        return []
    try:
        backlog = sum(1 for r in client.my_requests() if r.get("status") == "待审核")
    except Exception:
        return []   # 服务断连：留台账，下轮再试
    import datetime as _dt
    today = _dt.date.today().isoformat()
    th = _throttle_load()
    if th.get("date") != today:
        th = {"date": today, "count": 0, "submitted": th.get("submitted", {})}
    submitted_map = th.setdefault("submitted", {})
    q = _load_queue()
    done = []
    for it in todo:
        if backlog >= REQ_BACKLOG_PAUSE or th["count"] >= REQ_DAILY_LIMIT:
            break
        key = it["kind"] + "|" + it["content"]
        qit = _find(q, it["id"])
        if key in submitted_map:   # 同内容已直通过：台账标记，不重复打
            if qit:
                qit["status"] = "已提交REQ"
                qit["req"] = submitted_map[key]
            continue
        try:
            action, target, proposal, reason, proof = _req_payload(it)
            rid = client.propose(action, target=target, proposal=proposal,
                                 reason=reason, proof=proof)
        except Exception:
            continue   # 单条失败不拖垮整批，留台账下轮再试
        if qit:
            qit["status"] = "已提交REQ"
            qit["req"] = rid
        submitted_map[key] = rid
        th["count"] += 1
        backlog += 1
        done.append(rid)
    if done or q["items"]:
        _save_queue(q)
    config.atomic_write(REQ_THROTTLE_FILE, json.dumps(th, ensure_ascii=False, indent=2))
    return done


def main():
    argv = sys.argv[1:]
    q = _load_queue()
    if not argv or argv[0] == "--list":
        pending = [it for it in q["items"] if it["status"] == "待裁决"]
        if not pending:
            print("建议队列为空。")
            return
        print("待裁决建议（你是裁决者）：")
        for it in pending:
            print("  {} [{}] {} —— {}".format(it["id"], it["kind"],
                                               it["content"], it["why"]))
        print("\n处置：python -m brain.refine --apply ID（词典）/"
              "--req ID（缺口/提炼/晋升）/--drop ID（拒绝）")
        return
    cmd, sid = argv[0], argv[1] if len(argv) > 1 else ""
    if cmd == "--apply":
        print(apply_lexicon(sid))
    elif cmd == "--req":
        print(submit_req(sid))
    elif cmd == "--drop":
        print(drop(sid))
    else:
        print("未知命令：" + cmd)


if __name__ == "__main__":
    main()
