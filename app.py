import os
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.getenv("BINGX_API_KEY")
API_SECRET = os.getenv("BINGX_API_SECRET")
TRADE_TOKEN = os.getenv("TRADE_TOKEN")
BASE_URL = "https://open-api.bingx.com"

SYMBOL = "BTC-USDT"
LEVERAGE = 1
RISK_DIVISOR = 10

def sign(params):
    query = urlencode(sorted(params.items()))
    return hmac.new(
        API_SECRET.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()

def bingx_get(path, params=None):
    if params is None:
        params = {}

    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    response = requests.get(
        BASE_URL + path,
        params=params,
        headers=headers,
        timeout=10
    )
    return response.json()

def bingx_post(path, params=None):
    if params is None:
        params = {}

    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    response = requests.post(
        BASE_URL + path,
        params=params,
        headers=headers,
        timeout=10
    )
    return response.json()

def check_token():
    token = request.args.get("token")
    return TRADE_TOKEN and token == TRADE_TOKEN

def get_available_usdt():
    data = bingx_get("/openApi/swap/v2/user/balance")
    balance = data.get("data", {}).get("balance", {})
    return float(balance.get("availableMargin", 0))

def get_btc_price():
    data = bingx_get("/openApi/swap/v2/quote/price", {
        "symbol": SYMBOL
    })
    price = data.get("data", {}).get("price")
    return float(price)

def calc_quantity():
    available_usdt = get_available_usdt()
    btc_price = get_btc_price()
    order_usdt = available_usdt / RISK_DIVISOR
    quantity = order_usdt / btc_price * LEVERAGE
    quantity = round(quantity, 4)

    return quantity, available_usdt, btc_price, order_usdt

def place_order(side, position_side):
    if not check_token():
        return jsonify({
            "ok": False,
            "error": "token 錯誤，禁止下單"
        })

    quantity, available_usdt, btc_price, order_usdt = calc_quantity()

    if quantity <= 0:
        return jsonify({
            "ok": False,
            "error": "可用餘額不足"
        })

    params = {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity
    }

    result = bingx_post("/openApi/swap/v2/trade/order", params)

    return jsonify({
        "ok": True,
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "quantity": quantity,
        "availableUSDT": available_usdt,
        "btcPrice": btc_price,
        "orderUSDT": order_usdt,
        "bingxResult": result
    })

@app.route("/")
def home():
    return "BTC BingX Bot 真實下單版啟動成功！"

@app.route("/balance")
def balance():
    return jsonify(bingx_get("/openApi/swap/v2/user/balance"))

@app.route("/positions")
def positions():
    return jsonify(bingx_get("/openApi/swap/v2/user/positions", {
        "symbol": SYMBOL
    }))

@app.route("/order/buy")
def order_buy():
    return place_order("BUY", "LONG")

@app.route("/order/sell")
def order_sell():
    return place_order("SELL", "SHORT")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
