# -*- coding: utf-8 -*-
# ═══ 设计归属（2026-09-26 变更 #32）═══
# 【逐字】INCEPTION 桌面壳界面为设计者设计：白色抽象竖眼图标/深黑聊天界面/中央淡金眼注视/睁眼=运行、闭眼=无任务/眼碎成发光碎片飘落=系统错误；知识库界面米黄+深木复古庄重
# 【施工方】Tkinter 实现与动效代码
# 标签图例：【逐字】设计者逐字原话 【原话】设计者口述原意 【拍板】设计者确认的施工方提案 【隐含】施工方推断 【施工方】工程补建
# ═══════════════════════════
"""INCEPTION 桌面壳（变更 #14）：把「知识库服务 + 眼睛界面」包成一个窗口。

做了什么、不做什么：
    - 启动前先看 8320 端口的知识库服务在不在；不在就静默拉起一个
      （本程序关窗时不会杀掉它——服务可能正被账房/AI 用着）。
    - 窗口只是浏览器壳（WebView2，Windows 自带），页面还是
      知识库\\static\\inception.html——改界面不用动这个壳。
    - 不新增端口、不对外暴露；聊天接口沿用 admin_guard（仅本机 + 管理密钥）。

直接运行：
    python app.py          （或运行根目录的 启动INCEPTION.py）
打包成 exe（可选）：
    python build_exe.py    （PyInstaller 单文件，图标 inception.ico）
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent          # D:\cangku\AI\inception
ROOT = HERE.parents[1]                           # D:\cangku
KB_DIR = ROOT / "知识库"
HEALTH = "http://127.0.0.1:8320/health"
PAGE = "http://127.0.0.1:8320/static/inception.html"


def service_up():
    try:
        with urllib.request.urlopen(HEALTH, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


class _Api:
    """暴露给页面的桌面原生能力（js_api）。目前只有读剪贴板——
    登录罩的「粘贴」按钮用它，绕开 WebView2 默认不放行网页读剪贴板的限制。
    只读、不写，64 位句柄显式声明（resources.py 截断事故的教训）。"""
    def get_clipboard(self):
        try:
            import ctypes
            from ctypes import wintypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            u.GetClipboardData.restype = wintypes.HANDLE
            k.GlobalLock.restype = ctypes.c_wchar_p
            k.GlobalLock.argtypes = [wintypes.HANDLE]
            k.GlobalUnlock.argtypes = [wintypes.HANDLE]
            if not u.OpenClipboard(None):
                return ""
            try:
                h = u.GetClipboardData(13)   # CF_UNICODETEXT
                if not h:
                    return ""
                text = k.GlobalLock(h) or ""
                k.GlobalUnlock(h)
                return str(text)
            finally:
                u.CloseClipboard()
        except Exception:
            return ""


def ensure_service():
    """服务在 → 直接用；不在 → 拉起并等它健康（最多 40 秒）。"""
    if service_up():
        return True
    print("知识库服务没在运行，正在替你拉起……")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, "-m", "kb.main"], cwd=str(KB_DIR),
                     creationflags=creationflags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        if service_up():
            print("服务就绪。")
            return True
        time.sleep(0.5)
    print("服务 40 秒内没起来——请手动运行 启动服务.py 看报错。")
    return False


def main():
    try:
        import webview
    except ImportError:
        print("缺桌面壳依赖，请先执行：  pip install pywebview")
        sys.exit(1)
    if not ensure_service():
        sys.exit(1)
    ico = HERE / "inception.ico"
    kwargs = dict(width=1180, height=780, min_size=(880, 600),
                  background_color="#0b0b0e", js_api=_Api())
    if ico.exists():
        kwargs["icon"] = str(ico)
    try:
        webview.create_window("INCEPTION", PAGE, **kwargs)
    except TypeError:
        kwargs.pop("icon", None)   # 部分 pywebview 版本不支持窗口图标参数
        webview.create_window("INCEPTION", PAGE, **kwargs)
    webview.start()   # 关窗即退出本进程；知识库服务留着不动


if __name__ == "__main__":
    main()
