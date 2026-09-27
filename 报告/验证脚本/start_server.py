# -*- coding: utf-8 -*-
"""临时启动知识库服务用于界面验收：等健康检查通过后退出，服务进程稍后按 PID 关闭。"""
import subprocess
import sys
import tempfile
import time
import urllib.request

WS = tempfile.gettempdir()   # 日志写系统临时目录（公开版脱敏：原为开发机内部路径）
log = open(WS + r"\server-preview.log", "w", encoding="utf-8")
p = subprocess.Popen([sys.executable, "-m", "kb.main"], cwd=r"D:\cangku\知识库",
                     stdout=log, stderr=subprocess.STDOUT)
with open(WS + r"\server-preview.pid", "w") as f:
    f.write(str(p.pid))
for _ in range(80):
    try:
        urllib.request.urlopen("http://127.0.0.1:8320/health", timeout=1)
        print("SERVER_UP pid={}".format(p.pid))
        break
    except Exception:
        time.sleep(0.5)
else:
    print("SERVER_FAILED")
    sys.exit(1)
