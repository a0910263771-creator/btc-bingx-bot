import os
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BINGX_API_KEY")
API_SECRET = os.getenv("BINGX_API_SECRET")
BASE_URL = "https://open-api.bingx.com"

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

    url = BASE_URL + path
    r = requests.get(url, params=params, headers=headers, timeout=10)
    return r.json()

@app.route("/")
def home():
    return "BTC BingX Bot 安全測試版啟動成功！"

@app.route("/balance")
def balance():
    if not API_KEY or not API_SECRET:
        return jsonify({
            "ok": False,
            "error": "缺少 BINGX_API_KEY 或 BINGX_API_SECRET"
        })

    result = bingx_get("/openApi/swap/v2/user/balance")
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
    
