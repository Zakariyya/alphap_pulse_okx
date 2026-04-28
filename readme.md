# AlphaPulse OKX 监控脚本

三个脚本已彻底切换为 OKX 数据源，只监控 OKX 公开市场和 OKX 公告。

## 本项目快速启动
```
UV_LINK_MODE=copy uv sync --dev
uv run python web_console.py --host 127.0.0.1 --port 8787
```


## 脚本

- `s1_okx_listing_monitor.py`：监控 OKX 新币/新交易对公告，可选 Claude 分析质量，Telegram 实时推送。
- `s2_okx_oi_funding_rate_scanner.py`：快照对比 OKX USDT 永续，费率刚从正变负且 OI 较上次运行上涨时推送。
- `s3_okx_accumulation_radar.py`：每小时扫描 OKX USDT 永续，追踪 CoinGecko 热度、OKX 放量、OI 异动、负费率追多信号。

## 配置

优先读取 `.env.okx`，兼容旧的 `.env.oi`：

```bash
TG_BOT_TOKEN=你的Telegram机器人Token
TG_CHAT_ID=你的Telegram Chat ID
ANTHROPIC_API_KEY=你的Claude Key  # 仅 s1 可选，不配则规则分析
```

## uv 安装依赖

本项目推荐用 `uv`，不要直接 `pip install` 到用户级 Python，避免污染 `~/.local` 里的其他工具依赖。

```bash
cd /mnt/d/me/project/AlphaPulse
UV_LINK_MODE=copy uv sync --dev
```

如果本机没有 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc
uv --version
```

## 运行

公告监控长期运行：

```bash
uv run python s1_okx_listing_monitor.py
```

费率/OI 扫描，建议每 5 分钟运行一次：

```bash
uv run python s2_okx_oi_funding_rate_scanner.py
```

热度雷达，建议每小时运行一次：

```bash
uv run python s3_okx_accumulation_radar.py
```

测试：

```bash
uv run python -m pytest -q
```

## 定时任务示例

```cron
*/5 * * * * cd /mnt/d/me/project/AlphaPulse && /usr/bin/env UV_LINK_MODE=copy /home/anan/.local/bin/uv run python s2_okx_oi_funding_rate_scanner.py >> s2_okx.log 2>&1
0 * * * * cd /mnt/d/me/project/AlphaPulse && /usr/bin/env UV_LINK_MODE=copy /home/anan/.local/bin/uv run python s3_okx_accumulation_radar.py >> s3_okx.log 2>&1
```

## 注意

- OKX OI 公共接口提供当前 OI；脚本通过本地快照文件计算“较上次运行变化”。首次运行只会建立基线。
- 原交易所专属的 Alpha 和站内热度概念已移除，避免混用不同市场口径。
- 这些脚本是提醒器，不是自动交易系统。

## s2 性能参数

`s2_okx_oi_funding_rate_scanner.py` 默认并发读取 OKX funding rate：

```bash
uv run python s2_okx_oi_funding_rate_scanner.py --funding-workers 20
```

如果网络不稳定或 OKX 返回较多连接错误，可以临时降到 `8` 或 `10`。

## Web 工作台

启动页面：

```bash
uv run python web_console.py --host 127.0.0.1 --port 8787
```

打开：

```text
http://127.0.0.1:8787
```

页面能力：

- 一键启动 `s1/s2/s3`
- `s2` 可在页面输入 funding 并发量
- 后端会把脚本输出同时打印到当前终端
- 页面右侧通过 SSE 显示实时终端日志，固定窗口、内部滚动、自动滚到底部
- 支持停止长驻任务，例如 `s1` 公告监控

如果 `8787` 被占用，Web 工作台会自动尝试后续端口，并在终端打印实际访问地址。也可以手动指定：

```bash
uv run python web_console.py --host 127.0.0.1 --port 8788
```
