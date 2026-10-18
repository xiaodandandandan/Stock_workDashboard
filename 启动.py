#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键启动：起服务 + 开浏览器。

双击本文件即可（Windows 若没关联 .py，就用 `python 启动.py`）。

    python 启动.py                 # 默认 127.0.0.1:8765
    python 启动.py --port 9000
    python 启动.py --scan          # 起来后自动跑一次全市场扫描
"""
import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def port_busy(host, port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--scan", action="store_true", help="启动后自动跑一次全市场扫描")
    ap.add_argument("--limit", type=int, default=0, help="配合 --scan：只扫前 N 只")
    ap.add_argument("--no-browser", action="store_true", help="不自动开浏览器")
    a = ap.parse_args()

    url = "http://%s:%d/" % (a.host, a.port)
    if port_busy(a.host, a.port):
        print("端口 %d 已被占用 —— 可能服务已经在跑，直接打开 %s" % (a.port, url))
        if not a.no_browser:
            webbrowser.open(url)
        return

    print("=" * 56)
    print(" 缠论全市场扫描")
    print(" 目录: %s" % HERE)
    print(" 地址: %s" % url)
    print(" 停止: 在本窗口按 Ctrl+C")
    print("=" * 56)

    cmd = [sys.executable, os.path.join(HERE, "server.py"),
           "--port", str(a.port), "--host", a.host]
    proc = subprocess.Popen(cmd, cwd=HERE)

    if a.scan:
        # 服务起在子进程里，扫描也要打到它身上（这样进度能通过 /api/scan/status 看到）
        def kick():
            import json
            import urllib.request
            for _ in range(40):
                if port_busy(a.host, a.port):
                    break
                time.sleep(0.25)
            body = json.dumps({"limit": a.limit}).encode()
            req = urllib.request.Request(url + "api/scan", data=body,
                                         headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=10).read()
                print("已触发全市场扫描（进度见页面右上角）")
            except Exception as e:                               # noqa: BLE001
                print("触发扫描失败：%s" % e)
        threading.Thread(target=kick, daemon=True).start()

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n正在停止…")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("已停止")


if __name__ == "__main__":
    main()
