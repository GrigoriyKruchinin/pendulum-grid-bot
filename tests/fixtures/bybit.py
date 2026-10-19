"""Trimmed real Bybit V5 payloads (2026-10) for offline tests."""

from typing import Any

INSTRUMENTS: dict[str, Any] = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "category": "spot",
        "list": [
            {
                "symbol": "SOLUSDT",
                "baseCoin": "SOL",
                "quoteCoin": "USDT",
                "innovation": "0",
                "status": "Trading",
                "stTag": "0",
                "lotSizeFilter": {"basePrecision": "0.0001", "minOrderQty": "0.0001", "minOrderAmt": "5"},
                "priceFilter": {"tickSize": "0.01"},
            },
            {
                "symbol": "MEMEUSDT",
                "baseCoin": "MEME",
                "quoteCoin": "USDT",
                "innovation": "1",
                "status": "Trading",
                "stTag": "0",
                "lotSizeFilter": {"basePrecision": "1", "minOrderQty": "1", "minOrderAmt": "1"},
                "priceFilter": {"tickSize": "0.000001"},
            },
            {
                "symbol": "OLDUSDT",
                "baseCoin": "OLD",
                "quoteCoin": "USDT",
                "status": "Closed",
                "lotSizeFilter": {"basePrecision": "1", "minOrderQty": "1", "minOrderAmt": "1"},
                "priceFilter": {"tickSize": "0.01"},
            },
        ],
    },
}

KLINES: dict[str, Any] = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "list": [
            ["1792428300000", "104.5", "104.53", "104.46", "104.47", "587.8492", "61427.893921"],
            ["1792428240000", "104.54", "104.54", "104.5", "104.5", "530.3978", "55439.174878"],
            ["1792428180000", "104.58", "104.6", "104.53", "104.54", "192.6518", "20145.265863"],
        ]
    },
}

TICKERS: dict[str, Any] = {
    "retCode": 0,
    "result": {
        "list": [
            {
                "symbol": "SOLUSDT",
                "bid1Price": "104.46",
                "ask1Price": "104.47",
                "lastPrice": "104.47",
                "turnover24h": "250000000.5",
                "volume24h": "2400000",
            },
            {
                "symbol": "UNKNOWNUSDT",
                "bid1Price": "1",
                "ask1Price": "1",
                "lastPrice": "1",
                "turnover24h": "1",
            },
        ]
    },
}

FEE_RATE: dict[str, Any] = {
    "retCode": 0,
    "result": {"list": [{"symbol": "SOLUSDT", "makerFeeRate": "0.001", "takerFeeRate": "0.001"}]},
}

RATE_LIMITED: dict[str, Any] = {"retCode": 10006, "retMsg": "Too many visits!", "result": {}}
