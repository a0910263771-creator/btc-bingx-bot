import os, time, hmac, hashlib, requests
from urllib.parse import urlencode
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.getenv("BINGX_API_KEY")
API_SECRET = os.getenv("BINGX_API_SECRET")
TRADE_TOKEN = os.getenv("TRADE_TOKEN")
BASE_URL = "https://open-api.bingx.com"

SYMBOL = "BTC-USDT"
MIN_SCORE = 75
RISK_DIVISOR = 10
VOLUME_MULTIPLIER = 1.5

def sign(params):
    query = urlencode(sorted(params.items()))
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def bingx_get(path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": API_KEY}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bingx_post(path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": API_KEY}
    r = requests.post(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def check_token():
    return TRADE_TOKEN and request.args.get("token") == TRADE_TOKEN

def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def get_klines():
    data = bingx_get("/openApi/swap/v3/quote/klines", {
        "symbol": SYMBOL,
        "interval": "1h",
        "limit": 80
    })

    raw = data.get("data", [])
    candles = []

    for k in raw:
        if isinstance(k, dict):
            candles.append({
                "time": int(k.get("time", k.get("openTime", 0))),
                "open": float(k["open"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "close": float(k["close"]),
                "volume": float(k["volume"])
            })
        else:
            candles.append({
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5])
            })

    candles = sorted(candles, key=lambda x: x["time"])
    return candles[-60:]

def get_available_usdt():
    data = bingx_get("/openApi/swap/v2/user/balance")
    balance = data.get("data", {}).get("balance", {})
    return float(balance.get("availableMargin", 0))

def get_price():
    data = bingx_get("/openApi/swap/v2/quote/price", {"symbol": SYMBOL})
    return float(data.get("data", {}).get("price"))

def calculate_signal():
    candles = get_klines()

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    last = candles[-1]
    close = last["close"]

    prev_high = max(highs[-21:-1])
    prev_low = min(lows[-21:-1])
    avg_volume = sum(volumes[-21:-1]) / 20

    ema20 = ema(closes[-30:], 20)
    ema50 = ema(closes[-60:], 50)

    long_score = 0
    short_score = 0
    long_reasons = []
    short_reasons = []

    # C 爆量
    if last["volume"] > avg_volume * VOLUME_MULTIPLIER:
        long_score += 25
        short_score += 25
        long_reasons.append("C 爆量成立")
        short_reasons.append("C 爆量成立")

    # E 結構突破
    if close > prev_high:
        long_score += 25
        long_reasons.append("E 突破前高")
    if close < prev_low:
        short_score += 25
        short_reasons.append("E 跌破前低")

    # F 均線排列
    if ema20 > ema50 and close > ema20:
        long_score += 25
        long_reasons.append("F 多頭均線排列")
    if ema20 < ema50 and close < ema20:
        short_score += 25
        short_reasons.append("F 空頭均線排列")

    # H 壓力支撐
    middle = (prev_high + prev_low) / 2

    if close > middle:
        long_score += 25
        long_reasons.append("H 站上支撐區")
    if close < middle:
        short_score += 25
        short_reasons.append("H 跌破壓力區")

    if long_score >= short_score:
        direction = "LONG"
        final_score = long_score
        reasons = long_reasons
    else:
        direction = "SHORT"
        final_score = short_score
        reasons = short_reasons

    action = direction if final_score >= MIN_SCORE else "WAIT"

    return {
        "symbol": SYMBOL,
        "close": close,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "ema20": ema20,
        "ema50": ema50,
        "volume": last["volume"],
        "avg_volume": avg_volume,
        "long_score": long_score,
        "short_score": short_score,
        "final_score": final_score,
        "direction": direction,
        "action": action,
        "reasons": reasons
    }

def calc_quantity():
    available = get_available_usdt()
    price = get_price()
    order_usdt = available / RISK_DIVISOR
    quantity = round(order_usdt / price, 4)
    return quantity, available, price, order_usdt

def place_order(direction):
    quantity, available, price, order_usdt = calc_quantity()

    if quantity <= 0:
        return {"ok": False, "error": "可用餘額不足"}

    if direction == "LONG":
        side = "BUY"
        position_side = "LONG"
    else:
        side = "SELL"
        position_side = "SHORT"

    params = {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity
    }

    result = bingx_post("/openApi/swap/v2/trade/order", params)

    return {
        "quantity": quantity,
        "availableUSDT": available,
        "price": price,
        "orderUSDT": order_usdt,
        "side": side,
        "positionSide": position_side,
        "bingxResult": result
    }

@app.route("/")
def home():
    return "BTC BingX Bot CEFH 自動策略版啟動成功！"

@app.route("/balance")
def balance():
    return jsonify(bingx_get("/openApi/swap/v2/user/balance"))

@app.route("/positions")
def positions():
    return jsonify(bingx_get("/openApi/swap/v2/user/positions", {
        "symbol": SYMBOL
    }))

@app.route("/signal")
def signal():
    return jsonify(calculate_signal())

@app.route("/auto_trade")
def auto_trade():
    if not check_token():
        return jsonify({
            "ok": False,
            "error": "token 錯誤，禁止交易"
        })

    sig = calculate_signal()

    if sig["action"] == "WAIT":
        return jsonify({
            "ok": True,
            "trade": False,
            "message": "未達 75 分，不下單",
            "signal": sig
        })

    order = place_order(sig["action"])

    return jsonify({
        "ok": True,
        "trade": True,
        "signal": sig,
        "order": order
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
