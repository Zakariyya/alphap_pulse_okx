#!/usr/bin/env python3
"""AlphaPulse OKX web console with live terminal logs."""

from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from backtest_engine import discover_strategies, run_backtest_from_request, summarize_dataset

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKERS = int(os.getenv("OKX_FUNDING_WORKERS", "20"))

TASKS = {
    "s1": {
        "title": "OKX 新上线公告监控",
        "script": "s1_okx_listing_monitor.py",
        "mode": "long",
        "description": "轮询 OKX 新币/新交易对公告，可选 Claude 分析并推送 TG。",
    },
    "s2": {
        "title": "费率转负 + OI 上涨扫描",
        "script": "s2_okx_oi_funding_rate_scanner.py",
        "mode": "once",
        "description": "快照对比 OKX USDT 永续，发现费率刚转负且 OI 上涨。",
    },
    "s3": {
        "title": "OKX 热度/OI/费率雷达",
        "script": "s3_okx_accumulation_radar.py",
        "mode": "once",
        "description": "扫描 CoinGecko 热度、OKX 放量、OI 异动和负费率追多。",
    },
}


def now_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


def normalize_workers(value) -> int:
    try:
        workers = int(value)
    except (TypeError, ValueError):
        workers = DEFAULT_WORKERS
    return max(1, min(workers, 50))


def build_script_command(task_id: str, funding_workers: int = DEFAULT_WORKERS) -> list[str]:
    task = TASKS[task_id]
    cmd = [sys.executable, "-u", str(BASE_DIR / task["script"])]
    if task_id == "s2":
        cmd.extend(["--funding-workers", str(normalize_workers(funding_workers))])
    return cmd


class LogBus:
    def __init__(self, max_lines: int = 800):
        self._events = deque(maxlen=max_lines)
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._next_id = 0

    def publish(self, task_id: str, line: str, level: str = "info"):
        event = {
            "id": self._next_id,
            "time": now_text(),
            "task": task_id,
            "level": level,
            "line": line.rstrip("\n"),
        }
        self._next_id += 1
        with self._lock:
            self._events.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(event)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            self._subscribers.discard(q)


class TaskManager:
    def __init__(self, log_bus: LogBus):
        self.log_bus = log_bus
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            running = {task_id: proc.poll() is None for task_id, proc in self._processes.items()}
        return {task_id: {"running": running.get(task_id, False), **meta} for task_id, meta in TASKS.items()}

    def start(self, task_id: str, funding_workers: int = DEFAULT_WORKERS) -> tuple[bool, str]:
        if task_id not in TASKS:
            return False, f"未知任务: {task_id}"
        with self._lock:
            old = self._processes.get(task_id)
            if old and old.poll() is None:
                return False, f"{task_id} 正在运行，不重复启动"

        cmd = build_script_command(task_id, funding_workers=funding_workers)
        env = os.environ.copy()
        env.setdefault("UV_LINK_MODE", "copy")
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.log_bus.publish(task_id, f"[控制台] 启动 {TASKS[task_id]['title']}")
        self.log_bus.publish(task_id, f"[控制台] 命令: {' '.join(cmd)}")
        print(f"[{now_text()}] [控制台] 启动 {task_id}: {' '.join(cmd)}", flush=True)

        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._processes[task_id] = proc
        threading.Thread(target=self._pump_output, args=(task_id, proc), daemon=True).start()
        return True, f"已启动 {task_id}"

    def stop(self, task_id: str) -> tuple[bool, str]:
        with self._lock:
            proc = self._processes.get(task_id)
        if not proc or proc.poll() is not None:
            return False, f"{task_id} 未运行"
        self.log_bus.publish(task_id, "[控制台] 收到停止请求，终止进程")
        proc.terminate()
        return True, f"已请求停止 {task_id}"

    def _pump_output(self, task_id: str, proc: subprocess.Popen):
        assert proc.stdout is not None
        prefix = f"[{task_id}]"
        try:
            for line in proc.stdout:
                clean = line.rstrip("\n")
                print(f"[{now_text()}] {prefix} {clean}", flush=True)
                self.log_bus.publish(task_id, clean)
        finally:
            code = proc.wait()
            msg = f"[控制台] 任务结束，退出码={code}"
            print(f"[{now_text()}] {prefix} {msg}", flush=True)
            self.log_bus.publish(task_id, msg, level="warn" if code else "info")


LOG_BUS = LogBus()
TASK_MANAGER = TaskManager(LOG_BUS)
DATASET_SUMMARY_CACHE: dict | None = None
DATASET_SUMMARY_LOADING = False
DATASET_SUMMARY_LOCK = threading.Lock()


