# -*- coding: utf-8 -*-
"""变更 #27 专项验证：虚词词典 / 生词误报 / 类比误代 / 切片合并对AI生效 / 侧栏切片化。

四问题对应四组用例：
  1. 词典与分词：常用虚字全部登记；分词不再把虚字误判「未知」
  2. 类比攻坚：虚词不再被当实体代入（「为」不再是一种金属）
  3. 先行词池按切片可见域过滤：没合并的切片，AI 看不到它的记录；合并后看得到
  4. 切片后端：视图带预览/时间；删除进回收站可恢复（变更 #28 起改为回收站制）；重名自动加序号

自清理：session.json 先备份，结束恢复；不新建任何持久文件。
"""
import json
import shutil
import sys
import types
from pathlib import Path

AI_DIR = Path(r"D:\cangku\AI")
KB_DIR = Path(r"D:\cangku\知识库")
for p in (str(AI_DIR), str(KB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from brain import lexicon, reason, segment, session as sess_mod, state as state_mod  # noqa: E401
from kb import inception_api  # noqa: E401

SESSION_FILE = state_mod.SESSION_FILE

passed, failed = [], []


def ck(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  [{}] {}{}".format("✓" if cond else "✗", name, (" — " + str(detail)[:80]) if (detail and not cond) else ""))


print("══ 1. 词典与分词（虚字不再是生词）══")
la = lexicon.load_all()
probe = {"为": "介词", "把": "介词", "被": "介词", "和": "连词", "因为": "连词", "所以": "连词",
         "是": "动词", "有": "动词", "了": "助词", "着": "助词", "吧": "语气词",
         "他": "代词", "它": "代词", "这": "代词", "应该": "模态词", "必须": "模态词",
         "一": "数词", "个": "量词", "上": "方位词", "今天": "时间词", "就": "副词", "都": "副词"}
bad = [w for w, pos in probe.items() if la.get(w, {}).get("词性") != pos]
ck("23 个常用虚词抽查全部登记且词性正确", not bad, bad)

for sent, must_not in [("为什么铁会生锈", ["为", "什", "么", "会"]),
                       ("把铁放在水里会怎样", ["把", "在", "会"]),
                       ("铜会给环境带来污染吗", ["会", "给", "吗"]),
                       ("铁和铜哪个容易生锈", ["和", "哪"])]:
    unk = [w for k, w in segment.segment(sent, la) if k == "未知"]
    hit = [w for w in must_not if w in unk]
    ck("「{}」虚字零误判".format(sent), not hit, "误判:{} 生词:{}".format(hit, unk))

unk = [w for k, w in segment.segment("铜会给环境带来污染吗", la) if k == "未知"]
ck("内容词仍如实报生词（铜/污染等没入库的词）", "铜" in unk, unk)

print("══ 2. 类比攻坚不再把虚字当金属 ══")
subs_ok = True
for sent in ["为什么铁会生锈", "把铁放在水里会怎样", "铁和铜哪个容易生锈"]:
    unk = [w for k, w in segment.segment(sent, la) if k == "未知"]
    outs = reason.analogize(sent, reason.SEED_RULES, unk)
    bad_sub = [o for o in outs if o["new"] in ("为", "把", "和", "会", "吗", "在")]
    if bad_sub:
        subs_ok = False
        print("    翻车:", sent, bad_sub)
ck("三句里虚字零代入（为/把/和/会/吗/在 不再是实体候选）", subs_ok)

print("══ 3. 先行词池按切片可见域过滤 ══")
backup = SESSION_FILE.read_bytes() if SESSION_FILE.exists() else None
try:
    sess = sess_mod.Session()
    st = sess.state
    # 规则词表进分词词典（铁/潮湿/金属/环境 来自规则；断连用种子兜底）
    for w in ("金属", "铁", "潮湿", "环境"):
        ck("分词词典含规则词「{}」".format(w), w in sess._lex)
    # 造两个测试切片（名字带批次号，绝不撞真实数据）
    st.slices["t27a"] = {"name": "测试A27", "created": "2026-09-25T00:00:00"}
    st.slices["t27b"] = {"name": "测试B27", "created": "2026-09-25T00:00:00"}
    st.recent_entities = []   # 隔离：先行词池清空（真实状态在 finally 里恢复）
    st.active_slice = "t27a"
    sess._note_entities([{"var": "测试元素", "val": "钨27", "how": "测试"}])
    last = st.recent_entities[-1]
    ck("实体条目带切片标签",
       isinstance(last, dict) and last.get("slice") == "t27a" and last.get("val") == "钨27", last)
    # 未合并：在 B 切片里问「它」，绑不到 A 切片的「钨27」
    st.active_slice = "t27b"
    st.merged = []
    task = {"thinking": []}
    out = sess._bind_pronoun("它会生锈吗", task)
    ck("未合并：B 切片里「它」绑不到 A 的实体", out == "它会生锈吗", out)
    ck("未合并：思考链如实说可见范围里没有对象", any("可见范围" in t for t in task["thinking"]))
    # 合并 A：同一句话，绑到「钨27」
    st.merged = ["t27a"]
    task2 = {"thinking": []}
    out2 = sess._bind_pronoun("它会生锈吗", task2)
    ck("合并后：「它」绑到 A 切片的实体", out2 == "钨27会生锈吗", out2)
    # 旧格式字符串条目：祖父化全可见
    st.recent_entities.append("铜")
    st.merged = []
    task3 = {"thinking": []}
    out3 = sess._bind_pronoun("它值钱吗", task3)
    ck("旧格式字符串条目仍全可见域可用", out3 == "铜值钱吗", out3)
    # 持久化往返：dict 条目不被字符串化
    st.save()
    st2 = state_mod.SessionState.load()
    ok_rt = any(isinstance(x, dict) and x.get("val") == "钨27" and x.get("slice") == "t27a"
                for x in st2.recent_entities)
    ck("落盘往返后切片标签存活", ok_rt, st2.recent_entities[-3:])

    print("══ 4. 切片后端（预览/删除/重名去重）══")
    fake = types.SimpleNamespace(state=st2)
    inception_api._sess = fake   # 单例替身：切片操作打在内存态上，不落盘
    st2.save = lambda: None
    trash_backup = inception_api._trash_file().read_bytes() \
        if inception_api._trash_file().exists() else None
    # 视图带预览与时间
    st2.history.append({"role": "user", "text": "测试切片预览这句话很长很长很长很长很长",
                        "slice": "t27a", "time": "2026-09-25T12:00:00"})
    view = inception_api._slice_view(fake)
    row = next(s for s in view["slices"] if s["id"] == "t27a")
    ck("视图带最后消息预览（截 24 字）", row["last"] == "测试切片预览这句话很长很长很长很长很长"[:24], row)
    ck("视图带最后活跃时间", row["last_time"] == "2026-09-25T12:00:00", row)
    # 重名自动加序号
    r = inception_api.slice_op("new", {"name": "测试A27"})
    newrow = next(s for s in r["slices"] if s["id"] == r["active"])
    ck("新建撞名自动加序号", newrow["name"] == "测试A27 2", newrow["name"])
    new_sid = r["active"]
    # 删除：进回收站（变更 #28 起，不再直接并入主切片），切片消失，merged/active 清理
    st2.merged = [new_sid] if new_sid in st2.slices else []
    r2 = inception_api.slice_op("del", {"id": new_sid})
    ck("删除后切片消失", all(s["id"] != new_sid for s in r2["slices"]))
    ck("删除后活动切片回主切片", r2["active"] == "main")
    ck("删除后合并名单清理", new_sid not in r2["merged"])
    # 删除有记录的切片：记录随回收站条目搬走，恢复后归位
    r3 = inception_api.slice_op("new", {"name": "测试C27"})
    c_sid = r3["active"]
    st2.history.append({"role": "user", "text": "待迁移记录", "slice": c_sid,
                        "time": "2026-09-25T12:01:00"})
    inception_api.slice_op("del", {"id": c_sid})
    gone = [h for h in st2.history if h.get("text") == "待迁移记录"]
    ck("被删切片的记录从可见历史搬走", not gone, gone)
    t = inception_api._trash_load()
    tit = next((x for x in t["items"] if x["sid"] == c_sid), None)
    ck("记录随回收站条目保存不丢失", tit is not None and
       any(r.get("text") == "待迁移记录" for r in tit["records"]), tit)
    inception_api.slice_op("restore", {"id": c_sid})
    back = [h for h in st2.history if h.get("text") == "待迁移记录"]
    ck("从回收站恢复后记录归位", back and back[0]["slice"] == c_sid, back)
    # 主切片不可删
    r4 = inception_api.slice_op("del", {"id": "main"})
    ck("主切片拒绝删除", r4.get("ok") is False)
    inception_api._sess = None   # 替身还原
    tf = inception_api._trash_file()
    if trash_backup is None:
        if tf.exists():
            tf.unlink()
    else:
        tf.write_bytes(trash_backup)
finally:
    if backup is not None:
        SESSION_FILE.write_bytes(backup)   # 恢复真实会话
    inception_api._sess = None

print()
print("== 变更 #27 专项：{} 通过，{} 失败 ==".format(len(passed), len(failed)))
if failed:
    print("失败项：", failed)
    sys.exit(1)
print("全部通过 ✓")
