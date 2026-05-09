import os, time, hmac, hashlib, requests, json
from datetime import datetime, timezone
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
MAX_UNITS = 6
VOLUME_MULTIPLIER = 1.5
DAILY_MAX_WRONG = 2
STOP_LOSS_RATE = -0.50
LOCK_REVERSE_PROFIT = 0.10
SWITCH_PROFIT_MIN = 0.30

STATE_FILE = "bot_state.json"

def load_state():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    default = {"date": today, "wrong_count": 0, "last_action": ""}
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except:
        state = default
    if state.get("date") != today:
        state = default
    return state

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def sign(params):
    query = urlencode(sorted(params.items()))
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def bingx_get(path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": API_KEY}
    return requests.get(BASE_URL + path, params=params, headers=headers, timeout=10).json()

def bingx_post(path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": API_KEY}
    return requests.post(BASE_URL + path, params=params, headers=headers, timeout=10).json()

def check_token():
    return TRADE_TOKEN and request.args.get("token") == TRADE_TOKEN

def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def klines(interval, limit=80):
    data = bingx_get("/openApi/swap/v3/quote/klines", {
        "symbol": SYMBOL,
        "interval": interval,
        "limit": limit
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
    return sorted(candles, key=lambda x: x["time"])

def balance():
    data = bingx_get("/openApi/swap/v2/user/balance")
    b = data.get("data", {}).get("balance", {})
    return float(b.get("availableMargin", 0))

def price():
    data = bingx_get("/openApi/swap/v2/quote/price", {"symbol": SYMBOL})
    return float(data.get("data", {}).get("price"))

def positions_raw():
    return bingx_get("/openApi/swap/v2/user/positions", {"symbol": SYMBOL})

def get_positions():
    data = positions_raw().get("data", [])
    result = []
    for p in data:
        amt = float(p.get("positionAmt", p.get("positionAmount", 0)))
        if amt != 0:
            result.append(p)
    return result

def total_units():
    available = balance()
    unit_usdt = max(available / RISK_DIVISOR, 1)
    total_notional = 0
    for p in get_positions():
        amt = abs(float(p.get("positionAmt", p.get("positionAmount", 0))))
        total_notional += amt * price()
    return round(total_notional / unit_usdt, 2)

def daily_direction():
    c = klines("1d", 80)
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

def score_1h():
    c = klines("1h", 80)
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
        "close": close,
        "prev_high": prev_high,
        "prev_low": prev_low
    }

def confirm_15m(direction):
    c = klines("15m", 60)
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
    else:
        if last["close"] < prev_low:
            return True, "15m 跌破追空"
        if last["high"] >= ema20 and last["close"] < ema20:
            return True, "15m 反壓轉弱做空"

    return False, "15m 未確認"

def order_qty():
    usdt = balance()
    p = price()
    order_usdt = usdt / RISK_DIVISOR
    qty = round(order_usdt / p, 4)
    return qty, usdt, p, order_usdt

def market_order(direction, reduce_only=False):
    qty, usdt, p, order_usdt = order_qty()
    if qty <= 0:
        return {"ok": False, "error": "可用資金不足"}

    side = "BUY" if direction == "LONG" else "SELL"
    position_side = "LONG" if direction == "LONG" else "SHORT"

    params = {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": qty
    }

    if reduce_only:
        params["reduceOnly"] = "true"

    result = bingx_post("/openApi/swap/v2/trade/order", params)

    return {
        "qty": qty,
        "order_usdt": order_usdt,
        "price": p,
        "side": side,
        "positionSide": position_side,
        "result": result
    }

def risk_check():
    positions = get_positions()
    for p in positions:
        pnl_rate = float(p.get("pnlRatio", p.get("profitRate", 0)))
        side = p.get("positionSide", "")
        if pnl_rate <= STOP_LOSS_RATE:
            close_dir = "SHORT" if side == "LONG" else "LONG"
            return True, "單邊虧損 -50%，需要強制平倉", close_dir
    return False, "風控正常", None

def strategy_signal():
    state = load_state()
    daily = daily_direction()
    one_h = score_1h()
    confirm, confirm_msg = confirm_15m(one_h["direction"])
    units = total_units()

    action = "WAIT"
    reason = []

    if state["wrong_count"] >= DAILY_MAX_WRONG:
        reason.append("今日判錯已達 2 次，停手")
        return {
            "action": "STOP_DAY",
            "daily_direction": daily,
            "one_h": one_h,
            "confirm_15m": confirm,
            "confirm_msg": confirm_msg,
            "units": units,
            "state": state,
            "reason": reason
        }

    risk_hit, risk_msg, close_dir = risk_check()
    if risk_hit:
        return {
            "action": "FORCE_CLOSE",
            "close_direction": close_dir,
            "risk_msg": risk_msg,
            "state": state
        }

    if units >= MAX_UNITS:
        reason.append("總持倉已達 6 單位，禁止再擴張")
    elif daily != "RANGE" and one_h["direction"] == daily and one_h["score"] >= MIN_SCORE and confirm:
        action = one_h["direction"]
        reason.append("日線方向 + 1H >=75 + 15m 確認")
    else:
        reason.append("條件未齊，不進場")

    return {
        "action": action,
        "daily_direction": daily,
        "one_h": one_h,
        "confirm_15m": confirm,
        "confirm_msg": confirm_msg,
        "units": units,
        "state": state,
        "reason": reason
    }

@app.route("/")
def home():
    return "BTC BingX Bot 雙向進擊切換系統已啟動"

@app.route("/balance")
def route_balance():
    return jsonify(bingx_get("/openApi/swap/v2/user/balance"))

@app.route("/positions")
def route_positions():
    return jsonify(positions_raw())

@app.route("/signal")
def route_signal():
    return jsonify(strategy_signal())

@app.route("/auto_trade")
def route_auto_trade():
    if not check_token():
        return jsonify({"ok": False, "error": "token 錯誤，禁止交易"})

    sig = strategy_signal()

    if sig["action"] in ["WAIT", "STOP_DAY"]:
        return jsonify({
            "ok": True,
            "trade": False,
            "signal": sig
        })

    if sig["action"] == "FORCE_CLOSE":
        result = market_order(sig["close_direction"], reduce_only=True)
        return jsonify({
            "ok": True,
            "trade": True,
            "type": "FORCE_CLOSE",
            "signal": sig,
            "order": result
        })

    order = market_order(sig["action"])
    return jsonify({
        "ok": True,
        "trade": True,
        "type": "OPEN",
        "signal": sig,
        "order": order
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
