# AlphaPulse

面向 OKX 的监控 + 回测项目，覆盖公告监控、资金费率与持仓异动扫描、热度雷达，以及基于历史数据的策略回测验证。

## 🤖 给 AI 的一句话
```请在 Python 3.10+ 环境使用 `UV_LINK_MODE=copy uv sync --dev` 安装依赖，然后通过 `uv run python web_console.py --host 127.0.0.1 --port 8787` 启动工作台，按 `s1/s2/s3` 分别执行公告监控、资金费率+OI 扫描和热度雷达，并遵循“信号用上一根已收盘 K 线、下一根执行”的回测时序规则。```

## 🚀 快速开始

```bash
UV_LINK_MODE=copy uv sync --dev
uv run python web_console.py --host 127.0.0.1 --port 8787
```

浏览器打开：`http://127.0.0.1:8787`

## 🧰 环境要求

- Python 3.10+
- `uv`（推荐）

如果本机没有 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc
uv --version
```

## 📌 核心能力

- `s1_okx_listing_monitor.py`：监控 OKX 新币/新交易对公告，可选 AI 分析与 Telegram 推送。
- `s2_okx_oi_funding_rate_scanner.py`：扫描 USDT 永续，识别“费率由正转负且 OI 相比上次上涨”的机会。
- `s3_okx_accumulation_radar.py`：每小时雷达，结合热度、成交放量、OI 与费率信号。
- `backtest_engine.py` + `strategy/*`：策略回测与多种杠铃/反转/突破模型。
- `web_console.py`：Web 工作台，一键运行脚本并通过 SSE 实时查看日志。

## ⚙️ 配置

优先读取 `.env.okx`（兼容 `.env.oi`）：

```bash
TG_BOT_TOKEN=你的Telegram机器人Token
TG_CHAT_ID=你的Telegram Chat ID
ANTHROPIC_API_KEY=你的AI Key  # s1 可选，不配置则用规则分析
```

## 🏃 常用命令

运行公告监控（长期运行）：

```bash
uv run python s1_okx_listing_monitor.py
```

运行资金费率/OI 扫描：

```bash
uv run python s2_okx_oi_funding_rate_scanner.py --funding-workers 20
```

运行热度雷达：

```bash
uv run python s3_okx_accumulation_radar.py
```

运行测试：

```bash
uv run python -m pytest -q
```

## ⏱️ 回测规则（重要）

- 指标计算只使用上一根已收盘 K 线。
- 在 `bar[t-1]` 生成信号，在 `bar[t]` 执行。
- 不使用当前未收盘 K 线做决策。

## 🗂️ 数据与版本管理说明

- `fullDataExtractionForBTC/` 是重要数据集，默认应纳入版本管理。
- `okx_*.json` 为本地运行快照，默认不提交。

## 🌍 英文文档

- [README.en.md](./README.en.md)
