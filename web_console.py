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


HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AlphaPulse OKX 工作台</title>
  <style>
    :root {
      --bg: #eef7f3;
      --bg2: #dbeee8;
      --card: rgba(255, 255, 255, .82);
      --ink: #13231f;
      --muted: #61736e;
      --line: rgba(24, 78, 67, .16);
      --green: #0f8f73;
      --green-dark: #086653;
      --amber: #c98712;
      --terminal: #081712;
      --terminal-2: #0d241d;
      --shadow: 0 24px 70px rgba(21, 75, 62, .16);
      --mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
      --sans: "Aptos", "Segoe UI", "Noto Sans SC", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: var(--sans);
      background:
        radial-gradient(circle at 16% 12%, rgba(15, 143, 115, .22), transparent 30%),
        radial-gradient(circle at 86% 8%, rgba(201, 135, 18, .16), transparent 24%),
        linear-gradient(135deg, var(--bg), var(--bg2));
      min-height: 100vh;
    }
    .shell { width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 24px 0; }
    header {
      display: flex; justify-content: space-between; gap: 18px; align-items: flex-end;
      padding: 22px 24px; border: 1px solid var(--line); border-radius: 28px;
      background: rgba(255,255,255,.58); box-shadow: var(--shadow); backdrop-filter: blur(18px);
    }
    h1 { margin: 0; font-size: clamp(30px, 4vw, 54px); letter-spacing: -.05em; line-height: .95; }
    .subtitle { margin: 12px 0 0; max-width: 760px; color: var(--muted); font-size: 15px; line-height: 1.7; }
    .status-pill { padding: 10px 14px; border-radius: 999px; background: #e7f8f2; color: var(--green-dark); font-weight: 800; border: 1px solid rgba(15,143,115,.2); white-space: nowrap; }
    .grid { display: grid; grid-template-columns: 420px 1fr; gap: 18px; margin-top: 18px; }
    .workspace-column { display: grid; gap: 18px; align-content: start; }
    .left-column { display: grid; gap: 18px; align-content: start; }
    .panel { background: var(--card); border: 1px solid var(--line); border-radius: 26px; box-shadow: var(--shadow); overflow: hidden; }
    .panel-head { padding: 18px 20px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .panel-head h2 { margin: 0; font-size: 18px; letter-spacing: -.02em; }
    .cards { padding: 16px; display: grid; gap: 14px; }
    .task-card { border: 1px solid var(--line); border-radius: 22px; padding: 16px; background: rgba(255,255,255,.72); }
    .task-top { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
    .task-title { font-size: 17px; font-weight: 900; letter-spacing: -.02em; }
    .task-desc { color: var(--muted); line-height: 1.55; margin: 8px 0 14px; font-size: 13px; }
    .badge { font-size: 12px; padding: 5px 9px; border-radius: 999px; background: #edf3f1; color: var(--muted); font-weight: 800; }
    .badge.running { background: #dff8ef; color: var(--green-dark); }
    .actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    button { border: 0; cursor: pointer; border-radius: 14px; padding: 10px 14px; font-weight: 900; font-family: inherit; transition: .18s ease; }
    .primary { background: var(--green); color: white; box-shadow: 0 10px 22px rgba(15,143,115,.24); }
    .primary:hover { background: var(--green-dark); transform: translateY(-1px); }
    .ghost { background: #eef5f2; color: var(--green-dark); border: 1px solid var(--line); }
    .danger { background: #fff1e5; color: #a44b0a; border: 1px solid rgba(164,75,10,.18); }
    label { color: var(--muted); font-size: 12px; font-weight: 800; }
    input { width: 74px; border: 1px solid var(--line); border-radius: 12px; padding: 9px 10px; background: white; font-weight: 800; color: var(--ink); }
    .terminal-wrap { min-height: 360px; display: flex; flex-direction: column; }
    .backtest-output { min-height: 420px; }
    .terminal-toolbar { display: flex; gap: 10px; align-items: center; color: var(--muted); font-size: 12px; font-weight: 800; }
    .terminal {
      height: 340px; margin: 16px; border-radius: 16px; padding: 14px;
      background: linear-gradient(180deg, var(--terminal), var(--terminal-2));
      color: #d5fff3; font-family: var(--mono); font-size: 12px; line-height: 1.65;
      overflow: auto; border: 1px solid rgba(164, 255, 224, .12); box-shadow: inset 0 0 0 1px rgba(255,255,255,.03);
    }
    .backtest-log { height: 92px; margin: 16px 16px 0; }
    .log-line { white-space: pre-wrap; word-break: break-word; }
    .time { color: #79b9a7; }
    .task { color: #ffd37a; font-weight: 800; }
    .warn { color: #ffbd7a; }
    .empty { color: #7ca99c; }
    .hint { padding: 0 18px 16px; color: var(--muted); font-size: 12px; line-height: 1.6; }
    .backtest { padding: 16px; border-top: 1px solid var(--line); display: grid; gap: 12px; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .field { display: grid; gap: 6px; }
    .field input, .field select { width: 100%; border: 1px solid var(--line); border-radius: 12px; padding: 10px 11px; background: white; font-weight: 800; color: var(--ink); font-family: inherit; }
    .wide { grid-column: 1 / -1; }
    .strategy-list { display: grid; gap: 8px; }
    .strategy-row { border: 1px solid var(--line); border-radius: 14px; padding: 10px; background: rgba(255,255,255,.66); }
    .strategy-row.active { border-color: rgba(15,143,115,.6); box-shadow: inset 0 0 0 1px rgba(15,143,115,.28); }
    .strategy-ctl { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    .strategy-ctl label { display: inline-flex; align-items: center; gap: 6px; color: var(--ink); font-size: 12px; font-weight: 800; }
    .weight-input { width: 90px !important; }
    .strategy-name { font-weight: 900; font-size: 13px; }
    .strategy-desc { margin-top: 4px; color: var(--muted); line-height: 1.45; font-size: 12px; }
    .weight-summary { margin-top: 4px; font-size: 12px; font-weight: 800; }
    .weight-summary.ok { color: #0a7e66; }
    .weight-summary.over { color: #b54708; }
    .results { padding: 16px; display: grid; gap: 14px; }
    .metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .metric { border: 1px solid var(--line); border-radius: 14px; padding: 12px; background: rgba(255,255,255,.72); color: var(--ink); }
    .metric-label { color: var(--muted); font-size: 11px; font-weight: 900; }
    .metric-value { margin-top: 6px; font-size: 18px; font-weight: 950; }
    .trade-wrap {
      overflow: auto;
      max-height: 380px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255,255,255,.68);
      scrollbar-gutter: stable both-edges;
    }
    .trade-table { width: 100%; border-collapse: collapse; color: var(--ink); font-family: var(--mono); font-size: 12px; min-width: 1100px; }
    .trade-table th, .trade-table td { border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; white-space: nowrap; }
    .trade-table th { color: var(--muted); background: #f3faf6; font-weight: 900; }
    .chart-title { margin: 2px 0 -4px; color: var(--ink); font-size: 15px; font-weight: 950; }
    .chart { width: 100%; height: 190px; border-radius: 14px; background: linear-gradient(180deg, #ffffff, #f5faf7); border: 1px solid var(--line); }
    .report-wrap { display: grid; gap: 8px; }
    .report-head { display: flex; justify-content: space-between; align-items: center; }
    .report-title { color: var(--ink); font-size: 14px; font-weight: 900; }
    .report-box {
      width: 100%; min-height: 300px; resize: vertical; border: 1px solid var(--line); border-radius: 12px;
      background: #f8fcfa; color: var(--ink); padding: 12px; font-family: var(--mono); font-size: 12px; line-height: 1.6;
    }
    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>AlphaPulse OKX 工作台</h1>
        <p class="subtitle">三个 OKX 监控脚本集中在一个页面执行。后端会把子进程日志同时打印到当前终端，并通过 SSE 同步到右侧实时终端。</p>
      </div>
      <div class="status-pill" id="connState">SSE 连接中</div>
    </header>
    <section class="grid">
      <div class="left-column">
        <aside class="panel">
          <div class="panel-head"><h2>任务控制</h2><button class="ghost" onclick="refreshStatus()">刷新状态</button></div>
          <div class="cards" id="taskCards"></div>
          <p class="hint">提示：s1 是长期公告监控；s2/s3 是单次扫描，适合配合 cron 定时运行。s2 首次运行只建立费率/OI快照。</p>
        </aside>
        <aside class="panel">
          <div class="panel-head"><h2>BTC 策略回测</h2><button class="ghost" onclick="loadBacktestMeta()">刷新数据</button></div>
          <div class="backtest">
            <div class="strategy-list wide" id="strategyList"></div>
            <div class="form-grid">
              <div class="field"><label>开始日期</label><input id="btStart" type="date" value="2026-01-01"></div>
              <div class="field"><label>结束日期</label><input id="btEnd" type="date" value="2026-04-30"></div>
              <div class="field"><label>模拟盘金额(USDT)</label><input id="btCapital" type="number" min="100" step="100" value="10000"></div>
              <div class="field"><label>信号周期(分钟)</label><input id="btSignalMinutes" type="number" min="1" step="1" value="60"></div>
              <div class="field"><label>策略总占比上限</label><input id="btWeight" type="number" min="0.01" max="1" step="0.01" value="1.00"></div>
              <div class="field"><label>手续费率</label><input id="btFee" type="number" min="0" step="0.0001" value="0.0005"></div>
              <div class="field wide"><label>滑点率</label><input id="btSlippage" type="number" min="0" step="0.0001" value="0.0002"></div>
            </div>
            <button class="primary wide" id="runBacktestBtn" onclick="runBacktest()">运行选中策略回测</button>
            <div class="weight-summary ok" id="weightSummary">已选策略占比合计: 0.00 / 上限 1.00</div>
            <p class="hint" id="datasetHint">正在读取本地数据集...</p>
          </div>
        </aside>
      </div>
      <div class="workspace-column">
        <section class="panel terminal-wrap">
          <div class="panel-head">
            <h2>监控终端</h2>
            <div class="terminal-toolbar"><span id="lineCount">0 行</span><button class="ghost" onclick="clearTerminal()">清空监控日志</button></div>
          </div>
          <div class="terminal" id="terminal"><div class="empty">等待监控任务输出...</div></div>
        </section>
        <section class="panel backtest-output">
          <div class="panel-head">
            <h2>回测结果</h2>
            <div class="terminal-toolbar"><span id="backtestLineCount">0 行</span><button class="ghost" onclick="clearBacktestLog()">清空回测日志</button></div>
          </div>
          <div class="terminal backtest-log" id="backtestLog"><div class="empty">等待回测运行...</div></div>
          <div class="results" id="backtestResults"></div>
        </section>
      </div>
    </section>
  </main>
<script>
const tasks = __TASKS__;
let lineCount = 0;
let backtestLineCount = 0;
let selectedStrategies = {};
let strategies = {};
let lastBacktestPayload = null;
const terminal = document.getElementById('terminal');
const backtestLog = document.getElementById('backtestLog');

function renderCards(status = {}) {
  const root = document.getElementById('taskCards');
  root.innerHTML = Object.entries(tasks).map(([id, task]) => {
    const running = status[id]?.running;
    const workerControl = id === 's2' ? `<label>并发量 <input id="workers-${id}" type="number" min="1" max="50" value="20"></label>` : '';
    return `<article class="task-card">
      <div class="task-top"><div class="task-title">${id.toUpperCase()} · ${task.title}</div><span class="badge ${running ? 'running' : ''}">${running ? '运行中' : task.mode === 'long' ? '常驻' : '单次'}</span></div>
      <p class="task-desc">${task.description}</p>
      <div class="actions">
        ${workerControl}
        <button class="primary" onclick="startTask('${id}')">启动</button>
        <button class="danger" onclick="stopTask('${id}')">停止</button>
      </div>
    </article>`;
  }).join('');
}

async function api(path, body) {
  const resp = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const data = await resp.json();
  if (!data.ok) appendLog({time: new Date().toLocaleTimeString(), task: 'web', level: 'warn', line: data.message || '请求失败'});
  return data;
}

async function startTask(id) {
  const input = document.getElementById(`workers-${id}`);
  const funding_workers = input ? Number(input.value || 20) : 20;
  await api('/api/start', { task: id, funding_workers });
  refreshStatus();
}
async function stopTask(id) { await api('/api/stop', { task: id }); refreshStatus(); }
async function refreshStatus() {
  const resp = await fetch('/api/status');
  const data = await resp.json();
  renderCards(data.tasks || {});
}
async function loadBacktestMeta() {
  const root = document.getElementById('strategyList');
  root.innerHTML = '<div class="strategy-desc">正在读取策略列表...</div>';
  document.getElementById('datasetHint').textContent = '数据概要加载中...';
  try {
    const resp = await fetch('/api/backtest/strategies');
    const data = await resp.json();
    if (!data.ok) {
      root.innerHTML = '<div class="strategy-desc">策略列表读取失败，请点击“刷新数据”重试</div>';
      appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'warn', line: data.message || '读取策略失败'});
      return;
    }
    strategies = data.strategies || {};
    if (Object.keys(strategies).length === 0) {
      root.innerHTML = '<div class="strategy-desc">未发现可用策略文件，请检查 strategy 目录</div>';
      updateWeightSummary();
      return;
    }
    if (Object.keys(selectedStrategies).length === 0) {
      const first = Object.keys(strategies)[0];
      if (first) selectedStrategies[first] = { enabled: true, weight: 0.3 };
    }
    for (const id of Object.keys(strategies)) {
      if (!selectedStrategies[id]) selectedStrategies[id] = { enabled: false, weight: 0.2 };
    }
    renderStrategies();
    refreshDatasetHint();
  } catch (_err) {
    root.innerHTML = '<div class="strategy-desc">策略列表读取失败，请点击“刷新数据”重试</div>';
    appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'warn', line: '读取策略列表失败，请检查服务是否正常'});
    updateWeightSummary();
  }
}
async function refreshDatasetHint() {
  try {
    const resp = await fetch('/api/backtest/dataset');
    const data = await resp.json();
    if (!data.ok) return;
    if (data.ready && data.summary) {
      const ds = data.summary;
      document.getElementById('datasetHint').textContent = `数据范围: ${ds.start || '-'} 到 ${ds.end || '-'} | candles: ${ds.candles || 0} | 文件: ${(ds.files || []).length}`;
      return;
    }
    document.getElementById('datasetHint').textContent = data.loading ? '数据概要加载中...' : '数据概要待加载';
    setTimeout(refreshDatasetHint, 1500);
  } catch (_err) {
    document.getElementById('datasetHint').textContent = '数据概要加载失败，可点击“刷新数据”重试';
  }
}
function renderStrategies() {
  const root = document.getElementById('strategyList');
  root.innerHTML = Object.entries(strategies).map(([id, item]) => `
    <div class="strategy-row ${selectedStrategies[id]?.enabled ? 'active' : ''}">
      <div class="strategy-ctl">
        <label><input type="checkbox" ${selectedStrategies[id]?.enabled ? 'checked' : ''} onchange="toggleStrategy('${id}', this.checked)"> ${escapeHtml(item.title || id)}</label>
        <label>占比 <input class="weight-input" type="number" min="0.01" max="1" step="0.01" value="${Number(selectedStrategies[id]?.weight || 0.2).toFixed(2)}" onchange="setStrategyWeight('${id}', this.value)"></label>
      </div>
      <div class="strategy-name">${escapeHtml(item.title || id)}</div>
      <div class="strategy-desc">${escapeHtml(item.description || '')}</div>
    </div>
  `).join('') || '<div class="strategy-desc">未找到 strategy/*.py 策略文件</div>';
  updateWeightSummary();
}
function toggleStrategy(id, enabled) {
  if (!selectedStrategies[id]) selectedStrategies[id] = { enabled: false, weight: 0.2 };
  selectedStrategies[id].enabled = enabled;
  renderStrategies();
}
function setStrategyWeight(id, value) {
  const weight = Math.max(0, Math.min(1, Number(value || 0)));
  if (!selectedStrategies[id]) selectedStrategies[id] = { enabled: false, weight: 0.2 };
  selectedStrategies[id].weight = weight;
  renderStrategies();
}
function selectedWeightTotal() {
  return Object.values(selectedStrategies).filter(item => item.enabled).reduce((sum, item) => sum + Number(item.weight || 0), 0);
}
function updateWeightSummary() {
  const total = selectedWeightTotal();
  const maxWeight = Number(document.getElementById('btWeight')?.value || 1);
  const summary = document.getElementById('weightSummary');
  const runBtn = document.getElementById('runBacktestBtn');
  const over = total > maxWeight;
  const noStrategies = Object.keys(strategies).length === 0;
  const noneSelected = total <= 0;
  if (summary) {
    summary.className = `weight-summary ${over ? 'over' : 'ok'}`;
    summary.textContent = noStrategies
      ? '策略列表为空，请先刷新数据'
      : `已选策略占比合计: ${total.toFixed(2)} / 上限 ${maxWeight.toFixed(2)}${over ? '（已超限）' : ''}`;
  }
  if (runBtn) runBtn.disabled = noStrategies || over || noneSelected;
}
async function runBacktest() {
  if (Object.keys(strategies).length === 0) {
    appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'warn', line: '策略列表未加载成功，请先点“刷新数据”'});
    return;
  }
  const chosen = Object.entries(selectedStrategies)
    .filter(([, item]) => item.enabled)
    .map(([name, item]) => ({ name, weight: Number(item.weight || 0) }));
  if (!chosen.length) {
    appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'warn', line: '请至少勾选一个策略'});
    return;
  }
  const totalWeight = chosen.reduce((sum, item) => sum + item.weight, 0);
  const maxWeight = Number(document.getElementById('btWeight').value || 1);
  if (totalWeight > maxWeight) {
    appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'warn', line: `策略占比合计 ${totalWeight.toFixed(2)} 超过上限 ${maxWeight.toFixed(2)}`});
    return;
  }
  const payload = {
    strategies: chosen,
    start: document.getElementById('btStart').value,
    end: document.getElementById('btEnd').value,
    initial_capital: Number(document.getElementById('btCapital').value || 10000),
    signal_minutes: Number(document.getElementById('btSignalMinutes').value || 60),
    fee_rate: Number(document.getElementById('btFee').value || 0.0005),
    slippage_rate: Number(document.getElementById('btSlippage').value || 0.0002)
  };
  lastBacktestPayload = payload;
  appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'info', line: `开始组合回测 ${chosen.map(item => `${item.name}(${item.weight})`).join(' + ')} ${payload.start} -> ${payload.end}`});
  const resp = await fetch('/api/backtest/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await resp.json();
  if (!data.ok) {
    appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'warn', line: data.message || '回测失败'});
    return;
  }
  renderBacktestResult(data.result);
  appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'info', line: `回测完成: 交易 ${data.result.metrics.trades} 笔，收益 ${data.result.metrics.return_pct}%`});
}
function renderBacktestResult(result) {
  const m = result.metrics || {};
  const isCombined = result.strategy === 'combined';
  const metrics = [
    ['最终权益', money(m.final_equity)], ['净盈亏', money(m.net_pnl)], ['资金费盈亏', money(m.funding_pnl)], ['收益率', `${m.return_pct}%`],
    ['最大回撤', `${m.max_drawdown_pct}%`],
    ['胜率', `${m.win_rate}%`], ['交易数', m.trades], ['Sharpe', m.sharpe], ['Sortino', m.sortino],
    ['Calmar', m.calmar], ['Profit Factor', m.profit_factor], ['期望收益', money(m.expectancy)], ['资金费点数', result.funding_events], ['信号数', result.signals]
  ];
  const rows = (result.trades || []).slice().reverse().map(t => `<tr>${isCombined ? `<td>${escapeHtml(t.strategy || '-')}</td>` : ''}<td>${t.side}</td><td>${t.entry_time}</td><td>${t.exit_time}</td><td>${money(t.entry_notional)}</td><td>${money(t.exit_notional)}</td><td>${money(t.pnl)}</td><td>${money(t.funding_pnl)}</td><td>${money(t.account_equity_after_trade)}</td><td>${t.reason}</td></tr>`).join('');
  const head = `${isCombined ? '<th>策略</th>' : ''}<th>方向</th><th>入场</th><th>出场</th><th>入场金额</th><th>出场金额</th><th>盈亏</th><th>资金费</th><th>账户总额(该笔后)</th><th>原因</th>`;
  const emptyCols = isCombined ? 10 : 9;
  const reportText = buildBacktestReportText(result, lastBacktestPayload || {});
  document.getElementById('backtestResults').innerHTML = `
    <div class="chart-title">账户权益曲线</div>
    <svg class="chart" viewBox="0 0 800 190" preserveAspectRatio="none">${equityPath(result.equity_curve || [])}</svg>
    <div class="metric-grid">${metrics.map(([label, value]) => `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`).join('')}</div>
    <div class="trade-wrap"><table class="trade-table"><thead><tr>${head}</tr></thead><tbody>${rows || `<tr><td colspan="${emptyCols}">没有产生交易</td></tr>`}</tbody></table></div>
    <div class="report-wrap">
      <div class="report-head">
        <div class="report-title">回测文字报告（可复制）</div>
        <button class="ghost" onclick="copyBacktestReport()">复制报告</button>
      </div>
      <textarea id="backtestReportBox" class="report-box" readonly>${escapeHtml(reportText)}</textarea>
    </div>
  `;
}
function equityPath(curve) {
  if (!curve.length) return '<text x="24" y="100" fill="#61736e">暂无账户权益曲线</text>';
  const values = curve.map(row => Number(row.equity));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => {
    const x = curve.length === 1 ? 0 : index / (curve.length - 1) * 800;
    const y = 170 - ((value - min) / span * 145);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<polyline points="${points}" fill="none" stroke="#0f8f73" stroke-width="3" vector-effect="non-scaling-stroke"/>`;
}
function money(value) { return `${Number(value || 0).toLocaleString(undefined, {maximumFractionDigits: 2})} USDT`; }
function clearTerminal() { terminal.innerHTML = '<div class="empty">页面日志已清空，后端终端日志不受影响。</div>'; lineCount = 0; updateLineCount(); }
function clearBacktestLog() { backtestLog.innerHTML = '<div class="empty">回测日志已清空，回测结果不受影响。</div>'; backtestLineCount = 0; updateBacktestLineCount(); }
function updateLineCount() { document.getElementById('lineCount').textContent = `${lineCount} 行`; }
function updateBacktestLineCount() { document.getElementById('backtestLineCount').textContent = `${backtestLineCount} 行`; }
function appendLog(event) {
  if (terminal.querySelector('.empty')) terminal.innerHTML = '';
  const div = document.createElement('div');
  div.className = `log-line ${event.level || ''}`;
  div.innerHTML = `<span class="time">${event.time}</span> <span class="task">[${event.task}]</span> ${escapeHtml(event.line || '')}`;
  terminal.appendChild(div);
  lineCount += 1;
  updateLineCount();
  terminal.scrollTop = terminal.scrollHeight;
}
function appendBacktestLog(event) {
  if (backtestLog.querySelector('.empty')) backtestLog.innerHTML = '';
  const div = document.createElement('div');
  div.className = `log-line ${event.level || ''}`;
  div.innerHTML = `<span class="time">${event.time}</span> <span class="task">[${event.task}]</span> ${escapeHtml(event.line || '')}`;
  backtestLog.appendChild(div);
  backtestLineCount += 1;
  updateBacktestLineCount();
  backtestLog.scrollTop = backtestLog.scrollHeight;
}
function escapeHtml(text) { return text.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
function copyBacktestReport() {
  const box = document.getElementById('backtestReportBox');
  if (!box) return;
  box.select();
  box.setSelectionRange(0, 999999);
  try {
    document.execCommand('copy');
    appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'info', line: '已复制回测文字报告'});
  } catch (_err) {
    appendBacktestLog({time: new Date().toLocaleTimeString(), task: 'backtest', level: 'warn', line: '复制失败，请手动全选复制'});
  }
}
function buildBacktestReportText(result, payload) {
  const m = result.metrics || {};
  const selected = (payload.strategies || []).map(item => `${item.name}:${Number(item.weight).toFixed(2)}`).join(' | ') || (result.strategy || '');
  const lines = [];
  lines.push('【回测报告】');
  lines.push(`策略占比: ${selected}`);
  lines.push(`回测范围: ${payload.start || '-'} -> ${payload.end || '-'}`);
  lines.push(`最终权益: ${money(m.final_equity)}`);
  lines.push(`净盈亏: ${money(m.net_pnl)}`);
  lines.push(`资金费盈亏: ${money(m.funding_pnl)}`);
  lines.push(`收益率: ${m.return_pct}%`);
  lines.push(`最大回撤: ${m.max_drawdown_pct}%`);
  lines.push(`胜率: ${m.win_rate}%`);
  lines.push(`交易数: ${m.trades}`);
  lines.push(`Sharpe: ${m.sharpe}`);
  lines.push(`Sortino: ${m.sortino}`);
  lines.push(`Calmar: ${m.calmar}`);
  lines.push(`Profit Factor: ${m.profit_factor}`);
  lines.push(`期望收益: ${money(m.expectancy)}`);
  lines.push(`资金费点数: ${result.funding_events}`);
  lines.push(`信号数: ${result.signals}`);
  lines.push('');
  lines.push('【策略详细逻辑】');
  const selectedStrategiesForReport = (payload.strategies || []).length
    ? payload.strategies
    : [{ name: result.strategy, weight: 1 }];
  for (const row of selectedStrategiesForReport) {
    const info = strategies[row.name] || {};
    const params = info.default_params || {};
    const paramText = Object.keys(params).length
      ? Object.entries(params).map(([k, v]) => `${k}=${v}`).join(', ')
      : '无';
    const logicDetail = strategyLogicDetail(row.name, params);
    lines.push(`- 策略: ${row.name}`);
    lines.push(`  标题: ${info.title || row.name}`);
    lines.push(`  占比: ${Number(row.weight || 0).toFixed(2)}`);
    lines.push(`  逻辑: ${info.description || '未提供描述'}`);
    lines.push(`  关键参数: ${paramText}`);
    lines.push(`  规则明细: ${logicDetail}`);
  }
  lines.push('');
  lines.push('【每笔交易明细】');
  lines.push('策略\t方向\t入场\t出场\t入场金额\t出场金额\t盈亏\t资金费\t账户总额(该笔后)\t原因');
  for (const trade of result.trades || []) {
    lines.push([
      trade.strategy || result.strategy || '-',
      trade.side || '-',
      trade.entry_time || '-',
      trade.exit_time || '-',
      money(trade.entry_notional),
      money(trade.exit_notional),
      money(trade.pnl),
      money(trade.funding_pnl),
      money(trade.account_equity_after_trade),
      trade.reason || '-',
    ].join('\t'));
  }
  return lines.join('\n');
}
function strategyLogicDetail(name, p) {
  const get = (k, d) => (p && p[k] !== undefined ? p[k] : d);
  const docs = {
    volatility_breakout: () => {
      const cw = get('compress_window', 24);
      const pl = get('percentile_lookback', 120);
      const q = get('compress_quantile', 0.2);
      const bw = get('breakout_window', 20);
      const vw = get('volume_window', 20);
      const vm = get('volume_multiplier', 1.05);
      const sl = get('sl_atr', 1.2);
      const tp = get('tp_atr', 2.4);
      return `1) 压缩判定: width_t=(DonchianHigh_${cw}-DonchianLow_${cw})/Close_t，若 width_{t-1} <= Quantile(width, ${pl}, ${q}) 视为压缩。2) 成交量过滤: Volume_t >= MA(Volume,${vw})*${vm}。3) 突破入场: Close_t > max(High,t-${bw}..t-1) 做多；Close_t < min(Low,t-${bw}..t-1) 做空。4) 风控: ATR%=ATR_${cw}/Close_t，止损=entry*(1-sl_side*max(0.004,ATR%*${sl}))，止盈=entry*(1+tp_side*max(0.008,ATR%*${tp}))。5) 平仓: 触及止损/止盈或信号反向。`;
    },
    false_break_reversal: () => {
      const cw = get('compress_window', 24);
      const pl = get('percentile_lookback', 120);
      const q = get('compress_quantile', 0.25);
      const rw = get('range_window', 18);
      const rb = get('reclaim_bars', 3);
      const sl = get('sl_atr', 0.9);
      const tp = get('tp_atr', 1.6);
      return `1) 压缩判定同上: width_{t-1} <= Quantile(width, ${pl}, ${q})。2) 区间定义: upper=max(High,t-${rw}..t-1), lower=min(Low,t-${rw}..t-1)。3) 假突破识别: 最近${rb}根内若有 High>upper 且当下 Close_t<upper => 做空；若有 Low<lower 且 Close_t>lower => 做多。4) 风控: ATR%=ATR_${cw}/Close_t，止损比例=max(0.0035,ATR%*${sl})，止盈比例=max(0.006,ATR%*${tp})。5) 平仓: 触发止损/止盈或信号反向。`;
    },
    state_machine_spring: () => {
      const cw = get('compress_window', 24);
      const pl = get('percentile_lookback', 120);
      const q = get('compress_quantile', 0.25);
      const bw = get('breakout_window', 20);
      const vw = get('volume_window', 20);
      const vm = get('volume_multiplier', 1.0);
      const sl = get('sl_atr', 1.1);
      const tp = get('tp_atr', 2.0);
      return `状态机: idle->compressed->pre_release->cooldown。1) idle->compressed: width_{t-1} <= Quantile(width,${pl},${q})。2) compressed->pre_release: width_t > width_{t-1}*1.15。3) pre_release 触发: Volume_t >= MA(Volume,${vw})*${vm} 且 Close_t>upper(${bw}) 做多，Close_t<lower(${bw}) 做空。4) 风控: ATR%=ATR_${cw}/Close_t，止损=max(0.004,ATR%*${sl})，止盈=max(0.008,ATR%*${tp})。5) 平仓: 止损/止盈/反向信号。`;
    },
    zscore_mean_reversion: () => {
      const cw = get('compress_window', 24);
      const pl = get('percentile_lookback', 120);
      const q = get('compress_quantile', 0.3);
      const zw = get('z_window', 48);
      const ze = get('z_entry', 1.8);
      const sl = get('sl_atr', 1.0);
      const tp = get('tp_atr', 1.4);
      return `1) 压缩过滤: width_{t-1} <= Quantile(width,${pl},${q})。2) 统计量: mean_t=MA(Close,${zw}), std_t=STD(Close,${zw}), z_t=(Close_t-mean_t)/std_t。3) 入场: 若 z_{t-1}<-${ze} 且 z_t>z_{t-1} => 做多（超跌回归）；若 z_{t-1}>${ze} 且 z_t<z_{t-1} => 做空（超涨回归）。4) 风控: ATR%=ATR_${cw}/Close_t，止损=max(0.004,ATR%*${sl})，止盈=max(0.006,ATR%*${tp})。5) 平仓: 止损/止盈/反向信号。`;
    },
    small_barbell: () => {
      const mw = get('ma_window', 48);
      const sl = get('sl_pct', 0.03);
      const tp = get('tp_pct', 0.06);
      return `1) 趋势过滤: ma_t=MA(Close,${mw})。2) 入场: 当 Close_t > ma_{t-1} 且当前空仓 => 做多。3) 退出信号: Close_t < ma_{t-1} 触发平仓信号。4) 风控: 固定止损=${sl}，固定止盈=${tp}。5) 平仓: 止损/止盈/退出信号。`;
    },
    fixed_barbell: () => {
      const sl = get('sl_pct', 0.06);
      const tp = get('tp_pct', 0.12);
      return `1) 建仓: 第2根bar固定做多一次。2) 不做主动择时加减仓。3) 风控: 固定止损=${sl}，固定止盈=${tp}。4) 平仓: 止损/止盈或回测结束强平。`;
    },
    defensive_barbell: () => {
      const mw = get('ma_window', 72);
      const aw = get('atr_window', 24);
      const al = get('atr_limit', 0.012);
      const sl = get('sl_pct', 0.025);
      const tp = get('tp_pct', 0.05);
      return `1) 双过滤: 趋势过滤 Close_t>MA(Close,${mw})；波动过滤 ATR_${aw}/Close_t <= ${al}。2) 入场: 满足双过滤且空仓 => 做多。3) 退出: Close_t<MA 或 ATR/Close 超阈值 => 发平仓信号。4) 风控: 固定止损=${sl}，固定止盈=${tp}。5) 平仓: 止损/止盈/退出信号。`;
    },
  };
  const builder = docs[name];
  if (builder) return builder();
  return '未定义该策略的细化规则，请检查策略实现。';
}

function connectLogs() {
  const state = document.getElementById('connState');
  const es = new EventSource('/api/stream');
  es.onopen = () => { state.textContent = 'SSE 已连接'; };
  es.onerror = () => { state.textContent = 'SSE 重连中'; };
  es.onmessage = (msg) => appendLog(JSON.parse(msg.data));
}
renderCards({});
refreshStatus();
loadBacktestMeta();
connectLogs();
document.getElementById('btWeight').addEventListener('input', updateWeightSummary);
</script>
</body>
</html>'''


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
        body = HTML.replace("__TASKS__", json.dumps(TASKS, ensure_ascii=False)).encode("utf-8")
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
