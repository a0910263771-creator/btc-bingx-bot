import os, time, hmac, hashlib, requests, json
from urllib.parse import urlencode
from flask import Flask, jsonify, request

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
MASTER_TOKEN = os.getenv("MASTER_TOKEN", "admin123")

MIN_SCORE = 75
RISK_DIVISOR = 10
MAX_UNITS = 6
VOLUME_MULTIPLIER = 1.5

def load_users():
    with open("users.json", "r") as f:
        return json.load(f)

def sign(params, api_secret):
    query = urlencode(sorted(params.items()))
    return hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

def bingx_get(path, api_key, api_secret, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params, api_secret)
    headers = {"X-BX-APIKEY": api_key}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bingx_post(path, api_key, api_secret, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params, api_secret)
    headers = {"X-BX-APIKEY": api_key}
    r = requests.post(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def get_user(user_id):
    users = load_users()
    return users.get(user_id)

def klines(user, interval, limit=80):
    data = bingx_get(
        "/openApi/swap/v3/quote/klines",
        user["api_key"],
        user["api_secret"],
        {
            "symbol": user["symbol"],
            "interval": interval,
            "limit": limit
        }
    )

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
    return candles

def get_balance(user):
    data = bingx_get(
        "/openApi/swap/v2/user/balance",
        user["api_key"],
        user["api_secret"]
    )
    b = data.get("data", {}).get("balance", {})
    return float(b.get("availableMargin", 0))

def get_price(user):
    data = bingx_get(
        "/openApi/swap/v2/quote/price",
        user["api_key"],
        user["api_secret"],
        {"symbol": user["symbol"]}
    )
    return float(data.get("data", {}).get("price"))

def get_positions_raw(user):
    return bingx_get(
        "/openApi/swap/v2/user/positions",
        user["api_key"],
        user["api_secret"],
        {"symbol": user["symbol"]}
    )

def get_positions(user):
    data = get_positions_raw(user).get("data", [])
    result = []

    for p in data:
        amt = float(p.get("positionAmt", p.get("positionAmount", 0)))
        if amt != 0:
            result.append(p)

    return result

def daily_direction(user):
    c = klines(user, "1d", 80)

    closes = [x["close"] for x in c]
    highs = [x["high"] for x in c]
    lows = [x["low"] for x in c]

    last = c[-1]
    ema20 = ema(closes[-40:], 20)
    ema60 = ema(closes[-70:], 60)

    recent_high = max(highs[-21:-1])
    recent_low = min(lows[-21:-1])

    if last["close"] > recent_high and ema20 > ema60:
        return "LONG"

    if last["close"] < recent_low and ema20 < ema60:
        return "SHORT"

    return "RANGE"

def score_1h(user):
    c = klines(user, "1h", 80)

    closes = [x["close"] for x in c]
    highs = [x["high"] for x in c]
    lows = [x["low"] for x in c]
    vols = [x["volume"] for x in c]

    last = c[-1]
    close = last["close"]

    prev_high = max(highs[-21:-1])
    prev_low = min(lows[-21:-1])
    avg_vol = sum(vols[-21:-1]) / 20

    ema20 = ema(closes[-40:], 20)
    ema50 = ema(closes[-60:], 50)

    mid = (prev_high + prev_low) / 2

    long_score = 0
    short_score = 0
    long_reasons = []
    short_reasons = []

    if last["volume"] > avg_vol * VOLUME_MULTIPLIER:
        long_score += 25
        short_score += 25
        long_reasons.append("C 爆量")
        short_reasons.append("C 爆量")

    if close > prev_high:
        long_score += 25
        long_reasons.append("E 突破前高")

    if close < prev_low:
        short_score += 25
        short_reasons.append("E 跌破前低")

    if ema20 > ema50 and close > ema20:
        long_score += 25
        long_reasons.append("F 多頭均線")

    if ema20 < ema50 and close < ema20:
        short_score += 25
        short_reasons.append("F 空頭均線")

    if close > mid:
        long_score += 25
        long_reasons.append("H 支撐偏多")

    if close < mid:
        short_score += 25
        short_reasons.append("H 壓力偏空")

    if long_score >= short_score:
        direction = "LONG"
        score = long_score
        reasons = long_reasons
    else:
        direction = "SHORT"
        score = short_score
        reasons = short_reasons

    return {
        "direction": direction,
        "score": score,
        "long_score": long_score,
        "short_score": short_score,
        "reasons": reasons,
        "close": close
    }

def confirm_15m(user, direction):
    c = klines(user, "15m", 60)

    closes = [x["close"] for x in c]
    highs = [x["high"] for x in c]
    lows = [x["low"] for x in c]

    last = c[-1]
    prev_high = max(highs[-13:-1])
    prev_low = min(lows[-13:-1])
    ema20 = ema(closes[-40:], 20)

    if direction == "LONG":
        if last["close"] > prev_high:
            return True, "15m 突破追多"
        if last["low"] <= ema20 and last["close"] > ema20:
            return True, "15m 回踩轉強做多"

    if direction == "SHORT":
        if last["close"] < prev_low:
            return True, "15m 跌破追空"
        if last["high"] >= ema20 and last["close"] < ema20:
            return True, "15m 反壓轉弱做空"

    return False, "15m 未確認"

def total_units(user):
    available = get_balance(user)
    unit_usdt = max(available / RISK_DIVISOR, 1)
    total_notional = 0
    p = get_price(user)

    for pos in get_positions(user):
        amt = abs(float(pos.get("positionAmt", pos.get("positionAmount", 0))))
        total_notional += amt * p

    return round(total_notional / unit_usdt, 2)

def order_qty(user):
    usdt = get_balance(user)
    p = get_price(user)
    order_usdt = usdt / RISK_DIVISOR
    qty = round(order_usdt / p, 4)
    return qty, usdt, p, order_usdt

def market_order(user, direction):
    qty, usdt, p, order_usdt = order_qty(user)

    if qty <= 0:
        return {"ok": False, "error": "可用資金不足"}

    side = "BUY" if direction == "LONG" else "SELL"
    position_side = "LONG" if direction == "LONG" else "SHORT"

    params = {
        "symbol": user["symbol"],
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": qty
    }

    result = bingx_post(
        "/openApi/swap/v2/trade/order",
        user["api_key"],
        user["api_secret"],
        params
    )

    return {
        "qty": qty,
        "order_usdt": order_usdt,
        "price": p,
        "side": side,
        "positionSide": position_side,
        "result": result
    }

def strategy_signal(user):
    daily = daily_direction(user)
    one_h = score_1h(user)
    confirm, confirm_msg = confirm_15m(user, one_h["direction"])
    units = total_units(user)

    action = "WAIT"
    reasons = []

    if units >= MAX_UNITS:
        reasons.append("總持倉已達 6 單位")
    elif daily != "RANGE" and one_h["direction"] == daily and one_h["score"] >= MIN_SCORE and confirm:
        action = one_h["direction"]
        reasons.append("日線方向 + 1H >=75 + 15m 確認")
    else:
        reasons.append("條件未齊，不進場")

    return {
        "symbol": user["symbol"],
        "action": action,
        "daily_direction": daily,
        "one_h": one_h,
        "confirm_15m": confirm,
        "confirm_msg": confirm_msg,
        "units": units,
        "reasons": reasons
    }

@app.route("/")
def home():
    return "多人版 BTC BingX Bot 已啟動"

@app.route("/users")
def users():
    if request.args.get("token") != MASTER_TOKEN:
        return jsonify({"ok": False})

    users = load_users()

    return jsonify({
        "ok": True,
        "users": list(users.keys())
    })

@app.route("/user/<user_id>/balance")
def user_balance(user_id):
    user = get_user(user_id)

    if not user:
        return jsonify({"ok": False})

    return jsonify(bingx_get(
        "/openApi/swap/v2/user/balance",
        user["api_key"],
        user["api_secret"]
    ))

@app.route("/user/<user_id>/positions")
def user_positions(user_id):
    user = get_user(user_id)

    if not user:
        return jsonify({"ok": False})

    return jsonify(get_positions_raw(user))

@app.route("/user/<user_id>/signal")
def user_signal(user_id):
    user = get_user(user_id)

    if not user:
        return jsonify({"ok": False})

    return jsonify(strategy_signal(user))

@app.route("/user/<user_id>/auto_trade")
def user_auto_trade(user_id):
    user = get_user(user_id)

    if not user:
        return jsonify({"ok": False})

    if request.args.get("token") != user["trade_token"]:
        return jsonify({"ok": False})

    if not user.get("enabled", False):
        return jsonify({"ok": False})

    sig = strategy_signal(user)

    if sig["action"] == "WAIT":
        return jsonify({
            "ok": True,
            "trade": False,
            "action": "WAIT",
            "symbol": user["symbol"]
        })

    order = market_order(user, sig["action"])

    return jsonify({
        "ok": True,
        "trade": True,
        "action": sig["action"],
        "symbol": user["symbol"],
        "qty": order.get("qty")
    })

@app.route("/run_all")
def run_all():
    if request.args.get("token") != MASTER_TOKEN:
        return jsonify({"ok": False})

    users = load_users()
    done = 0
    traded = 0
    errors = 0

    for user_id, user in users.items():
        if not user.get("enabled", False):
            continue

        done += 1

        try:
            sig = strategy_signal(user)

            if sig["action"] != "WAIT":
                market_order(user, sig["action"])
                traded += 1

        except Exception:
            errors += 1

    return jsonify({
        "ok": True,
        "users": done,
        "traded": traded,
        "errors": errors
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
