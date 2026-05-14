import os
import time
import hmac
import hashlib
import threading
import math
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BINGX_API_KEY", "")
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

BASE_URL = "https://open-api.bingx.com"
SYMBOL = "BTC-USDT"

LEVERAGE = 30
CAPITAL_UNITS = 4
MAX_USED_UNITS = 3

FIRST_TARGET = 0.20
MERGED_TP = 0.05
MERGED_SL = 0.20

QTY_STEP = 0.0001
MIN_QTY = 0.0001

AUTO_ENABLED = True
LOOP_SECONDS = 30


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
    headers = {"X-BX-APIKEY": API_KEY}

    if method == "GET":
        response = requests.get(url, headers=headers, timeout=20)
    elif method == "POST":
        response = requests.post(url, headers=headers, timeout=20)
    elif method == "DELETE":
        response = requests.delete(url, headers=headers, timeout=20)
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


def floor_qty(qty):
    return math.floor(qty / QTY_STEP) * QTY_STEP


def get_price():
    result = bingx_get("/openApi/swap/v2/quote/price", {
        "symbol": SYMBOL
    })

    try:
        return float(result["data"]["price"])
    except Exception:
        return 0


def find_number(obj, keys):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                try:
                    return float(v)
                except Exception:
                    pass

        for v in obj.values():
            found = find_number(v, keys)
            if found is not None:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = find_number(item, keys)
            if found is not None:
                return found

    return None


def get_available_usdt():
    result = bingx_get("/openApi/swap/v2/user/balance")

    available = find_number(result, [
        "availableMargin",
        "availableBalance",
        "available",
        "balance"
    ])

    if available is None:
        return 0, result

    return available, result


