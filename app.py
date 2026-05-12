import os
import time
import hmac
import hashlib
import requests

from flask import Flask, jsonify

app = Flask(__name__)

# =========================
# 環境變數
# =========================
API_KEY = os.getenv("BINGX_API_KEY", "")
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

# =========================
# 基本設定
# =========================
BASE_URL = "https://open-api.bingx.com"

SYMBOL = "BTC-USDT"

LEVERAGE = 30
QUANTITY = 0.001

# 最大層數
MAX_LAYER = 3

# =========================
# BingX 簽名
# =========================
def sign_params(params):

    query = "&".join(
        [f"{k}={params[k]}" for k in sorted(params)]
    )

    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return signature


# =========================
# BingX POST
# =========================
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


# =========================
# BingX GET
# =========================
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


# =========================
# 設定多單槓桿
# =========================
def set_leverage_long():

    path = "/openApi/swap/v2/trade/leverage"

    params = {
        "symbol": SYMBOL,
        "side": "LONG",
        "leverage": LEVERAGE
    }

    return bingx_post(path, params)


# =========================
# 設定空單槓桿
# =========================
def set_leverage_short():

    path = "/openApi/swap/v2/trade/leverage"

    params = {
        "symbol": SYMBOL,
        "side": "SHORT",
        "leverage": LEVERAGE
    }

    return bingx_post(path, params)


# =========================
# 市價開多
# =========================
def open_long_market():

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": "BUY",
        "positionSide": "LONG",
        "type": "MARKET",
        "quantity": QUANTITY
    }

    return bingx_post(path, params)


# =========================
# 市價開空
# =========================
def open_short_market():

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": "SELL",
        "positionSide": "SHORT",
        "type": "MARKET",
        "quantity": QUANTITY
    }

    return bingx_post(path, params)


# =========================
# 查詢持倉
# =========================
def get_positions():

    path = "/openApi/swap/v2/user/positions"

    params = {
        "symbol": SYMBOL
    }

    return bingx_get(path, params)


# =========================
# 第一階段：
# 雙向鎖倉進擊法
# 同時開多＋開空
# =========================
@app.route("/phase1")
def phase1():

    if not API_KEY or not SECRET_KEY:

        return jsonify({
            "ok": False,
            "error": "缺少 API KEY"
        })

    # 設定槓桿
    long_leverage = set_leverage_long()
    short_leverage = set_leverage_short()

    # 同時開多空
    long_order = open_long_market()
    short_order = open_short_market()

    # 查詢持倉
    positions = get_positions()

    return jsonify({

        "ok": True,

        "strategy": "雙向鎖倉進擊法",

        "symbol": SYMBOL,

        "long_leverage": long_leverage,

        "short_leverage": short_leverage,

        "long_order": long_order,

        "short_order": short_order,

        "positions": positions
    })


# =========================
# 健康檢查
# =========================
@app.route("/")
def home():
    return "ok"


# =========================
# API Key 測試
# =========================
@app.route("/check_key")
def check_key():

    return jsonify({

        "API_KEY_EXISTS": bool(API_KEY),
        "API_KEY_HEAD": API_KEY[:5] if API_KEY else "NONE",

        "SECRET_KEY_EXISTS": bool(SECRET_KEY),
        "SECRET_KEY_HEAD": SECRET_KEY[:5] if SECRET_KEY else "NONE"
    })


# =========================
# 啟動
# =========================
if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
