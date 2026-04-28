from s2_okx_oi_funding_rate_scanner import collect_funding_rates


def test_collect_funding_rates_returns_symbol_to_rate_map():
    tickers = [{"symbol": "BTC-USDT-SWAP"}, {"symbol": "ETH-USDT-SWAP"}]

    def fake_getter(symbol):
        return {"BTC-USDT-SWAP": -0.01, "ETH-USDT-SWAP": 0.02}[symbol]

    assert collect_funding_rates(tickers, rate_getter=fake_getter, max_workers=2) == {
        "BTC-USDT-SWAP": -0.01,
        "ETH-USDT-SWAP": 0.02,
    }

from s2_okx_oi_funding_rate_scanner import parse_args


def test_parse_args_accepts_funding_workers():
    args = parse_args(["--funding-workers", "7"])
    assert args.funding_workers == 7
