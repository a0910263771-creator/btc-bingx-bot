import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# =========================
# Render 環境變數，不寫真實金鑰
# =========================
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


# =========================
# BingX 簽名
# =========================
def make_query(params):
    return "&".join([f"{k}={params[k]}" for k in sorted(params)])


def sign_query(query):
    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def bingx_post(path, params):
    params["timestamp"] = int(time.time() * 1000)

    query = make_query(params)
    signature = sign_query(query)

    url = BASE_URL + path + "?" + query + "&signature=" + signature

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    response = requests.post(url, headers=headers)

    try:
        return response.json()
    except Exception:
        return {
            "code": response.status_code,
            "text": response.text
        }


def bingx_get(path, params=None):
    if params is None:
        params = {}

    params["timestamp"] = int(time.time() * 1000)

    query = make_query(params)
    signature = sign_query(query)

    url = BASE_URL + path + "?" + query + "&signature=" + signature

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    response = requests.get(url, headers=headers)

    try:
        return response.json()
    except Exception:
        return {
            "code": response.status_code,
            "text": response.text
        }


# =========================
# 基本交易函式
# =========================
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


def get_positions():
    return bingx_get("/openApi/swap/v2/user/positions", {
        "symbol": SYMBOL
    })


def cancel_all_orders():
    return bingx_post("/openApi/swap/v2/trade/allOpenOrders", {
        "symbol": SYMBOL
    })


# =========================
# 解析持倉
# =========================
def parse_positions(result):
    long_qty = 0
    short_qty = 0
    long_avg = 0
    short_avg = 0
    long_pnl = 0
    short_pnl = 0

    data = result.get("data", [])

    for p in data:
        if p.get("positionSide") == "LONG":
            long_qty = abs(float(p.get("positionAmt", 0)))
            long_avg = float(p.get("avgPrice", 0))
            long_pnl = float(p.get("unrealizedProfit", 0))

        if p.get("positionSide") == "SHORT":
            short_qty = abs(float(p.get("positionAmt", 0)))
            short_avg = float(p.get("avgPrice", 0))
            short_pnl = float(p.get("unrealizedProfit", 0))

    return {
        "long_qty": long_qty,
        "short_qty": short_qty,
        "long_avg": long_avg,
        "short_avg": short_avg,
        "long_pnl": long_pnl,
        "short_pnl": short_pnl
    }


# =========================
# 首頁
# =========================
@app.route("/")
def home():
    return jsonify({
        "ok": True,
        "msg": "BTC-USDT phase1 20% cycle bot"
    })


# =========================
# 檢查 API KEY
# =========================
@app.route("/test")
def test():
    return jsonify({
        "API_KEY_EXISTS": bool(API_KEY),
        "SECRET_KEY_EXISTS": bool(SECRET_KEY)
    })


# =========================
# Phase1：雙向開倉 + 掛 20%
# =========================
@app.route("/phase1")
def phase1():
    if not API_KEY or not SECRET_KEY:
        return jsonify({
            "ok": False,
            "error": "缺少 BINGX_API_KEY 或 BINGX_SECRET_KEY"
        })

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
        return jsonify({
            "ok": False,
            "error": "無法取得多空均價",
            "long_order": long_order,
            "short_order": short_order,
            "positions": positions_result
        })

    move = FIRST_TARGET / LEVERAGE

    upper_price = long_avg * (1 + move)
    lower_price = short_avg * (1 - move)

    close_long_limit = limit_order(
        "SELL",
        "LONG",
        upper_price,
        FIRST_QTY
    )

    add_short_limit = limit_order(
        "SELL",
        "SHORT",
        upper_price,
        ADD_QTY
    )

    close_short_limit = limit_order(
        "BUY",
        "SHORT",
        lower_price,
        FIRST_QTY
    )

    add_long_limit = limit_order(
        "BUY",
        "LONG",
        lower_price,
        ADD_QTY
    )

    return jsonify({
        "ok": True,
        "stage": "phase1",
        "symbol": SYMBOL,
        "leverage": LEVERAGE,
        "first_qty": FIRST_QTY,
        "add_qty": ADD_QTY,
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
    })


