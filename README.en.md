# AlphaPulse

AlphaPulse is an OKX-focused monitoring and backtesting project, including listing alerts, funding-rate + open-interest scanners, accumulation radar, and strategy backtests.

## 🤖 One-line AI Quick Read
```
Use Python 3.10+, run `UV_LINK_MODE=copy uv sync --dev`, start `uv run python web_console.py --host 127.0.0.1 --port 8787`, use `s1/s2/s3` for listing monitor, funding+OI scan, and radar, and keep backtest timing as signal on previous closed bar and execution on next bar.
```

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

## 🗂️ Dataset Versioning

- `fullDataExtractionForBTC/data/` is ignored by default to keep large files out of Git history.
- Dataset distribution/recovery is done through GitHub Releases.
- `okx_*.json` runtime snapshots are local-only and not committed.

### 📦 Publish & Restore Dataset via GitHub Release

Publisher flow:

```bash
# 1) Package fullDataExtractionForBTC/data (zip + sha256 to dist/dataset/)
scripts/package_data_release.sh v2026.04.29

# 2) Create a new release
gh release create "v2026.04.29" \
  dist/dataset/data-v2026.04.29.zip \
  dist/dataset/data-v2026.04.29.zip.sha256 \
  --repo Zakariyya/alphap_pulse_okx \
  --title "Dataset v2026.04.29" \
  --notes "Dataset package for release v2026.04.29"
```

If the tag already exists:

```bash
gh release upload "v2026.04.29" \
  dist/dataset/data-v2026.04.29.zip \
  dist/dataset/data-v2026.04.29.zip.sha256 \
  --repo Zakariyya/fullDataExtractionForBTC \
  --clobber
```

Consumer flow:

```bash
# 1) Download release assets
gh release download "v2026.04.29" \
  --repo Zakariyya/fullDataExtractionForBTC \
  -D /tmp/btc-dataset

# 2) Verify checksum
cd /tmp/btc-dataset
sha256sum -c data-v2026.04.29.zip.sha256

# 3) Restore into project root (gets ./fullDataExtractionForBTC/data/...)
cd /mnt/d/me/project/AlphaPulse
unzip -o /tmp/btc-dataset/data-v2026.04.29.zip -d fullDataExtractionForBTC
```
