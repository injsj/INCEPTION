# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【原话】拆解问题：拆不开就不拆，拆得开就并行——原子操作清单为递归底线（规格 §4）
# 【拍板】数值绑定与一元一次方程求解（变更 #24）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""原子操作清单（变更 #11，规格 §4/§5 + 变更 #6 诚实清单第一项）。

推演层的"扳手与量尺"：四个白名单原子操作，纯函数、无副作用（查库只读），
每次执行留痕 state\\atomic_ops.jsonl（JSONL 追加）——数值趋同矿工的原料。

四族：
  算术   计算 3*3+2        （安全 AST 求值，不用 eval；支持 + - * / % ** 括号小数）
  比较   比较 3*3 和 3+3+3 （两边可为算术表达式；相等判断容差 1e-9）
  时间   现在几点 / 换算 90分钟 / 45分钟后是几点 / 还有多久到晚上8点
  查库   查库 P5 / 查库 性能    （委托 executor 白名单，与对话级查询同一通路）

逻辑域操作，结果置信 100%；失败给明确原因，绝不静默。
"""
import ast
import json
import operator
import re
import time
from datetime import datetime, timedelta

from . import config, executor, timenorm

ATOM_LOG = config.STATE / "atomic_ops.jsonl"

# ──────────────────── 留痕 ────────────────────

def record(op, inp, out, ok=True):
    """JSONL 追加留痕（append-only，崩溃最多损一行，矿工跳过坏行）。"""
    config.ensure()
    with ATOM_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "op": op, "input": inp, "output": out, "ok": ok},
                           ensure_ascii=False) + "\n")


def load_log(limit=500):
    """读最近的留痕（矿工用）。"""
    if not ATOM_LOG.exists():
        return []
    lines = ATOM_LOG.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


# ──────────────────── 算术（安全求值，eval 的替代品）────────────────────

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
MAX_EXPR_LEN = 100
MAX_POW_EXP = 999          # 防 2**10**9 类算力炸弹


def safe_eval(expr, bindings=None):
    """把算术表达式安全求值。非法成分抛 ValueError（上层转成人类可读提示）。
    变更 #24：支持变量名——AST Name 节点只认绑定表里的词（如「密度」），
    绑定表外的标识符一律拒绝，不开放任意标识符。"""
    if len(expr) > MAX_EXPR_LEN:
        raise ValueError("表达式太长（>{} 字符）".format(MAX_EXPR_LEN))
    bindings = bindings or {}
    tree = ast.parse(expr.strip(), mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            # 第三轮审查：True/False 是 int 子类，须排除——"计算 True"不该等于 1
            if isinstance(n.value, bool):
                raise ValueError("布尔值不是算术操作数（只支持数字和 + - * / % ** 括号）")
            return n.value
        if isinstance(n, ast.Name):
            if n.id in bindings:
                return bindings[n.id]
            raise ValueError("「{}」没有绑定数值——先告诉我它等于几（如：{}是7.8）".format(n.id, n.id))
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS:
            if isinstance(n.op, ast.Pow):
                # 指数先求值并封顶，底数过大也封顶，防算力炸弹
                r = ev(n.right)
                l = ev(n.left)
                if abs(r) > MAX_POW_EXP or abs(l) > 1e9:
                    raise ValueError("乘方规模超限（指数≤{}，底数≤1e9）".format(MAX_POW_EXP))
                return operator.pow(l, r)
            return _BINOPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNOPS:
            return _UNOPS[type(n.op)](ev(n.operand))
        raise ValueError("表达式含不支持的成分（只支持数字和 + - * / % ** 括号）")

    return ev(tree)


def _norm_expr(s):
    """中文输入归一化：全角符号 → 半角，× ÷ → * /。"""
    return (s.replace("×", "*").replace("÷", "/")
             .replace("（", "(").replace("）", ")")
             .replace("，", ",").strip())


# ── 中文数字算术（变更 #15 增补）：「十乘十等于多少」→ 10*10 ──
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def cn2num(s):
    """中文数字串 → int（支持到万级组合：二十三、一百零五、三万二千）。
    口语省略如「三千二」按三千零二算（已知局限，不影响主用法）。"""
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            section += (number or 1) * _CN_UNIT[ch]
            number = 0
        elif ch == "万":
            section = (section + number) * 10000
            total += section
            section, number = 0, 0
        else:
            return None
    return total + section + number


def chinese_arith_norm(text):
    """把「二十五加七」「一百除以四」归一成安全算式；认不出/不含运算符 → None。
    只处理「除以」（A除以B=A/B）；口语「A除B」是 B/A，易误会，不支持。"""
    s = _norm_expr(text)
    s = re.sub(r"乘以|乘", "*", s)
    s = re.sub(r"除以", "/", s)
    s = re.sub(r"加", "+", s)
    s = re.sub(r"减", "-", s)

    def _rep(m):
        v = cn2num(m.group(0))
        return str(v) if v is not None else m.group(0)

    s = re.sub("[零一二三四五六七八九十百千万两]+", _rep, s)
    if not re.fullmatch(r"[\d+\-*/%().]+", s):
        return None
    if not any(op in s for op in "+-*/%"):
        return None
    return s


def calculate(expr, bindings=None):
    """算术原子操作。bindings=会话数值绑定表（变更 #24）。返回 (ok, 结果文本)。"""
    expr = _norm_expr(expr)
    try:
        v = safe_eval(expr, bindings)
    except ZeroDivisionError:
        record("计算", expr, "除零错误", ok=False)
        return False, "除数为零，算不了（{}）".format(expr)
    except (ValueError, SyntaxError, TypeError, MemoryError, OverflowError) as e:
        record("计算", expr, str(e), ok=False)
        return False, "算不了「{}」：{}".format(expr, e)
    out = int(v) if isinstance(v, float) and v.is_integer() else v
    record("计算", expr, out)
    return True, "{} = {}（算术结果，置信 100%）".format(expr, out)


