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

MERGED_TP = 0.05
MERGED_SL = -0.50


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
    return requests.post(url, headers=headers).json()


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
    return requests.get(url, headers=headers).json()


def set_leverage(side):
    return bingx_post("/openApi/swap/v2/trade/leverage", {
        "symbol": SYMBOL,
        "side": side,
        "leverage": LEVERAGE
    })


def market_order(side, position_side, quantity):
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity
    })


def limit_order(side, position_side, price, quantity):
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "LIMIT",
        "price": round(price, 1),
        "quantity": quantity,
        "timeInForce": "GTC"
    })


def get_positions():
    return bingx_get("/openApi/swap/v2/user/positions", {
        "symbol": SYMBOL
    })


def get_avg_price(order_result):
    try:
        return float(order_result["data"]["order"]["avgPrice"])
    except Exception:
        return None


def parse_positions(result):
    positions = result.get("data", [])
    long_pos = None
    short_pos = None

    for p in positions:
        if p.get("symbol") == SYMBOL and p.get("positionSide") == "LONG":
            long_pos = p
        if p.get("symbol") == SYMBOL and p.get("positionSide") == "SHORT":
            short_pos = p

    return long_pos, short_pos


def pos_qty(pos):
    if not pos:
        return 0
    return abs(float(pos.get("positionAmt", 0)))


def pos_pnl_rate(pos):
    if not pos:
        return 0
    unrealized = float(pos.get("unrealizedProfit", 0))
    margin = float(pos.get("initialMargin", 0))
    if margin == 0:
        return 0
    return unrealized / margin


def pos_avg(pos):
    return float(pos.get("avgPrice", 0))


@app.route("/")
def home():
    return "ok"


@app.route("/phase1")
def phase1():
    if not API_KEY or not SECRET_KEY:
        return jsonify({"ok": False, "error": "缺少 API KEY"})

    long_lev = set_leverage("BUY")
    short_lev = set_leverage("SELL")

    long_order = market_order("BUY", "LONG", QUANTITY)
    short_order = market_order("SELL", "SHORT", QUANTITY)

    long_avg = get_avg_price(long_order)
    short_avg = get_avg_price(short_order)

    if not long_avg or not short_avg:
        return jsonify({
            "ok": False,
            "error": "無法取得成交均價",
            "long_order": long_order,
            "short_order": short_order
        })

    upper_price = long_avg * (1 + PRICE_MOVE)
    lower_price = short_avg * (1 - PRICE_MOVE)

    close_long_limit = limit_order("SELL", "LONG", upper_price, QUANTITY)
    add_short_limit = limit_order("SELL", "SHORT", upper_price, QUANTITY)

    close_short_limit = limit_order("BUY", "SHORT", lower_price, QUANTITY)
    add_long_limit = limit_order("BUY", "LONG", lower_price, QUANTITY)

    return jsonify({
        "ok": True,
        "stage": "phase1",
        "long_avg": long_avg,
        "short_avg": short_avg,
        "upper_price": round(upper_price, 1),
        "lower_price": round(lower_price, 1),
        "long_leverage": long_lev,
        "short_leverage": short_lev,
        "long_order": long_order,
        "short_order": short_order,
        "close_long_limit": close_long_limit,
        "add_short_limit": add_short_limit,
        "close_short_limit": close_short_limit,
        "add_long_limit": add_long_limit
    })


@app.route("/monitor")
def monitor():
    positions_result = get_positions()
    long_pos, short_pos = parse_positions(positions_result)

    long_qty = pos_qty(long_pos)
    short_qty = pos_qty(short_pos)

    actions = []

    if long_qty >= QUANTITY * 2:
        pnl = pos_pnl_rate(long_pos)
        avg = pos_avg(long_pos)

        tp_price = avg * (1 + MERGED_TP / LEVERAGE)

        if pnl <= MERGED_SL:
            actions.append({
                "type": "market_stop_long",
                "result": market_order("SELL", "LONG", long_qty)
            })
        else:
            actions.append({
                "type": "set_long_merged_tp_5_percent",
                "price": round(tp_price, 1),
                "result": limit_order("SELL", "LONG", tp_price, long_qty)
            })

    if short_qty >= QUANTITY * 2:
        pnl = pos_pnl_rate(short_pos)
        avg = pos_avg(short_pos)

        tp_price = avg * (1 - MERGED_TP / LEVERAGE)

        if pnl <= MERGED_SL:
            actions.append({
                "type": "market_stop_short",
                "result": market_order("BUY", "SHORT", short_qty)
            })
        else:
            actions.append({
                "type": "set_short_merged_tp_5_percent",
                "price": round(tp_price, 1),
                "result": limit_order("BUY", "SHORT", tp_price, short_qty)
            })

    return jsonify({
        "ok": True,
        "stage": "monitor_phase6",
        "long_qty": long_qty,
        "short_qty": short_qty,
        "actions": actions,
        "positions": positions_result
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
