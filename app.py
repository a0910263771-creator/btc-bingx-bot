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

BASE_URL = "https://open-api.bingx.com"
SYMBOL = "BTC-USDT"

LEVERAGE = 30
QUANTITY = 0.001   # 先用最小測試單


# =========================
# BingX 簽名
# =========================
def sign_params(params):
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query, signature


def bingx_post(path, params):
    params["timestamp"] = int(time.time() * 1000)

    query, signature = sign_params(params)
    url = f"{BASE_URL}{path}?{query}&signature={signature}"

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    res = requests.post(url, headers=headers, timeout=15)

    try:
        return res.json()
    except Exception:
        return {
            "code": -1,
            "msg": res.text
        }


# =========================
# 設定槓桿
# =========================
def set_leverage_long():
    path = "/openApi/swap/v2/trade/leverage"

    params = {
        "symbol": SYMBOL,
        "side": "LONG",
        "leverage": LEVERAGE,
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
        "quantity": QUANTITY,
}

    return bingx_post(path, params)


# =========================
# 手動測試網址
# =========================
@app.route("/")
def home():
    return {
        "API_KEY_EXISTS": bool(API_KEY),
        "SECRET_KEY_EXISTS": bool(SECRET_KEY),
        "API_KEY_HEAD": API_KEY[:5] if API_KEY else "NONE",
        "SECRET_KEY_HEAD": SECRET_KEY[:5] if SECRET_KEY else "NONE",
    }


@app.route("/test_long")
def test_long():
    if not API_KEY or not SECRET_KEY:
        return jsonify({
            "ok": False,
            "error": "缺少 BINGX_API_KEY 或 BINGX_SECRET_KEY"
        })

    leverage_result = set_leverage_long()
    order_result = open_long_market()

    return jsonify({
        "ok": True,
        "symbol": SYMBOL,
        "leverage_result": leverage_result,
        "order_result": order_result
    })


# =========================
# Render 啟動
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