# =========================
# Monitor：成交後撤單 + 掛 5% TP + 20% 市價停損
# =========================
@app.route("/monitor")
def monitor():
    positions_result = get_positions()
    pos = parse_positions(positions_result)

    long_qty = pos["long_qty"]
    short_qty = pos["short_qty"]
    long_avg = pos["long_avg"]
    short_avg = pos["short_avg"]

    actions = []

    # 空單加碼完成：空單 >= 0.002
    if short_qty >= FIRST_QTY + ADD_QTY:
        cancel_result = cancel_all_orders()

        tp_price = short_avg * (1 - MERGED_TP / LEVERAGE)
        sl_trigger_price = short_avg * (1 + MERGED_SL / LEVERAGE)

        tp_order = limit_order(
            "BUY",
            "SHORT",
            tp_price,
            short_qty
        )

        actions.append({
            "mode": "short_merged",
            "cancel_all_orders": cancel_result,
            "tp_type": "LIMIT_CLOSE_SHORT",
            "tp_price": round(tp_price, 1),
            "sl_trigger_price_note": round(sl_trigger_price, 1),
            "sl_note": "目前此版先回傳停損觸發價，實際市價停損由 /risk_stop 監控執行",
            "tp_order": tp_order
        })

    # 多單加碼完成：多單 >= 0.002
    if long_qty >= FIRST_QTY + ADD_QTY:
        cancel_result = cancel_all_orders()

        tp_price = long_avg * (1 + MERGED_TP / LEVERAGE)
        sl_trigger_price = long_avg * (1 - MERGED_SL / LEVERAGE)

        tp_order = limit_order(
            "SELL",
            "LONG",
            tp_price,
            long_qty
        )

        actions.append({
            "mode": "long_merged",
            "cancel_all_orders": cancel_result,
            "tp_type": "LIMIT_CLOSE_LONG",
            "tp_price": round(tp_price, 1),
            "sl_trigger_price_note": round(sl_trigger_price, 1),
            "sl_note": "目前此版先回傳停損觸發價，實際市價停損由 /risk_stop 監控執行",
            "tp_order": tp_order
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


# =========================
# Risk stop：手動觸發檢查 -20% 市價停損
# =========================
@app.route("/risk_stop")
def risk_stop():
    positions_result = get_positions()
    pos = parse_positions(positions_result)

    long_qty = pos["long_qty"]
    short_qty = pos["short_qty"]
    long_avg = pos["long_avg"]
    short_avg = pos["short_avg"]

    actions = []

    ticker = bingx_get("/openApi/swap/v2/quote/price", {
        "symbol": SYMBOL
    })

    try:
        price = float(ticker["data"]["price"])
    except Exception:
        return jsonify({
            "ok": False,
            "error": "無法取得現價",
            "ticker": ticker
        })

    if long_qty > 0 and long_avg > 0:
        long_sl_price = long_avg * (1 - MERGED_SL / LEVERAGE)

        if price <= long_sl_price:
            result = market_order("SELL", "LONG", long_qty)
            actions.append({
                "type": "STOP_LONG_MARKET",
                "trigger_price": round(long_sl_price, 1),
                "current_price": price,
                "result": result
            })

    if short_qty > 0 and short_avg > 0:
        short_sl_price = short_avg * (1 + MERGED_SL / LEVERAGE)

        if price >= short_sl_price:
            result = market_order("BUY", "SHORT", short_qty)
            actions.append({
                "type": "STOP_SHORT_MARKET",
                "trigger_price": round(short_sl_price, 1),
                "current_price": price,
                "result": result
            })

    return jsonify({
        "ok": True,
        "stage": "risk_stop",
        "current_price": price,
        "long_qty": long_qty,
        "short_qty": short_qty,
        "actions": actions
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
