# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【施工方】服务装配：启动自检/外部修改侦测/快照协程挂载
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""服务入口。启动：python -m kb.main

中间件顺序（每个请求依次经过）：
1. 请求体大小限制（防大文件攻击）
2. 按 IP 限流（目录/申请/调用更严格）
3. 管理端点来源 IP 硬隔离（仅 127.0.0.1）
4. 总开关（关闭时一切访问 503，仅 /health 与静态页面除外——重开用本地 CLI）

绑定地址在启动时决定：对外访问开 → 0.0.0.0，关 → 127.0.0.1（改开关需重启）。
另有一个后台协程每 30 秒侦测 data/ 目录的外部修改（如直接编辑文件）并记日志。"""
import asyncio
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import backup, config, ratelimit, store
from .audit import admin as alog
from .admin_api import router as admin_router
from .inception_api import router as inception_router
from .public_api import router as public_router

app = FastAPI(title="KB Server", docs_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    # 1. 请求体上限
    try:
        cl = int(request.headers.get("content-length") or 0)
    except ValueError:
        cl = 0
    if cl > config.MAX_BODY_BYTES:
        return JSONResponse({"error": "请求体过大"}, status_code=413)
    # 第二轮审查 H2：chunked 传输没有 Content-Length，上面拦不住。
    # 流式读取并累计字节数，超限即断；读完后写入请求体缓存供下游解析。
    te = request.headers.get("transfer-encoding", "").lower()
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and "chunked" in te:
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > config.MAX_BODY_BYTES:
                return JSONResponse({"error": "请求体过大"}, status_code=413)
        request._body = bytes(body)  # Starlette body()/json() 会优先读这个缓存

    ip = request.client.host if request.client else "?"

    # 2. 限流（静态资源不限）
    path = request.url.path
    if not path.startswith("/static"):
        limit = config.RATE_PER_MIN if path.startswith(
            ("/api/catalog", "/api/apply", "/api/kb", "/api/req")) else config.RATE_BURST
        if not ratelimit.allow(ip, limit):
            return JSONResponse({"error": "请求过于频繁，请稍后再试"}, status_code=429)

    # 3. 管理端点仅本机
    if path.startswith("/admin") and ip not in config.LOCAL_HOSTS:
        return JSONResponse({"error": "禁止访问"}, status_code=403)

    # 4. 总开关：关闭时禁止一切访问（/health 供本机确认进程状态，静态页可渲染"已关闭"提示）
    if not path.startswith(("/health", "/static")) and not store.get_state().get("enabled", True):
        return JSONResponse({"error": "服务已被管理员关闭"}, status_code=503)

    return await call_next(request)


@app.get("/")
def index():
    return RedirectResponse("/static/catalog.html")


@app.get("/health")
def health(request: Request):
    if (request.client.host if request.client else "") not in config.LOCAL_HOSTS:
        raise HTTPException(403, "仅本机可查看")
    s = store.get_state()
    return {"ok": True, "enabled": s["enabled"], "allow_remote": s["allow_remote"],
            "counts": store.counts()}


app.include_router(public_router)
app.include_router(admin_router)
app.include_router(inception_router)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


# ── 外部修改侦测（PyCharm 直接改文件等场景，30 秒一轮）──

def _snapshot():
    snap = {}
    for zone in ("trusted", "suspect", "pending"):
        zdir = config.DATA_DIR / zone
        if not zdir.exists():
            continue
        for f in zdir.rglob("*.json"):
            try:
                st = f.stat()
                snap[str(f.relative_to(config.DATA_DIR))] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    return snap


async def _watch_external():
    snap = _snapshot()
    while True:
        await asyncio.sleep(30)
        try:
            cur = _snapshot()
            if cur != snap:
                new = sorted(set(cur) - set(snap))
                gone = sorted(set(snap) - set(cur))
                mod = sorted(k for k in cur if k in snap and cur[k] != snap[k])
                if new or gone or mod:
                    alog("检测到知识库外部修改：新增 {}，删除 {}，变更 {}（不记录操作者，"
                         "建议通过管理端修改以便完整审计）".format(new, gone, mod))
                    store.rebuild_index()
                snap = cur
        except Exception as e:  # 侦测失败绝不影响服务
            alog("外部修改侦测异常：{}".format(e))


@app.on_event("startup")
async def startup():
    config.validate()
    n, bad = store.rebuild_index()
    s = store.get_state()
    alog("服务启动：索引 {} 条{}{}，对外访问：{}".format(
        n, ("，损坏文件 {} 个".format(len(bad)) if bad else ""),
        "，总开关：关闭" if not s["enabled"] else "", "开" if s["allow_remote"] else "关（仅本机）"))
    asyncio.create_task(_watch_external())
    asyncio.create_task(backup.watch_backup(alog))   # 变更 #30：每日快照+14 份轮换


def run():
    config.validate()
    s = store.get_state()
    host = "0.0.0.0" if s.get("allow_remote") else "127.0.0.1"
    print("=" * 56)
    print("  知识库服务启动")
    print("  绑定地址 : {}（{}）".format(host, "对外访问已开启" if host == "0.0.0.0" else "仅本机"))
    print("  端口     : {}".format(config.PORT))
    print("  目录界面 : http://127.0.0.1:{}/static/catalog.html".format(config.PORT))
    print("  管理界面 : http://127.0.0.1:{}/static/admin.html（仅本机）".format(config.PORT))
    if host == "0.0.0.0" and not config.HTTPS_CERT:
        print("  警告     : 对外访问未启用 HTTPS，密钥可能被局域网嗅探")
    print("=" * 56)
    kwargs = dict(host=host, port=config.PORT, log_level="warning")
    if config.HTTPS_CERT:
        kwargs["ssl_certfile"], kwargs["ssl_keyfile"] = config.HTTPS_CERT
    import uvicorn
    uvicorn.run("kb.main:app", **kwargs)


if __name__ == "__main__":
    run()
