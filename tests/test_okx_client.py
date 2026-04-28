from okx_client import (
    extract_okx_symbol,
    normalize_ticker,
    oi_usd,
    parse_okx_announcement_items,
)


def test_extract_okx_symbol_from_new_listing_title():
    assert extract_okx_symbol("OKX will launch CHIP/USD and CHIP/EUR for spot trading") == "CHIP"
    assert extract_okx_symbol("OKX to list Foo Token (FOO) spot trading") == "FOO"


def test_normalize_swap_ticker_uses_okx_fields():
    raw = {
        "instId": "BTC-USDT-SWAP",
        "last": "76813.9",
        "open24h": "76000",
        "volCcy24h": "123.45",
        "vol24h": "999",
    }
    normalized = normalize_ticker(raw)
    assert normalized["symbol"] == "BTC-USDT-SWAP"
    assert normalized["coin"] == "BTC"
    assert normalized["price"] == 76813.9
    assert round(normalized["price_chg_24h"], 4) == round((76813.9 - 76000) / 76000 * 100, 4)
    assert normalized["volume_usd"] == 123.45 * 76813.9


def test_oi_usd_prefers_okx_oiusd_field():
    assert oi_usd({"oiUsd": "2506640596.12", "oiCcy": "32783"}, price=76000) == 2506640596.12
    assert oi_usd({"oiCcy": "2"}, price=100) == 200


def test_parse_okx_announcement_items_flattens_details():
    payload = {
        "data": [
            {"details": [
                {"title": "OKX will launch CHIP/USD for spot trading", "pTime": "1777028400000", "url": "https://example.com/a"}
            ]}
        ]
    }
    items = parse_okx_announcement_items(payload)
    assert items == [{
        "title": "OKX will launch CHIP/USD for spot trading",
        "published_ms": 1777028400000,
        "url": "https://example.com/a",
    }]

from okx_client import open_interest_map


def test_open_interest_map_converts_okx_rows_by_inst_id():
    rows = [
        {"instId": "BTC-USDT-SWAP", "oiUsd": "2500", "oiCcy": "2"},
        {"instId": "ETH-USDT-SWAP", "oiCcy": "3"},
        {"instId": "BAD-USDT-SWAP"},
    ]
    result = open_interest_map(rows, {"ETH-USDT-SWAP": 100.0})
    assert result == {
        "BTC-USDT-SWAP": 2500.0,
        "ETH-USDT-SWAP": 300.0,
        "BAD-USDT-SWAP": 0.0,
    }
