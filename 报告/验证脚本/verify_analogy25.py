# -*- coding: utf-8 -*-
"""丁批专项（变更 #25）：类比攻坚。"""
import io
import sys

sys.path.insert(0, r"D:\cangku\AI")
from brain import reason   # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ok = fail = 0


def check(name, cond):
    global ok, fail
    ok, fail = ok + bool(cond), fail + (not bool(cond))
    print("  [{}] {}".format("✓" if cond else "✗", name))


RULES = [
    {"id": "G18", "when": ["金属=铁", "环境=潮湿"], "then": "生锈=会",
     "tier": "高", "domain": "化学", "source": "测试"},
    {"id": "G8", "when": ["天体=地球"], "then": "绕转对象=太阳",
     "tier": "高", "domain": "物理", "source": "测试"},
]

print("══ 类比攻坚 ══")
a = reason.analogize("铜会生锈吗", RULES, ["铜", "会"])
check("铜→铁 类比迁移命中 G18",
      any(x["rule"] == "G18" and x["new"] == "铜" and x["old"] == "铁" for x in a))
check("虚字「会」不被当实体代入", not any(x["new"] == "会" for x in a))
check("类比标注旧实体与目标变量", a and a[0]["goal"] == "生锈" and a[0]["sub_var"] == "金属")

a2 = reason.analogize("铜会发光吗", RULES, ["铜"])
check("目标词不认得时不乱类比（发光无规则）", a2 == [])

a3 = reason.analogize("铁会生锈吗", RULES, [])
check("没有陌生词不产类比", a3 == [])

many = ["铜", "铝", "锌", "镍", "锡"]
a4 = reason.analogize("铜铝锌镍锡会生锈吗", RULES, many)
check("类比上限 3 条防刷屏", len(a4) <= 3)

# 组句检查：类比输出的句子要是通顺人话
sys.path.insert(0, r"D:\cangku\AI")
from brain import session as sess_mod   # noqa: E402
s = sess_mod.Session()
line = s._compose_analogy(a[0])
print("  组句实例：" + line)
check("类比组句含实体替换与免责标注",
      "铜" in line and "铁" in line and "潮湿" in line and "类比" in line)

print()
print("== 丁批专项：{} 通过，{} 失败 ==".format(ok, fail))
