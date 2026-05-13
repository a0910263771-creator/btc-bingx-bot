from flask import Flask
import requests
import time
import hmac
import hashlib
from urllib.parse import urlencode

app = Flask(__name__)

# =========================================
# BingX API
# =========================================

API_KEY = "你的API_KEY"
SECRET_KEY = "你的SECRET_KEY"

BASE_URL = "https://open-api.bingx.com"

SYMBOL = "BTC-USDT"

LEVERAGE = 30

FIRST_QTY = 0.001
ADD_QTY = 0.001

# =========================================
# 工具
# =========================================

def sign_params(params):
    query = urlencode(sorted(params.items()))
    return hmac.new(
        SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()

# =========================================
# POST
# =========================================

def bingx_post(path, params):

    params["timestamp"] = int(time.time() * 1000)

    signature = sign_params(params)

    params["signature"] = signature

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    url = BASE_URL + path

    response = requests.post(
        url,
        params=params,
        headers=headers
    )

    return response.json()

# =========================================
# GET
# =========================================

def bingx_get(path, params=None):

    if params is None:
        params = {}

    params["timestamp"] = int(time.time() * 1000)

    signature = sign_params(params)

    params["signature"] = signature

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    url = BASE_URL + path

    response = requests.get(
        url,
        params=params,
        headers=headers
    )

    return response.json()

# =========================================
# 市價多
# =========================================

def market_long(qty):

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": "BUY",
        "positionSide": "LONG",
        "type": "MARKET",
        "quantity": qty
    }

    return bingx_post(path, params)

# =========================================
# 市價空
# =========================================

def market_short(qty):

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": "SELL",
        "positionSide": "SHORT",
        "type": "MARKET",
        "quantity": qty
    }

    return bingx_post(path, params)

# =========================================
# 限價平多
# =========================================

def close_long_limit(price, qty):

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": "SELL",
        "positionSide": "LONG",
        "type": "LIMIT",
        "price": price,
        "quantity": qty
    }

    return bingx_post(path, params)

# =========================================
# 限價平空
# =========================================

def close_short_limit(price, qty):

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": "BUY",
        "positionSide": "SHORT",
        "type": "LIMIT",
        "price": price,
        "quantity": qty
    }

    return bingx_post(path, params)

# =========================================
# 撤銷全部委託
# =========================================

def cancel_all():

    path = "/openApi/swap/v2/trade/allOpenOrders"

    params = {
        "symbol": SYMBOL
    }

    return bingx_post(path, params)

# =========================================
# 查持倉
# =========================================

def get_positions():

    path = "/openApi/swap/v2/user/positions"

    params = {
        "symbol": SYMBOL
    }

    return bingx_get(path, params)

# =========================================
# phase1
# =========================================

@app.route("/phase1")

def phase1():

    # 開雙向

    long_order = market_long(FIRST_QTY)

    short_order = market_short(FIRST_QTY)

    time.sleep(2)

    positions = get_positions()

    data = positions["data"]

    long_avg = 0
    short_avg = 0

    for p in data:

        if p["positionSide"] == "LONG":
            long_avg = float(p["avgPrice"])

        if p["positionSide"] == "SHORT":
            short_avg = float(p["avgPrice"])

    # LONG +20%
    long_tp = round(long_avg * 1.2, 1)

    # SHORT -20%
    short_add = round(short_avg * 1.2, 1)

    close_long = close_long_limit(long_tp, FIRST_QTY)

    add_short = close_short_limit(short_add, ADD_QTY)

    return {
        "stage": "phase1",
        "long_avg": long_avg,
        "short_avg": short_avg,
        "long_tp": long_tp,
        "short_add": short_add,
        "close_long": close_long,
        "add_short": add_short
    }

# =========================================
# monitor
# =========================================

@app.route("/monitor")

def monitor():

    positions = get_positions()

    data = positions["data"]

    long_qty = 0
    short_qty = 0

    long_avg = 0
    short_avg = 0

    for p in data:

        if p["positionSide"] == "LONG":

            long_qty = float(p["positionAmt"])
            long_avg = float(p["avgPrice"])

        if p["positionSide"] == "SHORT":

            short_qty = float(p["positionAmt"])
            short_avg = float(p["avgPrice"])

    actions = []

    # =====================================
    # SHORT 並單完成
    # =====================================

    if short_qty >= 0.002:

        cancel_all()

        tp_price = round(short_avg * 0.95, 1)

        sl_price = round(short_avg * 1.2, 1)

        tp = close_short_limit(tp_price, short_qty)

        sl = market_long(short_qty)

        actions.append({
            "mode": "short_merged",
            "tp_price": tp_price,
            "sl_price": sl_price,
            "tp": tp,
            "sl": sl
        })

    # =====================================
    # LONG 並單完成
    # =====================================

    if long_qty >= 0.002:

        cancel_all()

        tp_price = round(long_avg * 1.05, 1)

        sl_price = round(long_avg * 0.8, 1)

        tp = close_long_limit(tp_price, long_qty)

        sl = market_short(long_qty)

        actions.append({
            "mode": "long_merged",
            "tp_price": tp_price,
            "sl_price": sl_price,
            "tp": tp,
            "sl": sl
        })

    return {
        "ok": True,
        "actions": actions,
        "long_qty": long_qty,
        "short_qty": short_qty
    }

# =========================================
# 測試
# =========================================

@app.route("/test")

def test():

    return {
        "API_KEY_EXISTS": API_KEY != "",
        "SECRET_KEY_EXISTS": SECRET_KEY != ""
    }

# =========================================
# 主程式
# =========================================

@app.route("/")

def home():

    return {
        "msg": "phase1_20percent_cycle"
    }

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
