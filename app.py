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
FIRST_QTY = 0.001
ADD_QTY = 0.001

FIRST_TARGET = 0.20
MERGED_TP = 0.05
MERGED_SL = 0.20

AUTO_RESTART = True


def make_query(params):
    return "&".join([f"{k}={params[k]}" for k in sorted(params)])


def sign_query(query):
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def bingx_request(method, path, params=None):
    if params is None:
        params = {}

    params["timestamp"] = int(time.time() * 1000)

    query = make_query(params)
    signature = sign_query(query)

    url = BASE_URL + path + "?" + query + "&signature=" + signature

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    if method == "GET":
        response = requests.get(url, headers=headers)
    elif method == "POST":
        response = requests.post(url, headers=headers)
    elif method == "DELETE":
        response = requests.delete(url, headers=headers)
    else:
        return {"ok": False, "error": "method error"}

    try:
        return response.json()
    except Exception:
        return {"status_code": response.status_code, "text": response.text}


def bingx_get(path, params=None):
    return bingx_request("GET", path, params)


def bingx_post(path, params=None):
    return bingx_request("POST", path, params)


def bingx_delete(path, params=None):
    return bingx_request("DELETE", path, params)


def set_leverage(side):
    return bingx_post("/openApi/swap/v2/trade/leverage", {
        "symbol": SYMBOL,
        "side": side,
        "leverage": LEVERAGE
    })


def market_order(side, position_side, qty):
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": qty
    })


def limit_order(side, position_side, price, qty):
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "LIMIT",
        "price": round(price, 1),
        "quantity": qty,
        "timeInForce": "GTC"
    })


def stop_market_order(side, position_side, stop_price, qty):
    return bingx_post("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "STOP_MARKET",
        "stopPrice": round(stop_price, 1),
        "quantity": qty,
        "workingType": "MARK_PRICE"
    })


def get_positions():
    return bingx_get("/openApi/swap/v2/user/positions", {
        "symbol": SYMBOL
    })


def get_open_orders():
    return bingx_get("/openApi/swap/v2/trade/openOrders", {
        "symbol": SYMBOL
    })


def cancel_order(order_id):
    return bingx_delete("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL,
        "orderId": order_id
    })


def extract_orders(open_orders_result):
    data = open_orders_result.get("data", [])

    if isinstance(data, dict):
        if "orders" in data:
            return data["orders"]
        if "order" in data:
            return data["order"]

    if isinstance(data, list):
        return data

    return []


def cancel_all_open_orders():
    result = get_open_orders()
    orders = extract_orders(result)

    cancelled = []

    for o in orders:
        order_id = o.get("orderId")

        if order_id:
            cancelled.append({
                "orderId": order_id,
                "result": cancel_order(order_id)
            })

            time.sleep(0.2)

    return {
        "open_orders_before": result,
        "cancelled": cancelled
    }


def parse_positions(result):
    long_qty = 0
    short_qty = 0
    long_avg = 0
    short_avg = 0

    for p in result.get("data", []):
        if p.get("positionSide") == "LONG":
            long_qty = abs(float(p.get("positionAmt", 0)))
            long_avg = float(p.get("avgPrice", 0))

        if p.get("positionSide") == "SHORT":
            short_qty = abs(float(p.get("positionAmt", 0)))
            short_avg = float(p.get("avgPrice", 0))

    return {
        "long_qty": long_qty,
        "short_qty": short_qty,
        "long_avg": long_avg,
        "short_avg": short_avg
    }


