# -*- coding: utf-8 -*-
"""组句器真组合验证（变更 #22）：全规则逐条过 + 新词证明 + 理解侧抽测。"""
import io
import sys

sys.path.insert(0, r"D:\cangku\AI")
from brain import compose, lexicon, parse, dlgact   # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 全部规则结论（G 类 20 条 + 种子 7 条）：(条件, 结论, 问句事实)
RULES = [
    (["天体=地球"], "绕转对象=太阳", "地球"),
    (["天体=月球"], "绕转对象=地球", "月球"),
    (["传声介质=真空"], "声音传播=不能", "真空"),
    (["传光介质=真空"], "光传播=可以", "真空"),
    (["物体密度=大于液体"], "浮沉=下沉", "大于液体"),
    (["物体密度=小于液体"], "浮沉=上浮", "小于液体"),
    (["电压=不变;电阻=增大"], "电流=减小", "增大"),
    (["水温=100摄氏度;气压=标准大气压"], "水状态=沸腾", "100摄氏度"),
    (["水温=低于0摄氏度"], "水状态=结冰", "低于0摄氏度"),
    (["反应=燃烧;氧气=不足"], "燃烧状态=熄灭", "燃烧"),
    (["金属=铁;环境=潮湿"], "生锈=会", "铁"),
    (["气体=二氧化碳;试剂=澄清石灰水"], "现象=变浑浊", "二氧化碳"),
    (["溶液=酸性;试纸=蓝色石蕊"], "试纸颜色=变红", "酸性"),
    (["溶液=碱性;试纸=红色石蕊"], "试纸颜色=变蓝", "碱性"),
    (["图形=三角形"], "内角和=180度", "三角形"),
    (["图形=四边形"], "内角和=360度", "四边形"),
    (["数=偶数"], "被2整除=能", "偶数"),
    (["植物=绿色植物;光照=充足"], "光合作用=进行", "绿色植物"),
    (["光合作用=进行"], "氧气释放=有", None),
    (["氧气=无"], "人生存=不能", None),
    (["饥饿=极高"], "觅食行为=激烈", "极高"),
    (["觅食行为=激烈", "捍卫亲友意念=无"], "威胁亲人=高", None),
    (["威胁亲人=高", "友人在场=是"], "目标转为友人=中", None),
    (["饥饿=高", "食物可得=是"], "觅食行为=缓和", None),
    (["自制力=不足", "觅食行为=激烈"], "突破禁忌=高", None),
    (["截止分钟<10"], "紧迫度=高", None),
    (["受力=平衡"], "加速度=零", "平衡"),
]

print("══ 全规则组句逐条过目 ══")
for when, then, fact in RULES:
    when = [w for w in ";".join(when).split(";") if w]
    facts = [{"var": "", "val": fact}] if fact else []
    s = compose.compose_conclusion(
        {"then": then, "conf": 0.9, "rule": "T"}, when, facts)
    print("  {:<28} → {}".format(then, s))

print()
print("══ 新词证明：词典登记「公转」（只标环绕类，走正式扩展通道，没人写句式）══")
import json as _json
from brain import config as _cfg
_extra = _cfg.STATE / "lexicon_grammar_extra.json"
_extra.write_text(_json.dumps(
    [{"词": "公转", "词性": "动词", "情态类型": "环绕", "论元": ["主体", "对象"]}],
    ensure_ascii=False), encoding="utf-8")
import importlib
importlib.reload(lexicon)
importlib.reload(compose)   # 两侧都重新读词典视图
s = compose.compose_conclusion(
    {"then": "公转对象=太阳", "conf": 1.0, "rule": "NEW"},
    ["星球=火星"], [{"var": "星球", "val": "火星"}])
print("  公转对象=太阳（主体=火星） → " + s)
ok_new = "火星绕着太阳转" in s
_extra.unlink()   # 证明完毕即清理，不留残留
importlib.reload(lexicon)
importlib.reload(compose)

print()
print("══ 理解侧抽测 ══")
CASES = [
    ("怎么才能让铁生锈", "query_condition"),
    ("燃烧需要什么条件", "query_condition"),
    ("为什么天是蓝的", "query_why"),
    ("凸透镜成像规律是什么", "query_fact"),
    ("铁会生锈吗", "query_confirm"),    # 吗=确认焦点（确认类本来就是独立类别）
    ("提醒我明天浇花", "command"),
    ("十乘十等于多少", "query_fact"),
]
allok = ok_new
for text, want in CASES:
    got = parse.parse(text)["clauses"][0]["intent"]
    ok = "✓" if got == want else "✗(得 %s)" % got
    if got != want:
        allok = False
    print("  {:<16} 期望 {:<15} {}".format(text, want, ok))
print("  问候识别: 你好呀=%s / 各位好=%s / 你好十乘十(应非问候)=%s" % (
    dlgact.classify("你好呀"), dlgact.classify("各位好"),
    dlgact.classify("你好，十乘十是多少")))
print()
print("总体：" + ("全部符合预期" if allok else "有偏差，见上"))
