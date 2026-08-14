#!/usr/bin/env python3
"""测试用假后端：模拟 ipc 的 health 响应，可控崩溃。

仅供 supervisor 单测使用，不进打包（build_app.sh 已排除 tests 目录）。
"""
import argparse
import os
import socket
import sys
import threading
import time


def serve(port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(4)
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        try:
            conn.settimeout(1.0)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            resp = '{"ok": true, "data": {"status": "up"}}'
            conn.sendall((resp + "\n").encode())
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--crash-first", type=int, default=0)  # 前 N 次启动：监听后退出（模拟运行中崩溃）
    ap.add_argument("--state", default="")                  # 跨进程启动计数文件
    ap.add_argument("--mode", default="ok")                 # ok | import_error（启动即崩）
    args = ap.parse_args()

    if args.mode == "import_error":
        sys.stderr.write(
            "Traceback (most recent call last):\n"
            "  File 'fake', in <module>\n"
            "ModuleNotFoundError: No module named 'llama_cpp'\n")
        sys.stderr.flush()
        sys.exit(1)

    count = 0
    if args.state and os.path.exists(args.state):
        try:
            count = int(open(args.state).read().strip() or "0")
        except (OSError, ValueError):
            count = 0
    count += 1
    if args.state:
        try:
            open(args.state, "w").write(str(count))
        except OSError:
            pass

    threading.Thread(target=serve, args=(args.port,), daemon=True).start()

    if args.crash_first and count <= args.crash_first:
        time.sleep(0.2)
        sys.stderr.write(f"[fake] 第 {count} 次启动：模拟运行中崩溃\n")
        sys.stderr.flush()
        sys.exit(1)

    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    main()
