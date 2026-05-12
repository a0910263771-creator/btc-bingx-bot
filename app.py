import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BINGX_API_KEY", "")
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

BASE_URL = "https://open-api.bingx.com"

SYMBOL = "BTC-USDT"
LEVERAGE = 30
QUANTITY = 0.001

PNL_TARGET = 0.50
PRICE_MOVE = PNL_TARGET / LEVERAGE


def bingx_post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])

    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    url = BASE_URL + path + "?" + query + "&signature=" + signature
    headers = {"X-BX-APIKEY": API_KEY}

    res = requests.post(url, headers=headers)
    return res.json()


def bingx_get(path, params=None):
    if params is None:
        params = {}

    params["timestamp"] = int(time.time() * 1000)
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])

    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    url = BASE_URL + path + "?" + query + "&signature=" + signature
    headers = {"X-BX-APIKEY": API_KEY}

    res = requests.get(url, headers=headers)
    return res.json()


def set_leverage_long():
    return bingx_post("/openApi/swap/v2/trade/leverage", {
        "symbol": SYMBOL,
        "side": "BUY",
        "leverage": LEVERAGE
    })


def set_leverage_short():
    return bingx_post("/openApi/swap/v2/trade/leverage", {
        "symbol": SYMBOL,
        "side": "SELL",
        "leverage": LEVERAGE
    })


def open_long_market():
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": "BUY",
        "positionSide": "LONG",
        "type": "MARKET",
        "quantity": QUANTITY
    })


def open_short_market():
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": "SELL",
        "positionSide": "SHORT",
        "type": "MARKET",
        "quantity": QUANTITY
    })


def limit_order(side, position_side, price):
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "LIMIT",
        "price": round(price, 1),
        "quantity": QUANTITY,
        "timeInForce": "GTC"
    })


def get_avg_price(order_result):
    try:
        return float(order_result["data"]["order"]["avgPrice"])
    except Exception:
        return None


@app.route("/")
def home():
    return "ok"


@app.route("/phase1")
def phase1():
    if not API_KEY or not SECRET_KEY:
        return jsonify({
            "ok": False,
            "error": "缺少 BINGX_API_KEY 或 BINGX_SECRET_KEY"
        })

    long_leverage = set_leverage_long()
    short_leverage = set_leverage_short()

    long_order = open_long_market()
    short_order = open_short_market()

    long_avg = get_avg_price(long_order)
    short_avg = get_avg_price(short_order)

    if not long_avg or not short_avg:
        return jsonify({
            "ok": False,
            "error": "無法取得成交均價",
            "long_order": long_order,
            "short_order": short_order
        })

    long_tp_price = long_avg * (1 + PRICE_MOVE)
    short_tp_price = short_avg * (1 - PRICE_MOVE)

    close_long_limit = limit_order(
        side="SELL",
        position_side="LONG",
        price=long_tp_price
    )

    add_short_limit = limit_order(
        side="SELL",
        position_side="SHORT",
        price=long_tp_price
    )

    close_short_limit = limit_order(
        side="BUY",
        position_side="SHORT",
        price=short_tp_price
    )

    add_long_limit = limit_order(
        side="BUY",
        position_side="LONG",
        price=short_tp_price
    )

    return jsonify({
        "ok": True,
        "strategy": "第一階段雙向循環",
        "symbol": SYMBOL,
        "leverage": LEVERAGE,
        "quantity": QUANTITY,
        "long_avg": long_avg,
        "short_avg": short_avg,
        "long_tp_price": round(long_tp_price, 1),
        "short_tp_price": round(short_tp_price, 1),
        "long_leverage": long_leverage,
        "short_leverage": short_leverage,
        "long_order": long_order,
        "short_order": short_order,
        "close_long_limit": close_long_limit,
        "add_short_limit": add_short_limit,
        "close_short_limit": close_short_limit,
        "add_long_limit": add_long_limit
    })


@app.route("/check_key")
def check_key():
    return jsonify({
        "API_KEY_EXISTS": bool(API_KEY),
        "API_KEY_HEAD": API_KEY[:5] if API_KEY else "NONE",
        "SECRET_KEY_EXISTS": bool(SECRET_KEY),
        "SECRET_KEY_HEAD": SECRET_KEY[:5] if SECRET_KEY else "NONE"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
