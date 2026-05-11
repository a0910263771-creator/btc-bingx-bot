import os, time, hmac, json, hashlib, threading, requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)
@app.route("/ping")
def ping():
    return "ok", 200
BASE_URL = "https://open-api.bingx.com"
MASTER_TOKEN = os.getenv("MASTER_TOKEN", "888888")

MIN_SCORE = 50
RISK_DIVISOR = 10
LEVERAGE = 30
MAX_UNITS = 4
STATE_FILE = "state.json"
VOLUME_MULTIPLIER = 1.5


def load_users():
    with open("users.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_state(state, user_id):
    if user_id not in state:
        state[user_id] = {
            "stage": "IDLE",
            "main_direction": None,
            "hedge_direction": None,
            "rescue_direction": None,
            "rescue_entry": 0,
            "mistakes_today": 0,
            "updated_at": int(time.time())
        }
    return state[user_id]


def reset_state(s):
    s["stage"] = "IDLE"
    s["main_direction"] = None
    s["hedge_direction"] = None
    s["rescue_direction"] = None
    s["rescue_entry"] = 0
    s["updated_at"] = int(time.time())


def opposite(direction):
    return "SHORT" if direction == "LONG" else "LONG"


def signed_url(path, api_secret, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    sig = hmac.new(
        api_secret.strip().encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return BASE_URL + path + "?" + query + "&signature=" + sig


def bingx_get(path, user, params=None):
    url = signed_url(path, user["api_secret"], params)
    headers = {"X-BX-APIKEY": user["api_key"].strip()}
    return requests.get(url, headers=headers, timeout=10).json()


def bingx_post(path, user, params=None):
    url = signed_url(path, user["api_secret"], params)
    headers = {"X-BX-APIKEY": user["api_key"].strip()}
    return requests.post(url, headers=headers, timeout=10).json()


def ema(values, period):
    if not values:
        return 0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def klines(user, interval, limit=80):
    data = bingx_get(
        "/openApi/swap/v3/quote/klines",
        user,
        {"symbol": user["symbol"], "interval": interval, "limit": limit}
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

    return sorted(candles, key=lambda x: x["time"])


def get_balance(user):
    data = bingx_get("/openApi/swap/v2/user/balance", user)
    b = data.get("data", {}).get("balance", {})
    return float(b.get("availableMargin") or b.get("availableBalance") or 0)


def get_price(user):
    data = bingx_get(
        "/openApi/swap/v2/quote/price",
        user,
        {"symbol": user["symbol"]}
    )
    return float(data.get("data", {}).get("price", 0))


def get_positions_raw(user):
    return bingx_get(
        "/openApi/swap/v2/user/positions",
        user,
        {"symbol": user["symbol"]}
    )


def pos_amt(p):
    return abs(float(p.get("positionAmt", p.get("positionAmount", 0))))


def pos_side(p):
    return p.get("positionSide", "")


def pos_entry(p):
    return float(
        p.get("avgPrice")
        or p.get("averagePrice")
        or p.get("entryPrice")
        or 0
    )


def get_positions(user):
    data = get_positions_raw(user).get("data", [])
    return [p for p in data if pos_amt(p) > 0]


def position_by_side(user, direction):
    total_amt = 0
    total_cost = 0

    for p in get_positions(user):
        if pos_side(p) == direction:
            amt = pos_amt(p)
            entry = pos_entry(p)
            total_amt += amt
            total_cost += amt * entry

    if total_amt <= 0:
        return None

    return {
        "direction": direction,
        "amount": total_amt,
        "entry": total_cost / total_amt
    }


def pnl_percent(user, direction):
    p = position_by_side(user, direction)
    if not p:
        return None

    price = get_price(user)
    entry = p["entry"]

    if direction == "LONG":
        raw = (price - entry) / entry * 100
    else:
        raw = (entry - price) / entry * 100

    return raw * LEVERAGE


def pnl_from_entry(user, direction, entry):
    if not entry:
        return None

    price = get_price(user)

    if direction == "LONG":
        raw = (price - entry) / entry * 100
    else:
        raw = (entry - price) / entry * 100

    return raw * LEVERAGE


def unit_notional(user):
    return (get_balance(user) / RISK_DIVISOR) * LEVERAGE


def total_units(user):
    unit = unit_notional(user)
    if unit <= 0:
        return 0

    price = get_price(user)
    total = 0

    for p in get_positions(user):
        total += pos_amt(p) * price

    return round(total / unit, 2)


def daily_direction(user):
    c = klines(user, "1d", 80)
    closes = [x["close"] for x in c]
    close = closes[-1]

    ema20 = ema(closes[-40:], 20)
    ema60 = ema(closes[-70:], 60)

    if ema20 > ema60 and close > ema20:
        return "LONG"
    if ema20 < ema60 and close < ema20:
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
        return {
            "direction": "LONG",
            "score": long_score,
            "long_score": long_score,
            "short_score": short_score,
            "reasons": long_reasons
        }

    return {
        "direction": "SHORT",
        "score": short_score,
        "long_score": long_score,
        "short_score": short_score,
        "reasons": short_reasons
    }


def confirm_15m(user, direction):
    c = klines(user, "15m", 60)
    closes = [x["close"] for x in c]
    close = closes[-1]
    ema20 = ema(closes[-40:], 20)

    if direction == "LONG" and close > ema20:
        return True, "15m EMA20 多方確認"

    if direction == "SHORT" and close < ema20:
        return True, "15m EMA20 空方確認"

    return False, "15m 未確認"


def strategy_signal(user):
    daily = daily_direction(user)
    one_h = score_1h(user)
    confirm, confirm_msg = confirm_15m(user, one_h["direction"])

    action = "WAIT"
    reasons = []

    if daily == "RANGE":
        reasons.append("日線盤整")

    elif one_h["direction"] != daily:
        reasons.append("1H 與日線不同方向")

    elif one_h["score"] < MIN_SCORE:
        reasons.append("1H 分數不足 50")

    elif not confirm:
        reasons.append("15m 未確認")

    else:
        action = one_h["direction"]
        reasons.append("日線 + 1H >= 50 + 15m 確認")

    return {
        "symbol": user["symbol"],
        "action": action,
        "daily_direction": daily,
        "one_h": one_h,
        "confirm_15m": confirm,
        "confirm_msg": confirm_msg,
        "units": total_units(user),
        "reasons": reasons
    }


def order_qty(user, units=1):
    balance = get_balance(user)
    price = get_price(user)
    order_usdt = (balance / RISK_DIVISOR) * LEVERAGE * units
    qty = round(order_usdt / price, 4)
    return qty, balance, price, order_usdt


def market_order(user, direction, units=1):
    if total_units(user) + units > MAX_UNITS:
        return {"ok": False, "error": "超過最大4單位"}

    qty, balance, price, order_usdt = order_qty(user, units)

    if qty <= 0:
        return {"ok": False, "error": "可用資金不足"}

    side = "BUY" if direction == "LONG" else "SELL"

    params = {
        "symbol": user["symbol"],
        "side": side,
        "positionSide": direction,
        "type": "MARKET",
        "quantity": qty
    }

    result = bingx_post("/openApi/swap/v2/trade/order", user, params)
    print("ORDER_RESULT", result, flush=True)

    return {
        "ok": str(result.get("code")) == "0",
        "direction": direction,
        "units": units,
        "qty": qty,
        "price": price,
        "order_usdt": order_usdt,
        "result": result
    }


def close_position(user, direction, ratio=1):
    p = position_by_side(user, direction)
    if not p:
        return {"ok": True, "message": "無倉可平"}

    qty = round(p["amount"] * ratio, 4)
    side = "SELL" if direction == "LONG" else "BUY"

    params = {
        "symbol": user["symbol"],
        "side": side,
        "positionSide": direction,
        "type": "MARKET",
        "quantity": qty,
        "reduceOnly": "true"
    }

    result = bingx_post("/openApi/swap/v2/trade/order", user, params)
    print("CLOSE_RESULT", result, flush=True)

    return {
        "ok": str(result.get("code")) == "0",
        "direction": direction,
        "qty": qty,
        "result": result
    }


def close_all(user):
    results = []
    for d in ["LONG", "SHORT"]:
        if position_by_side(user, d):
            results.append(close_position(user, d))
    return results


def run_strategy_for_user(user_id, user):
    state = load_state()
    s = get_state(state, user_id)

    sig = strategy_signal(user)
    stage = s["stage"]

    result = {
        "user": user_id,
        "stage": stage,
        "signal": sig,
        "action_taken": "WAIT"
    }

    if s.get("mistakes_today", 0) >= 2:
        result["reason"] = "今日判錯2次，停手"
        save_state(state)
        return result

    main = s.get("main_direction")
    hedge = s.get("hedge_direction")

    # 1. 等待訊號，開第一筆試倉
    if stage == "IDLE":
        if position_by_side(user, "LONG") or position_by_side(user, "SHORT"):
            result["reason"] = "已有倉位，先不開新單"
        elif sig["action"] in ["LONG", "SHORT"]:
            order = market_order(user, sig["action"], 1)
            if order["ok"]:
                s["stage"] = "FIRST_ENTRY"
                s["main_direction"] = sig["action"]
                s["hedge_direction"] = opposite(sig["action"])
                result["action_taken"] = "第一筆試倉"
                result["order"] = order

    # 2. 第一單：+10% 開反向鎖倉；-10% 強制平倉重來
    elif stage == "FIRST_ENTRY":
        pnl = pnl_percent(user, main)
        result["main_pnl"] = pnl

        if pnl is not None and pnl <= -10:
            result["close"] = close_position(user, main)
            s["mistakes_today"] = s.get("mistakes_today", 0) + 1
            reset_state(s)
            result["action_taken"] = "第一單-10%，強制平倉重來"

        elif pnl is not None and pnl >= 10:
            order = market_order(user, hedge, 1)
            if order["ok"]:
                s["stage"] = "HEDGE_LOCK"
                result["action_taken"] = "第一單+10%，開反向鎖倉"
                result["order"] = order

    # 3. 鎖倉中：原獲利單+50%且技術轉向，平獲利單，加碼反向1單位
    elif stage == "HEDGE_LOCK":
        main_pnl = pnl_percent(user, main)
        result["main_pnl"] = main_pnl

        reverse_ok = (
            sig["action"] == hedge
            and sig["confirm_15m"]
            and sig["one_h"]["score"] >= MIN_SCORE
        )

        if main_pnl is not None and main_pnl >= 50 and reverse_ok:
            close_win = close_position(user, main)
            add_loss_side = market_order(user, hedge, 1)

            if close_win["ok"] and add_loss_side["ok"]:
                s["stage"] = "LEFT_2_LOSS"
                s["main_direction"] = hedge
                s["hedge_direction"] = opposite(hedge)
                result["action_taken"] = "原單+50%且轉向，平獲利單，加碼反向1單位，剩2單位"
                result["close_win"] = close_win
                result["add"] = add_loss_side

    # 4. 剩2單位：轉盈+5%全平；若虧損-30%，回補原方向2單位救援
    elif stage == "LEFT_2_LOSS":
        main = s["main_direction"]
        hedge = s["hedge_direction"]
        pnl = pnl_percent(user, main)
        result["left_2_pnl"] = pnl

        if pnl is not None and pnl >= 5:
            result["close_all"] = close_all(user)
            reset_state(s)
            result["action_taken"] = "2單位轉盈5%，全平重新循環"

        elif pnl is not None and pnl <= -30:
            order = market_order(user, hedge, 2)
            if order["ok"]:
                s["stage"] = "RESCUE_2"
                s["rescue_direction"] = hedge
                s["rescue_entry"] = get_price(user)
                result["action_taken"] = "2單位虧損-30%，反向進場2單位"
                result["order"] = order

    # 5. 救援2單位：這2單位獲利+20%，平掉，再回補原虧損方向2單位，攤平成4單位
    elif stage == "RESCUE_2":
        rescue = s["rescue_direction"]
        rescue_pnl = pnl_from_entry(user, rescue, s.get("rescue_entry", 0))
        result["rescue_pnl"] = rescue_pnl

        if rescue_pnl is not None and rescue_pnl >= 20:
            close_rescue = close_position(user, rescue)
            add_original = market_order(user, opposite(rescue), 2)

            if close_rescue["ok"] and add_original["ok"]:
                s["stage"] = "FINAL_4"
                s["main_direction"] = opposite(rescue)
                result["action_taken"] = "救援2單位+20%，平救援單，再回補原方向2單位，形成4單位"
                result["close_rescue"] = close_rescue
                result["add_original"] = add_original

    # 6. 最終4單位：停利5%，停損50%
    elif stage == "FINAL_4":
        main = s["main_direction"]
        pnl = pnl_percent(user, main)
        result["final_4_pnl"] = pnl

        if pnl is not None and pnl >= 5:
            result["close_all"] = close_all(user)
            reset_state(s)
            result["action_taken"] = "最終4單位停利5%，全平重來"

        elif pnl is not None and pnl <= -50:
            result["close_all"] = close_all(user)
            s["mistakes_today"] = s.get("mistakes_today", 0) + 1
            reset_state(s)
            result["action_taken"] = "最終4單位-50%，強制平倉重來"

    s["updated_at"] = int(time.time())
    save_state(state)
    return result


def token_ok(user=None):
    token = request.args.get("token", "")
    return token == MASTER_TOKEN or (user and token == user.get("trade_token", ""))


@app.route("/")
def home():
    return "BTC BingX Bot 已啟動"


@app.route("/users")
def users():
    if request.args.get("token") != MASTER_TOKEN:
        return jsonify({"ok": False, "error": "token錯誤"})
    return jsonify({"ok": True, "users": list(load_users().keys())})


@app.route("/user/<user_id>/signal")
def user_signal(user_id):
    user = load_users().get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "找不到 user"})
    return jsonify(strategy_signal(user))


@app.route("/user/<user_id>/state")
def user_state_view(user_id):
    state = load_state()
    return jsonify(get_state(state, user_id))


@app.route("/user/<user_id>/reset_state")
def user_reset_state(user_id):
    if request.args.get("token") != MASTER_TOKEN:
        return jsonify({"ok": False, "error": "token錯誤"})
    state = load_state()
    s = get_state(state, user_id)
    reset_state(s)
    save_state(state)
    return jsonify({"ok": True, "state": s})


@app.route("/user/<user_id>/positions")
def user_positions(user_id):
    user = load_users().get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "找不到 user"})
    return jsonify(get_positions_raw(user))