def ensure_dataset_summary_async():
    global DATASET_SUMMARY_LOADING
    with DATASET_SUMMARY_LOCK:
        if DATASET_SUMMARY_CACHE is not None or DATASET_SUMMARY_LOADING:
            return
        DATASET_SUMMARY_LOADING = True

    def _worker():
        global DATASET_SUMMARY_CACHE, DATASET_SUMMARY_LOADING
        try:
            summary = summarize_dataset()
            payload = {
                "candles": summary.candles,
                "start": summary.start,
                "end": summary.end,
                "files": summary.files,
            }
            with DATASET_SUMMARY_LOCK:
                DATASET_SUMMARY_CACHE = payload
        finally:
            with DATASET_SUMMARY_LOCK:
                DATASET_SUMMARY_LOADING = False

    threading.Thread(target=_worker, daemon=True).start()


HTML_TEMPLATE_PATH = BASE_DIR / "web" / "index.html"


def load_html_template() -> str:
    if not HTML_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"页面模板不存在: {HTML_TEMPLATE_PATH}")
    return HTML_TEMPLATE_PATH.read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "AlphaPulseWeb/0.1"

    def log_message(self, fmt, *args):
        print(f"[{now_text()}] [web] {fmt % args}", flush=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html()
        elif parsed.path == "/api/status":
            self._send_json({"ok": True, "tasks": TASK_MANAGER.status()})
        elif parsed.path == "/api/backtest/strategies":
            ensure_dataset_summary_async()
            self._send_json({"ok": True, "strategies": self._strategy_payload()})
        elif parsed.path == "/api/backtest/dataset":
            self._send_json({"ok": True, **self._dataset_payload()})
        elif parsed.path == "/api/stream":
            self._stream_logs()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_json()
        if parsed.path == "/api/start":
            ok, message = TASK_MANAGER.start(body.get("task", ""), normalize_workers(body.get("funding_workers")))
            self._send_json({"ok": ok, "message": message})
        elif parsed.path == "/api/stop":
            ok, message = TASK_MANAGER.stop(body.get("task", ""))
            self._send_json({"ok": ok, "message": message})
        elif parsed.path == "/api/backtest/run":
            try:
                result = run_backtest_from_request(body)
                self._send_json({"ok": True, "result": result})
            except Exception as exc:
                self._send_json({"ok": False, "message": str(exc)})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _send_html(self):
        try:
            html = load_html_template()
        except FileNotFoundError as exc:
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            body = str(exc).encode("utf-8")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = html.replace("__TASKS__", json.dumps(TASKS, ensure_ascii=False)).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _strategy_payload(self) -> dict:
        return {
            name: {
                "title": getattr(strategy, "title", name),
                "description": getattr(strategy, "description", ""),
                "default_params": getattr(strategy, "default_params", {}),
            }
            for name, strategy in discover_strategies().items()
        }

    def _dataset_payload(self) -> dict:
        with DATASET_SUMMARY_LOCK:
            cached = DATASET_SUMMARY_CACHE
            loading = DATASET_SUMMARY_LOADING
        return {"ready": cached is not None, "loading": loading, "summary": cached}

    def _stream_logs(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        for event in LOG_BUS.snapshot():
            self._write_sse(event)
        q = LOG_BUS.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    self._write_sse(event)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            LOG_BUS.unsubscribe(q)

    def _write_sse(self, event: dict):
        payload = f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(payload)
        self.wfile.flush()


def parse_args(argv: Optional[list[str]] = None):
    parser = argparse.ArgumentParser(description="AlphaPulse OKX Web Console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-port-fallback", action="store_true", help="端口被占用时直接报错，不自动尝试后续端口")
    return parser.parse_args(argv)


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def pick_server_port(host: str, preferred_port: int, attempts: int = 20) -> int:
    for port in range(preferred_port, preferred_port + max(1, attempts)):
        if port_is_available(host, port):
            return port
    raise OSError(f"端口 {preferred_port}-{preferred_port + attempts - 1} 都不可用")


def main(argv: Optional[list[str]] = None):
    args = parse_args(argv)
    port = args.port if args.no_port_fallback else pick_server_port(args.host, args.port)
    if port != args.port:
        print(f"[{now_text()}] [web] 端口 {args.port} 被占用，自动切换到 {port}", flush=True)
    ensure_dataset_summary_async()
    server = ThreadingHTTPServer((args.host, port), Handler)
    print(f"[{now_text()}] [web] AlphaPulse OKX 工作台启动: http://{args.host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{now_text()}] [web] 收到中断，关闭服务", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