def calculate_unit_qty():
    price = get_price()
    available_usdt, balance_result = get_available_usdt()

    if price <= 0 or available_usdt <= 0:
        return {
            "ok": False,
            "price": price,
            "available_usdt": available_usdt,
            "balance_result": balance_result,
            "qty": 0
        }

    unit_margin = available_usdt / CAPITAL_UNITS
    notional = unit_margin * LEVERAGE
    raw_qty = notional / price
    qty = floor_qty(raw_qty)

    if qty < MIN_QTY:
        qty = MIN_QTY

    return {
        "ok": True,
        "price": price,
        "available_usdt": available_usdt,
        "unit_margin": unit_margin,
        "qty": round(qty, 4)
    }


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
            cancelled.append(cancel_order(order_id))
            time.sleep(0.2)

    return {
        "count": len(cancelled)
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


def has_short_protection():
    orders = extract_orders(get_open_orders())

    tp = False
    sl = False

    for o in orders:
        if o.get("positionSide") == "SHORT" and o.get("side") == "BUY":
            if o.get("type") == "LIMIT":
                tp = True
            if "STOP" in str(o.get("type")):
                sl = True

    return tp and sl


def has_long_protection():
    orders = extract_orders(get_open_orders())

    tp = False
    sl = False

    for o in orders:
        if o.get("positionSide") == "LONG" and o.get("side") == "SELL":
            if o.get("type") == "LIMIT":
                tp = True
            if "STOP" in str(o.get("type")):
                sl = True

    return tp and sl


def phase1_core():
    qty_info = calculate_unit_qty()

    if not qty_info["ok"]:
        return {
            "ok": False,
            "error": "無法計算單位下單數量",
            "qty_info": qty_info
        }

    unit_qty = qty_info["qty"]

    set_leverage("BUY")
    set_leverage("SELL")

    long_order = market_order("BUY", "LONG", unit_qty)
    short_order = market_order("SELL", "SHORT", unit_qty)

    time.sleep(1)

    positions_result = get_positions()
    pos = parse_positions(positions_result)

    long_avg = pos["long_avg"]
    short_avg = pos["short_avg"]

    if long_avg <= 0 or short_avg <= 0:
        return {
            "ok": False,
            "error": "無法取得多空均價",
            "unit_qty": unit_qty
        }

    move = FIRST_TARGET / LEVERAGE

    upper_price = long_avg * (1 + move)
    lower_price = short_avg * (1 - move)

    close_long_limit = limit_order("SELL", "LONG", upper_price, unit_qty)
    add_short_limit = limit_order("SELL", "SHORT", upper_price, unit_qty)

    close_short_limit = limit_order("BUY", "SHORT", lower_price, unit_qty)
    add_long_limit = limit_order("BUY", "LONG", lower_price, unit_qty)

    return {
        "ok": True,
        "stage": "phase1",
        "capital_units": CAPITAL_UNITS,
        "max_used_units": MAX_USED_UNITS,
        "unit_qty": unit_qty,
        "unit_margin": qty_info["unit_margin"],
        "available_usdt": qty_info["available_usdt"],
        "price": qty_info["price"],
        "upper_price": round(upper_price, 1),
        "lower_price": round(lower_price, 1),
        "long_order": long_order,
        "short_order": short_order,
        "close_long_limit": close_long_limit,
        "add_short_limit": add_short_limit,
        "close_short_limit": close_short_limit,
        "add_long_limit": add_long_limit
    }


def monitor_core():
    positions_result = get_positions()
    pos = parse_positions(positions_result)

    long_qty = pos["long_qty"]
    short_qty = pos["short_qty"]
    long_avg = pos["long_avg"]
    short_avg = pos["short_avg"]

    actions = []

    if short_qty > 0 and long_qty == 0:
        if not has_short_protection():
            cancel_all_open_orders()

            tp_price = short_avg * (1 - MERGED_TP / LEVERAGE)
            sl_price = short_avg * (1 + MERGED_SL / LEVERAGE)

            limit_order("BUY", "SHORT", tp_price, short_qty)
            stop_market_order("BUY", "SHORT", sl_price, short_qty)

            actions.append("short_protection_set")

    elif long_qty > 0 and short_qty == 0:
        if not has_long_protection():
            cancel_all_open_orders()

            tp_price = long_avg * (1 + MERGED_TP / LEVERAGE)
            sl_price = long_avg * (1 - MERGED_SL / LEVERAGE)

            limit_order("SELL", "LONG", tp_price, long_qty)
            stop_market_order("SELL", "LONG", sl_price, long_qty)

            actions.append("long_protection_set")

    elif long_qty == 0 and short_qty == 0:
        open_orders = extract_orders(get_open_orders())

        if len(open_orders) == 0:
            phase1_core()
            actions.append("auto_restart_phase1")

    return {
        "ok": True,
        "stage": "monitor",
        "long_qty": long_qty,
        "short_qty": short_qty,
        "actions_count": len(actions)
    }


def auto_loop():
    time.sleep(10)

    while True:
        try:
            if AUTO_ENABLED and API_KEY and SECRET_KEY:
                result = monitor_core()
                print("AUTO_MONITOR:", result)

        except Exception as e:
            print("AUTO_ERROR:", str(e))

        time.sleep(LOOP_SECONDS)


threading.Thread(
    target=auto_loop,
    daemon=True
).start()


@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "msg": "auto cycle running",
        "auto_enabled": AUTO_ENABLED,
        "loop_seconds": LOOP_SECONDS
    })


@app.route("/test")
def test():
    qty_info = calculate_unit_qty()

    return jsonify({
        "API_KEY_EXISTS": bool(API_KEY),
        "SECRET_KEY_EXISTS": bool(SECRET_KEY),
        "auto_enabled": AUTO_ENABLED,
        "capital_units": CAPITAL_UNITS,
        "max_used_units": MAX_USED_UNITS,
        "qty_info": qty_info
    })


@app.route("/phase1")
def phase1():
    return jsonify(phase1_core())


@app.route("/monitor")
def monitor():
    return jsonify(monitor_core())


@app.route("/status")
def status():
    positions_result = get_positions()
    orders_result = get_open_orders()
@app.route("/pause")
def pause():
    global AUTO_ENABLED

    AUTO_ENABLED = False

    return jsonify({
        "ok": True,
        "auto_enabled": AUTO_ENABLED,
        "msg": "AUTO TRADING PAUSED"
    })


@app.route("/resume")
def resume():
    global AUTO_ENABLED

    AUTO_ENABLED = True

    return jsonify({
        "ok": True,
        "auto_enabled": AUTO_ENABLED,
        "msg": "AUTO TRADING RESUMED"
    })
    return jsonify({
        "ok": True,
        "auto_enabled": AUTO_ENABLED,
        "positions": positions_result,
        "open_orders": orders_result
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
