# -*- coding: utf-8 -*-
"""验收：桌面壳 js_api 剪贴板通道。设哨兵文本→开窗→点粘贴按钮→读输入框值→比对。"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, r"D:\cangku\AI\inception")
import webview  # noqa: E402
from app import _Api  # noqa: E402

SENT = "CLIPTEST-sentinel-密钥9"

# 1) 把哨兵文本写进 Windows 剪贴板
u, k = ctypes.windll.user32, ctypes.windll.kernel32
u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
u.SetClipboardData.restype = wintypes.HANDLE
u.OpenClipboard.argtypes = [wintypes.HWND]
u.OpenClipboard(None)
u.EmptyClipboard()
buf = ctypes.create_unicode_buffer(SENT)
k.GlobalAlloc.restype = wintypes.HANDLE
h = k.GlobalAlloc(0x0042, (len(SENT) + 1) * 2)   # GMEM_MOVEABLE|GMEM_ZEROINIT
k.GlobalLock.restype = ctypes.c_void_p
k.GlobalLock.argtypes = [wintypes.HANDLE]
p = k.GlobalLock(h)
ctypes.memmove(p, buf, (len(SENT) + 1) * 2)
k.GlobalUnlock.argtypes = [wintypes.HANDLE]
k.GlobalUnlock(h)
u.SetClipboardData(13, h)   # CF_UNICODETEXT；此后句柄归系统
u.CloseClipboard()

win = webview.create_window("clip-test", "http://127.0.0.1:8320/static/inception.html",
                            js_api=_Api())


def on_loaded():
    def work():
        time.sleep(1.2)
        win.evaluate_js("pasteKey()")
        time.sleep(1.5)
        got = win.evaluate_js("document.getElementById('lockKey').value")
        msg = win.evaluate_js("document.getElementById('lockMsg').textContent")
        print("FIELD=" + repr(got))
        print("MSG=" + repr(msg))
        print("MATCH=" + str(got == SENT))
        win.destroy()
    threading.Thread(target=work, daemon=True).start()


win.events.loaded += on_loaded
webview.start()