@app.route("/user/<user_id>/auto_trade")
def user_auto_trade(user_id):
    user = load_users().get(user_id)

    if not user:
        return jsonify({"ok": False, "error": "找不到 user"})

    if not token_ok(user):
        return jsonify({"ok": False, "error": "token錯誤"})

    if not user.get("enabled", False):
        return jsonify({"ok": False, "error": "此用戶未啟用"})

    return jsonify({"ok": True, "result": run_strategy_for_user(user_id, user)})


@app.route("/run_all")
def run_all():
    if request.args.get("token") != MASTER_TOKEN:
        return jsonify({"ok": False, "error": "token錯誤"})

    results = {}
    users_data = load_users()

    for user_id, user in users_data.items():
        if not user.get("enabled", False):
            results[user_id] = {"ok": False, "message": "未啟用"}
            continue

        try:
            results[user_id] = run_strategy_for_user(user_id, user)
        except Exception as e:
            results[user_id] = {"ok": False, "error": str(e)}

    return jsonify({"ok": True, "results": results})


def run_bot_job():
    try:
        users_data = load_users()

        for user_id, user in users_data.items():
            if not user.get("enabled", False):
                continue

            try:
                result = run_strategy_for_user(user_id, user)
                print("BOT_RESULT", user_id, result, flush=True)
            except Exception as e:
                print("BOT_ERROR", user_id, str(e), flush=True)

    except Exception as e:
        print("RUN_BOT_ERROR", str(e), flush=True)


@app.route("/cron")
def cron():
    if request.args.get("token") != MASTER_TOKEN:
        return Response("BAD", status=403, mimetype="text/plain")
    threading.Thread(target=run_bot_job).start()
    return Response("OK", status=200, mimetype="text/plain")


@app.route("/cron204")
def cron204():
    if request.args.get("token") != MASTER_TOKEN:
        return Response("BAD", status=403, mimetype="text/plain")
    threading.Thread(target=run_bot_job).start()
    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