# ──────────────────── 一元一次方程（变更 #24，甲 7）────────────────────
# 「x加3等于8，x等于几」「某数乘以2减4等于10」→ 数值法线性反解：
# f(0)=b，f(1)=a+b → x=(c-b)/a。只解一元一次；不是一元一次明说不会。
_UNKNOWN_WORDS = ("x", "X", "未知数", "某数", "这个数", "它")


def _eq_norm(s):
    """方程侧归一化：中文运算符→符号，中文数字→阿拉伯，未知数词→x。"""
    s = _norm_expr(s)
    s = re.sub(r"乘以|乘", "*", s)
    s = re.sub(r"除以", "/", s)
    s = re.sub(r"加", "+", s)
    s = re.sub(r"减", "-", s)

    def _rep(m):
        v = cn2num(m.group(0))
        return str(v) if v is not None else m.group(0)

    s = re.sub("[零一二三四五六七八九十百千万两]+", _rep, s)
    for w in ("未知数", "某数", "这个数"):
        s = s.replace(w, "x")
    return s


def solve_linear(text, bindings=None):
    """尝试把一句话按一元一次方程解。认不出方程结构 → None（交回上级路由）；
    认出但不是一元一次 → (False, 人话说明)；解出 → (True, 人话答案)。"""
    t = text.strip().strip("？？")
    m = re.split(r"等于|=", t)
    if len(m) < 2:
        return None
    left_s, right_s = m[0], m[1]
    # 右半常带问尾巴（「8，x等于几」「8是多少」）：取第一段数字表达式
    right_s = re.split("[，,]", right_s)[0]
    left_s = _eq_norm(re.sub(r"^(计算|算一下|求解|解方程|求)", "", left_s))
    right_s = _eq_norm(right_s)
    if not re.search(r"[xX]", left_s + right_s):
        return None                     # 没有未知数——普通算式，不归方程管
    if not re.fullmatch(r"[\d\sxX+\-*/%().]+", left_s + right_s):
        return None
    bindings = dict(bindings or {})

    def f(side, xv):
        return safe_eval(side, {**bindings, "x": xv, "X": xv})

    try:
        b, a1 = f(left_s, 0.0), f(left_s, 1.0)
        c = float(f(right_s, 0.0)) if not re.search(r"[xX]", right_s) else None
        if c is None:                   # 右边也含 x：移项 f(x)-g(x)=0
            c0, c1 = f(right_s, 0.0), f(right_s, 1.0)
            a, bb = a1 - b - (c1 - c0), b - c0
        else:
            a, bb = a1 - b, b - c
    except (ValueError, SyntaxError, ZeroDivisionError):
        return None                     # 结构超出归一化能力，交回上级
    if abs(a) < 1e-12:
        if abs(bb) < 1e-12:
            return False, "这个式子 x 取什么都成立（恒等式），不算方程"
        return False, "这个式子矛盾（{}={} 不成立），无解".format(left_s, right_s)
    x = -bb / a
    out = int(x) if float(x).is_integer() else round(x, 6)
    record("解方程", text, out)
    return True, "{} = {}（一元一次方程，数值法反解，置信 100%）".format("x", out)


