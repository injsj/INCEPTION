# -*- coding: utf-8 -*-
"""推理层深化专项（变更 #23）：域拦检 / 并行一致性 / 试假设攻坚。"""
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


print("══ 1. 域拦检 ══")
RULES = [
    {"id": "P1", "when": ["受力=平衡"], "then": "加速度=零",
     "tier": "高", "domain": "物理", "source": "测试"},
    {"id": "B1", "when": ["加速度=零"], "then": "警觉=低",
     "tier": "高", "domain": "生物", "source": "测试"},
    {"id": "B2", "when": ["警觉=低"], "then": "逃跑=不会",
     "tier": "高", "domain": "生物", "source": "测试"},
]
ry = reason.Reasoner(RULES)
ry.assert_fact("受力", "平衡")          # 用户陈述无域=通配
d = ry.forward()
check("物理规则照常推出加速度=零", any(x["then"] == "加速度=零" for x in d))
check("生物规则不吃物理域结论（警觉=低被拦）",
      not any(x["then"] == "警觉=低" for x in d))
ry2 = reason.Reasoner(RULES)
ry2.assert_fact("加速度", "零")         # 用户直接陈述 → 通配，生物规则该吃
d2 = ry2.forward()
check("用户直接陈述的事实任何域都可消费（警觉=低推出）",
      any(x["then"] == "警觉=低" for x in d2))
check("域内链式照常（逃跑=不会推出）", any(x["then"] == "逃跑=不会" for x in d2))

print("══ 2. 并行与串行结果一致 ══")
CHAIN = [{"id": "C%d" % i,
          "when": ["v%d=1" % i],
          "then": "v%d=1" % (i + 1),
          "tier": "高", "domain": "逻辑", "source": "测试"}
         for i in range(12)]
ry3 = reason.Reasoner(CHAIN)
ry3.assert_fact("v0", "1")
d3 = ry3.forward()                       # 12 条规则 ≥8 → 走并行路径
check("12 环链并行推出 v12=1", any(x["then"] == "v12=1" for x in d3))
check("链上每环都触发（12 条 derived）", len(d3) == 12)
# 竞争假设语义不变
COMP = [
    {"id": "X1", "when": ["触发=是"], "then": "结果=甲", "tier": "高",
     "domain": "生物", "source": "测试"},
    {"id": "X2", "when": ["触发=是"], "then": "结果=乙", "tier": "中",
     "domain": "生物", "source": "测试"},
] + CHAIN[:6]                            # 凑够 8 条走并行
ry4 = reason.Reasoner(COMP)
ry4.assert_fact("触发", "是")
ry4.forward()
check("并行下竞争假设仍并存不覆盖", len(ry4.competing) == 1)

print("══ 3. 试假设攻坚 ══")
G_RULES = [
    {"id": "G18", "when": ["金属=铁", "环境=潮湿"], "then": "生锈=会",
     "tier": "高", "domain": "化学", "source": "测试"},
    {"id": "G18b", "when": ["环境=干燥"], "then": "生锈=不会",
     "tier": "高", "domain": "化学", "source": "测试"},
]
ry5 = reason.Reasoner(G_RULES)
ry5.assert_fact("金属", "铁")
branches = ry5.hypothesize(["环境=潮湿"])
env = [b for b in branches if any(v == "环境" for v, _ in b["assign"])]
check("环境缺口枚举出候选值分支", len(env) >= 2)
wet = [b for b in branches if ("环境", "潮湿") in b["assign"]]
check("假设环境潮湿 → 推出生锈=会",
      wet and any(x["then"] == "生锈=会" for x in wet[0]["derived"]))
dry = [b for b in branches if ("环境", "干燥") in b["assign"]]
check("假设环境干燥 → 推出生锈=不会（反向分支也有结论）",
      dry and any(x["then"] == "生锈=不会" for x in dry[0]["derived"]))
b0 = reason.Reasoner(G_RULES)
check("无缺失条件时不产分支", b0.hypothesize([]) == [])

print("══ 3b. 双缺口组合分支（变更 #25 补）══")
ry6 = reason.Reasoner(G_RULES)          # 两个条件都缺：金属、环境都没说
branches6 = ry6.hypothesize(["金属=铁", "环境=潮湿"])
combo = [b for b in branches6 if len(b["assign"]) == 2]
check("双缺口产出组合值对分支", len(combo) >= 1)
hit = [b for b in combo if b["assign"] == (("金属", "铁"), ("环境", "潮湿"))]
check("组合（金属=铁 且 环境=潮湿）→ 生锈=会",
      hit and any(x["then"] == "生锈=会" for x in hit[0]["derived"]))
single = [b for b in branches6 if b["assign"] == (("环境", "潮湿"),)]
check("单补一个条件推不出（证明组合的必要性）",
      single and not single[0]["derived"])

print()
print("== 专项结果：{} 通过，{} 失败 ==".format(ok, fail))
