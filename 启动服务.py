# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】知识库服务启动入口
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
# ═══════════════════════════════════════════════════════
#   知识库一键启动程序
#   PyCharm 里右键本文件 → 运行（Run）即可；双击也可以。
#   它会自动：装依赖（缺的话）→ 生成管理员和 AI 密钥（第一次）
#          → 启动服务 → 显示目录/管理页面地址
# ═══════════════════════════════════════════════════════
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # D:\cangku
SERVER = ROOT / "知识库"                      # 知识库程序本体在这
sys.path.insert(0, str(SERVER))


def ensure_deps():
    """缺少 fastapi/uvicorn 时自动安装（只发生在第一次运行）。"""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        return True
    except ImportError:
        print("首次运行，正在自动安装依赖（fastapi、uvicorn）……")
        print("（需要联网，几分钟一次，以后不再出现）")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                            str(SERVER / "requirements.txt")])
        if r.returncode != 0:
            print("依赖安装失败：请检查网络，或手动执行：")
            print("    pip install -r {}".format(SERVER / "requirements.txt"))
            return False
        return True


def ensure_keys():
    """第一次运行时自动生成管理员密钥和 AI 密钥，并各显示一次。"""
    from kb import auth, config
    config.validate()
    created = []
    if not auth.name_exists(config.ADMIN_NAME):
        k = auth.add_key(config.ADMIN_NAME, "admin")
        created.append(("管理员", config.ADMIN_NAME, k))
    if not auth.name_exists(config.AI_NAME):
        k = auth.add_key(config.AI_NAME, "ai")
        created.append(("AI", config.AI_NAME, k))
    if created:
        print("\n" + "=" * 62)
        print("  【重要】新密钥已生成，只显示这一次，请立即复制保存！")
        for role, name, k in created:
            print("  {}「{}」的密钥：".format(role, name))
            print("      " + k)
        print("=" * 62)
        try:
            input("  复制保存好后，按回车继续启动服务……")
        except EOFError:
            pass


def main():
    if not ensure_deps():
        sys.exit(1)
    ensure_keys()
    from kb.main import run   # 统一从服务入口启动（打印地址、按开关决定绑定）
    run()


if __name__ == "__main__":
    main()
