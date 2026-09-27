# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】图标生成脚本（眼睛图案按设计者描述绘制）
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""INCEPTION 图标生成器（变更 #14）：竖瞳之眼 × 高级黑底。

运行一次即可：
    python make_icon.py
产出：
    AI\\inception\\inception.ico     多尺寸（16~256）桌面/打包用
    知识库\\static\\inception.ico    浏览器页签图标（inception.html 已引用）

画法与界面里的 SVG 眼睛同一条贝塞尔眼眶，保证图标和界面长一个样：
    左弧 M50 6 C25 42 25 98 50 134，右弧 C75 98 75 42 50 6 Z
白色是暖调高级白 #ece7dd；底不是纯黑，是带微弱中央晕的 #0b0b0e。
512 超采样绘制后 LANCZOS 缩到各尺寸，小尺寸自动简化（只留眼眶+瞳孔）。
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent                 # D:\cangku\AI\inception
STATIC = HERE.parents[1] / "知识库" / "static"          # D:\cangku\知识库\static

BG = (11, 11, 14)            # 高级黑（非纯黑）
GLOW = (22, 22, 28)          # 中央微晕
WHITE = (236, 231, 221)      # 高级白（暖）

S = 512                      # 超采样画布
VU = S / 140.0               # SVG viewBox(100x140) → 画布的缩放（按高对齐）


def _bez(p0, p1, p2, p3, n=90):
    """三次贝塞尔采样。"""
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _map(pt, ox, oy):
    """viewBox 坐标 → 画布坐标（水平居中）。"""
    return (ox + pt[0] * VU, oy + pt[1] * VU)


def draw_eye(size, simple=False):
    """在 size×size 高级黑底上画竖瞳之眼。simple=True 时只画眼眶+瞳孔（小尺寸防糊）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # 底：高级黑 + 径向微晕
    bg = Image.new("RGBA", (size, size), BG + (255,))
    glow = Image.new("L", (size, size), 0)
    gd = ImageDraw.Draw(glow)
    steps = 48
    for i in range(steps, 0, -1):
        r = size * 0.62 * i / steps
        v = int(26 * (1 - i / steps))
        gd.ellipse([size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r], fill=v)
    glow = glow.filter(ImageFilter.GaussianBlur(size / 40))
    tint = Image.new("RGBA", (size, size), GLOW + (255,))
    bg = Image.composite(tint, bg, glow.point(lambda v: min(255, v * 3)))
    img.alpha_composite(bg)

    k = size / S                      # 相对 512 基准的比例
    vu = VU * k
    ox = (size - 100 * vu) / 2        # 水平居中（viewBox 宽 100）
    oy = 0.0

    left = _bez((50, 6), (25, 42), (25, 98), (50, 134))
    right = _bez((50, 134), (75, 98), (75, 42), (50, 6))
    outline = [_map(p, ox, oy) for p in left + right]

    lw = max(2, int(round(3.2 * k)))          # 眼眶线宽
    iris_r = 21 * vu
    pupil_r = 9.5 * vu
    cx, cy = _map((50, 70), ox, oy)

    # 发光层（柔焦副本垫底）
    glow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow_layer)
    g.line(outline, fill=WHITE + (255,), width=lw, joint="curve")
    g.ellipse([cx - pupil_r, cy - pupil_r, cx + pupil_r, cy + pupil_r], fill=WHITE + (255,))
    if not simple:
        g.ellipse([cx - iris_r, cy - iris_r, cx + iris_r, cy + iris_r],
                  outline=WHITE + (255,), width=max(1, int(1.8 * k)))
    blur = glow_layer.filter(ImageFilter.GaussianBlur(6 * k + 2))
    img.alpha_composite(blur)                 # 晕
    img.alpha_composite(glow_layer)           # 锐利本体
    return img


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for sz in sizes:
        big = draw_eye(512, simple=(sz < 32))
        frames.append(big.resize((sz, sz), Image.LANCZOS))
    ico = HERE / "inception.ico"
    frames[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    # 备用 PNG（任务栏预览/文档用）
    draw_eye(512).save(HERE / "inception.png")
    # 浏览器页签图标
    STATIC.mkdir(parents=True, exist_ok=True)
    frames[-1].save(STATIC / "inception.ico", format="ICO",
                    sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print("图标已生成：")
    print("  " + str(ico))
    print("  " + str(HERE / "inception.png"))
    print("  " + str(STATIC / "inception.ico"))


if __name__ == "__main__":
    main()