# ──────────────────── 比较 ────────────────────

CMP_SPLIT = re.compile(r"\s*(?:和|与|跟|,|，)\s*")


def compare(text):
    """「比较 A 和 B」：两边各按算术表达式求值后比较。返回 (ok, 文本)。"""
    parts = [p for p in CMP_SPLIT.split(text.strip()) if p]
    if len(parts) != 2:
        return False, "比较需要两边，例如：比较 3*3 和 3+3+3"
    vals = []
    for p in parts:
        p = _norm_expr(p)
        try:
            vals.append((p, safe_eval(p)))
        except Exception:
            vals.append((p, None))
    (la, va), (lb, vb) = vals
    if va is None or vb is None:
        # 非数值：退化为字符串相等比较
        eq = la == lb
        record("比较", text, "相等" if eq else "不等")
        return True, "「{}」与「{}」{}（字符串比较）".format(la, lb, "完全相同" if eq else "不同")
    if abs(va - vb) < 1e-9:
        rel, sym = "相等", "="
    elif va > vb:
        rel, sym = "大于", ">"
    else:
        rel, sym = "小于", "<"
    record("比较", text, sym)
    return True, "{} {} {}（{} {} {}，置信 100%）".format(la, rel, lb, la, sym, lb)


# ──────────────────── 时间换算 ────────────────────

_UNIT_SECONDS = {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400}


def _fmt_seconds(sec):
    d, sec = divmod(int(round(sec)), 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    parts = []
    if d:
        parts.append("{} 天".format(d))
    if h:
        parts.append("{} 小时".format(h))
    if m:
        parts.append("{} 分钟".format(m))
    if s or not parts:
        parts.append("{} 秒".format(s))
    return " ".join(parts)


def time_convert(text, now=None):
    """时间换算原子操作。返回 (ok, 文本)。now 可注入（测试用）。"""
    now = now or datetime.now()
    t = text.strip()
    if t in ("现在几点", "现在几点了", "现在时间"):
        out = now.strftime("%H:%M")
        record("时间换算", t, out)
        return True, "现在是 {}（{}）".format(out, now.strftime("%Y-%m-%d"))
    m = re.match(r"^换算\s*(\d+(?:\.\d+)?)(秒|分钟|小时|天)$", t)
    if m:
        sec = float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
        out = _fmt_seconds(sec)
        record("时间换算", t, out)
        return True, "{}{} = {}（置信 100%）".format(m.group(1), m.group(2), out)
    m = re.match(r"^(\d+(?:\.\d+)?)(秒|分钟|小时|天)后是几点$", t)
    if m:
        sec = float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
        tgt = now + timedelta(seconds=sec)
        out = tgt.strftime("%Y-%m-%d %H:%M")
        record("时间换算", t, out)
        return True, "{}{}后是 {}（{}）".format(m.group(1), m.group(2),
                                           tgt.strftime("%H:%M"),
                                           "明天" if tgt.date() > now.date() else "今天")
    m = re.match(r"^还有多久到(.+?)[?？]?$", t)
    if m:
        tgt, why = timenorm.parse_time_str(m.group(1), now)
        if tgt is None:
            record("时间换算", t, why, ok=False)
            return False, "时间「{}」解析不了：{}（支持 晚上8点 / 早上7点30分 式）".format(m.group(1), why)
        delta = (tgt - now).total_seconds()
        out = _fmt_seconds(delta)
        record("时间换算", t, out)
        return True, "距离 {} 还有 {}".format(tgt.strftime("%H:%M"), out)
    return False, "支持的时间换算：现在几点 / 换算 90分钟 / 45分钟后是几点 / 还有多久到晚上8点"


# ──────────────────── 查库（只读，委托 executor 白名单）────────────────────

def query_kb(target, kb):
    """查库原子操作。target 是标记（P5）走 describe，否则走目录检索。"""
    target = target.strip()
    if re.fullmatch(r"[A-Za-z]+\d+", target):
        status, out = executor.execute("describe", {"target": target}, kb)
    else:
        status, out = executor.execute("search", {"topic": target}, kb)
    record("查库", target, "status=" + status)
    return status != "refused", out