def phase1_core():
    long_leverage = set_leverage("BUY")
    short_leverage = set_leverage("SELL")

    long_order = market_order("BUY", "LONG", FIRST_QTY)
    short_order = market_order("SELL", "SHORT", FIRST_QTY)

    time.sleep(1)

    positions_result = get_positions()
    pos = parse_positions(positions_result)

    long_avg = pos["long_avg"]
    short_avg = pos["short_avg"]

    if long_avg <= 0 or short_avg <= 0:
        return {
            "ok": False,
            "error": "無法取得多空均價",
            "long_order": long_order,
            "short_order": short_order,
            "positions": positions_result
        }

    move = FIRST_TARGET / LEVERAGE

    upper_price = long_avg * (1 + move)
    lower_price = short_avg * (1 - move)

    close_long_limit = limit_order("SELL", "LONG", upper_price, FIRST_QTY)
    add_short_limit = limit_order("SELL", "SHORT", upper_price, ADD_QTY)

    close_short_limit = limit_order("BUY", "SHORT", lower_price, FIRST_QTY)
    add_long_limit = limit_order("BUY", "LONG", lower_price, ADD_QTY)

    return {
        "ok": True,
        "stage": "phase1",
        "symbol": SYMBOL,
        "leverage": LEVERAGE,
        "long_avg": long_avg,
        "short_avg": short_avg,
        "upper_price": round(upper_price, 1),
        "lower_price": round(lower_price, 1),
        "long_leverage": long_leverage,
        "short_leverage": short_leverage,
        "long_order": long_order,
        "short_order": short_order,
        "close_long_limit": close_long_limit,
        "add_short_limit": add_short_limit,
        "close_short_limit": close_short_limit,
        "add_long_limit": add_long_limit
    }


@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "msg": "BTC-USDT 20% phase1 cycle bot"
    })


@app.route("/test")
def test():
    return jsonify({
        "API_KEY_EXISTS": bool(API_KEY),
        "SECRET_KEY_EXISTS": bool(SECRET_KEY)
    })


@app.route("/phase1")
def phase1():
    if not API_KEY or not SECRET_KEY:
        return jsonify({
            "ok": False,
            "error": "缺少 BINGX_API_KEY 或 BINGX_SECRET_KEY"
        })

    return jsonify(phase1_core())


@app.route("/monitor")
def monitor():
    positions_result = get_positions()
    pos = parse_positions(positions_result)

    long_qty = pos["long_qty"]
    short_qty = pos["short_qty"]
    long_avg = pos["long_avg"]
    short_avg = pos["short_avg"]

    actions = []

    # 空單並單完成
    if short_qty >= FIRST_QTY + ADD_QTY:
        cancel_result = cancel_all_open_orders()

        tp_price = short_avg * (1 - MERGED_TP / LEVERAGE)
        sl_price = short_avg * (1 + MERGED_SL / LEVERAGE)

        tp_order = limit_order("BUY", "SHORT", tp_price, short_qty)
        sl_order = stop_market_order("BUY", "SHORT", sl_price, short_qty)

        actions.append({
            "mode": "short_merged",
            "cancel_all_open_orders": cancel_result,
            "tp_price": round(tp_price, 1),
            "sl_price": round(sl_price, 1),
            "tp_order": tp_order,
            "sl_order": sl_order
        })

    # 多單並單完成
    elif long_qty >= FIRST_QTY + ADD_QTY:
        cancel_result = cancel_all_open_orders()

        tp_price = long_avg * (1 + MERGED_TP / LEVERAGE)
        sl_price = long_avg * (1 - MERGED_SL / LEVERAGE)

        tp_order = limit_order("SELL", "LONG", tp_price, long_qty)
        sl_order = stop_market_order("SELL", "LONG", sl_price, long_qty)

        actions.append({
            "mode": "long_merged",
            "cancel_all_open_orders": cancel_result,
            "tp_price": round(tp_price, 1),
            "sl_price": round(sl_price, 1),
            "tp_order": tp_order,
            "sl_order": sl_order
        })

    # 倉位歸零後，自動下一輪
    elif long_qty == 0 and short_qty == 0 and AUTO_RESTART:
        cancel_result = cancel_all_open_orders()
        restart_result = phase1_core()

        actions.append({
            "mode": "auto_restart_phase1",
            "cancel_all_open_orders": cancel_result,
            "restart_result": restart_result
        })

    return jsonify({
        "ok": True,
        "stage": "monitor",
        "long_qty": long_qty,
        "short_qty": short_qty,
        "long_avg": long_avg,
        "short_avg": short_avg,
        "actions": actions,
        "positions": positions_result
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
