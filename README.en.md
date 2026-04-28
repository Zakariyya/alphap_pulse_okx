# AlphaPulse

AlphaPulse is an OKX-focused monitoring and backtesting project, including listing alerts, funding-rate + open-interest scanners, accumulation radar, and strategy backtests.

## 🤖 One-line AI Quick Read
Use Python 3.10+, run `UV_LINK_MODE=copy uv sync --dev`, start `uv run python web_console.py --host 127.0.0.1 --port 8787`, use `s1/s2/s3` for listing monitor, funding+OI scan, and radar, and keep backtest timing as signal on previous closed bar and execution on next bar.

## 🚀 Quick Start

```bash
UV_LINK_MODE=copy uv sync --dev
uv run python web_console.py --host 127.0.0.1 --port 8787
```

Open: `http://127.0.0.1:8787`

## 🧰 Requirements

- Python 3.10+
- `uv` (recommended)

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc
uv --version
```

## 📌 Main Modules

- `s1_okx_listing_monitor.py`: OKX listing/news monitor with optional AI analysis and Telegram push.
- `s2_okx_oi_funding_rate_scanner.py`: Finds cases where funding flips positive->negative with rising OI.
- `s3_okx_accumulation_radar.py`: Hourly radar combining attention, volume, OI, and funding signals.
- `backtest_engine.py` + `strategy/*`: Backtesting framework and multiple strategy implementations.
- `web_console.py`: Web console with one-click execution and SSE live logs.

## ⚙️ Config

Uses `.env.okx` first (compatible with `.env.oi`):

```bash
TG_BOT_TOKEN=your_telegram_bot_token
TG_CHAT_ID=your_telegram_chat_id
ANTHROPIC_API_KEY=your_ai_key  # optional for s1
```

## 🏃 Common Commands

```bash
uv run python s1_okx_listing_monitor.py
uv run python s2_okx_oi_funding_rate_scanner.py --funding-workers 20
uv run python s3_okx_accumulation_radar.py
uv run python -m pytest -q
```

## ⏱️ Backtest Timing Rule (Important)

- Indicators must use only the previous closed bar.
- Signals are generated on `bar[t-1]` and executed on `bar[t]`.
- Never use an unfinished bar for decisions.
