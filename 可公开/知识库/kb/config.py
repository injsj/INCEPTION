# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】路径/端口/限额常数
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""全局配置：所有可自定义项集中在这里。改完直接生效（名称类需重启）。"""
import os
from pathlib import Path

# ── 固定名称（启动时校验：均不能为空且互不相同）──
ADMIN_NAME = "admin"      # 管理员的固定名称
AI_NAME = "AI助手"         # AI 的固定名称

# ── 网络 ──
PORT = 8320               # 服务端口
HTTPS_CERT = None         # 对外访问时建议启用 HTTPS：填 ("cert.pem", "key.pem")
MAX_BODY_BYTES = 1_000_000  # 请求体上限 1MB，防大文件攻击
RATE_PER_MIN = 60         # 目录/申请/知识调用：每 IP 每分钟上限
RATE_BURST = 300          # 其他接口：每 IP 每分钟上限

# ── 目录布局 ──
ROOT = Path(__file__).resolve().parent.parent
META_DIR = ROOT / "meta"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
STATIC_DIR = ROOT / "static"
HISTORY_DIR = DATA_DIR / ".history"

STATE_FILE = META_DIR / "state.json"
# ── 机密目录（2026-09-27 变更 #33）：密钥移出仓库树，物理隔离绝不入库 ──
SECRET_DIR = Path(os.environ.get("CANGKU_SECRET_DIR", r"D:\机密"))
KEYS_FILE = SECRET_DIR / "keys.json"
COUNTERS_FILE = META_DIR / "counters.json"
TYPES_FILE = META_DIR / "types.json"
APPLICATIONS_FILE = META_DIR / "applications.json"
INDEX_FILE = META_DIR / "index.json"
LOG_STATE_FILE = META_DIR / "logstate.json"

# ── 分区（物理目录用 ASCII，中文只做显示标签）──
ZONES = ["trusted", "suspect", "pending", "requests"]
ZONE_LABEL = {
    "trusted": "信任区",
    "suspect": "存疑区",
    "pending": "待审核区",
    "requests": "申请区",
}
# 常规可调用分区；待审核区仅 AI 与管理员可调用，访问方需管理员临时授权
CALLABLE_ZONES = {"trusted", "suspect"}

# 知识条目的强制字段（创建时缺一拒绝入库）
ENTRY_REQUIRED_FIELDS = ["title", "summary", "usage", "experience", "limitations"]

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def validate():
    """启动自检：校验名称并创建目录骨架。"""
    if not ADMIN_NAME.strip() or not AI_NAME.strip():
        raise SystemExit("配置错误：管理员或 AI 的固定名称不能为空")
    if ADMIN_NAME == AI_NAME:
        raise SystemExit("配置错误：管理员与 AI 的固定名称不能相同")
    for p in (META_DIR, DATA_DIR, LOG_DIR, STATIC_DIR, HISTORY_DIR):
        p.mkdir(parents=True, exist_ok=True)
    for z in ZONES:
        (DATA_DIR / z).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    validate()
    print("配置校验通过")
