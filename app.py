import os, time, hmac, json, hashlib, threading, requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

BASE_URL = "https://open-api.bingx.com"
MASTER_TOKEN = os.getenv("MASTER_TOKEN", "888888")
STATE_FILE = "state.json"

MIN_SCORE = 50
RISK_DIVISOR = 10
LEVERAGE = 30
MAX_UNITS = 4
VOLUME_MULTIPLIER = 1.5


def today_key():
    return time.strftime("%Y-%m-%d", time.localtime())


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
    today = today_key()
    if user_id not in state:
        state[user_id] = {
            "stage": "IDLE",
            "main_direction": None,
            "hedge_direction": None,
            "rescue_direction": None,
            "rescue_entry": 0,
            "mistakes_today": 0,
            "mistake_date": today,
            "protection_stage": None,
            "updated_at": int(time.time())
        }

    s = state[user_id]

    if s.get("mistake_date") != today:
        s["mistakes_today"] = 0
        s["mistake_date"] = today

    return s


def reset_state(s):
    today = today_key()
    mistakes = s.get("mistakes_today", 0)

    s["stage"] = "IDLE"
    s["main_direction"] = None
    s["hedge_direction"] = None
    s["rescue_direction"] = None
    s["rescue_entry"] = 0
    s["mistakes_today"] = mistakes
    s["mistake_date"] = today
    s["protection_stage"] = None
    s["updated_at"] = int(time.time())


def opposite(direction):
    return "SHORT" if direction == "LONG" else "LONG"


def signed_query(api_secret, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    sig = hmac.new(
        api_secret.strip().encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query + "&signature=" + sig


def bingx_get(path, user, params=None):
    query = signed_query(user["api_secret"], params)
    headers = {"X-BX-APIKEY": user["api_key"].strip()}
    return requests.get(BASE_URL + path + "?" + query, headers=headers, timeout=10).json()


def bingx_post(path, user, params=None):
    query = signed_query(user["api_secret"], params)
    headers = {"X-BX-APIKEY": user["api_key"].strip()}
    return requests.post(BASE_URL + path + "?" + query, headers=headers, timeout=10).json()


def bingx_delete(path, user, params=None):
    query = signed_query(user["api_secret"], params)
    headers = {"X-BX-APIKEY": user["api_key"].strip()}
    return requests.delete(BASE_URL + path + "?" + query, headers=headers, timeout=10).json()


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
            if amt > 0 and entry > 0:
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
    pos = position_by_side(user, direction)
    if not pos:
        return None

    price = get_price(user)
    entry = pos["entry"]

    if direction == "LONG":
        raw = (price - entry) / entry * 100
    else:
        raw = (entry - price) / entry * 100

    return raw * LEVERAGE


def price_by_pnl(entry, direction, pnl_value):
    raw = pnl_value / LEVERAGE / 100
    if direction == "LONG":
        return round(entry * (1 + raw), 1)
    return round(entry * (1 - raw), 1)


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


def order_qty(user, units=1):
    balance = get_balance(user)
    price = get_price(user)
    order_usdt = (balance / RISK_DIVISOR) * LEVERAGE * units
    qty = round(order_usdt / price, 4)
    return qty, balance, price, order_usdt


def open_side(direction):
    return "BUY" if direction == "LONG" else "SELL"


def close_side(direction):
    return "SELL" if direction == "LONG" else "BUY"


def trigger_type(side, trigger_price, current_price):
    if side == "BUY":
        return "STOP_MARKET" if trigger_price > current_price else "TAKE_PROFIT_MARKET"
    return "TAKE_PROFIT_MARKET" if trigger_price > current_price else "STOP_MARKET"


def cancel_all_open_orders(user):
    result = bingx_delete(
        "/openApi/swap/v2/trade/allOpenOrders",
        user,
        {"symbol": user["symbol"]}
    )
    print("CANCEL_ALL_RESULT", result, flush=True)
    return result


def market_order(user, direction, units=1):
    if total_units(user) + units > MAX_UNITS:
        return {"ok": False, "error": "超過最大4單位"}

    qty, balance, price, order_usdt = order_qty(user, units)

    if qty <= 0:
        return {"ok": False, "error": "可用資金不足"}

    params = {
        "symbol": user["symbol"],
        "side": open_side(direction),
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


def trigger_open_order(user, direction, units, trigger_price):
    if total_units(user) + units > MAX_UNITS:
        return {"ok": False, "error": "超過最大4單位"}

    qty, balance, current_price, order_usdt = order_qty(user, units)
    side = open_side(direction)
    otype = trigger_type(side, trigger_price, current_price)

    params = {
        "symbol": user["symbol"],
        "side": side,
        "positionSide": direction,
        "type": otype,
        "quantity": qty,
        "stopPrice": trigger_price,
        "workingType": "MARK_PRICE"
    }

    result = bingx_post("/openApi/swap/v2/trade/order", user, params)
    print("TRIGGER_OPEN_RESULT", result, flush=True)

    return {
        "ok": str(result.get("code")) == "0",
        "direction": direction,
        "units": units,
        "qty": qty,
        "trigger_price": trigger_price,
        "type": otype,
        "result": result
    }


def trigger_close_order(user, direction, pnl_value):
    pos = position_by_side(user, direction)
    if not pos:
        return {"ok": True, "message": "無倉可掛保護單"}

    current_price = get_price(user)
    trigger_price = price_by_pnl(pos["entry"], direction, pnl_value)
    side = close_side(direction)
    otype = trigger_type(side, trigger_price, current_price)

    qty = round(pos["amount"], 4)

    params = {
        "symbol": user["symbol"],
        "side": side,
        "positionSide": direction,
        "type": otype,
        "quantity": qty,
        "stopPrice": trigger_price,
        "reduceOnly": "true",
        "workingType": "MARK_PRICE"
    }

    result = bingx_post("/openApi/swap/v2/trade/order", user, params)
    print("TRIGGER_CLOSE_RESULT", result, flush=True)

    return {
        "ok": str(result.get("code")) == "0",
        "direction": direction,
        "qty": qty,
        "pnl_trigger": pnl_value,
        "trigger_price": trigger_price,
        "type": otype,
        "result": result
    }


def close_position(user, direction):
    p = position_by_side(user, direction)
    if not p:
        return {"ok": True, "message": "無倉可平"}

    qty = round(p["amount"], 4)

    params = {
        "symbol": user["symbol"],
        "side": close_side(direction),
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
    cancel_all_open_orders(user)
    return results


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


def protect_first_entry(user, s):
    main = s["main_direction"]
    hedge = s["hedge_direction"]
    pos = position_by_side(user, main)
    if not pos:
        return {"ok": False, "error": "沒有第一單"}

    entry = pos["entry"]
    tp10_price = price_by_pnl(entry, main, 10)

    r1 = trigger_open_order(user, hedge, 1, tp10_price)
    r2 = trigger_close_order(user, main, -10)

    return {"stage": "FIRST_ENTRY", "tp10_open_hedge": r1, "sl10_close_main": r2}


def protect_left_2(user, s):
    main = s["main_direction"]
    hedge = s["hedge_direction"]
    pos = position_by_side(user, main)
    if not pos:
        return {"ok": False, "error": "沒有剩餘2單位"}

    entry = pos["entry"]
    loss30_price = price_by_pnl(entry, main, -30)

    r1 = trigger_close_order(user, main, 5)
    r2 = trigger_open_order(user, hedge, 2, loss30_price)

    return {"stage": "LEFT_2_LOSS", "tp5_close_left2": r1, "loss30_open_rescue2": r2}


def protect_rescue_2(user, s):
    rescue = s["rescue_direction"]
    original = opposite(rescue)
    pos = position_by_side(user, rescue)
    if not pos:
        return {"ok": False, "error": "沒有新反向2單位"}

    entry = pos["entry"]
    s["rescue_entry"] = entry
    tp20_price = price_by_pnl(entry, rescue, 20)

    r1 = trigger_close_order(user, rescue, 20)
    r2 = trigger_open_order(user, original, 2, tp20_price)

    return {"stage": "RESCUE_2", "tp20_close_rescue2": r1, "tp20_open_original2": r2}


def protect_final_4(user, s):
    main = s["main_direction"]
    pos = position_by_side(user, main)
    if not pos:
        return {"ok": False, "error": "沒有最終4單位"}

    r1 = trigger_close_order(user, main, 5)
    r2 = trigger_close_order(user, main, -50)

    return {"stage": "FINAL_4", "tp5_close_final4": r1, "sl50_close_final4": r2}


def ensure_stage_protection(user, s):
    stage = s["stage"]

    if s.get("protection_stage") == stage:
        return {"ok": True, "message": "本階段已掛保護單"}

    cancel_all_open_orders(user)

    if stage == "FIRST_ENTRY":
        result = protect_first_entry(user, s)
    elif stage == "LEFT_2_LOSS":
        result = protect_left_2(user, s)
    elif stage == "RESCUE_2":
        result = protect_rescue_2(user, s)
    elif stage == "FINAL_4":
        result = protect_final_4(user, s)
    else:
        return {"ok": True, "message": "此階段不需掛保護單"}

    s["protection_stage"] = stage
    return result


def sync_state_by_positions(user, s):
    long_pos = position_by_side(user, "LONG")
    short_pos = position_by_side(user, "SHORT")
    stage = s["stage"]

    if not long_pos and not short_pos:
        if stage != "IDLE":
            cancel_all_open_orders(user)
            if stage == "FIRST_ENTRY":
                s["mistakes_today"] = s.get("mistakes_today", 0) + 1
            reset_state(s)
            return "全部平倉，回到IDLE"
        return "IDLE無持倉"

    main = s.get("main_direction")
    hedge = s.get("hedge_direction")

    if stage == "FIRST_ENTRY":
        if position_by_side(user, main) and position_by_side(user, hedge):
            cancel_all_open_orders(user)
            s["stage"] = "HEDGE_LOCK"
            s["protection_stage"] = None
            return "偵測到+10%反向鎖倉，進入HEDGE_LOCK"

    if stage == "LEFT_2_LOSS":
        if position_by_side(user, main) and position_by_side(user, hedge):
            cancel_all_open_orders(user)
            s["stage"] = "RESCUE_2"
            s["rescue_direction"] = hedge
            s["protection_stage"] = None
            return "偵測到-30%反向補2，進入RESCUE_2"

    if stage == "RESCUE_2":
        rescue = s.get("rescue_direction")
        if rescue:
            original = opposite(rescue)
            if position_by_side(user, original) and not position_by_side(user, rescue):
                cancel_all_open_orders(user)
                s["stage"] = "FINAL_4"
                s["main_direction"] = original
                s["hedge_direction"] = None
                s["protection_stage"] = None
                return "偵測到+20%平救援並回補原方向，進入FINAL_4"

    return "狀態同步完成"


def run_strategy_for_user(user_id, user):
    state = load_state()
    s = get_state(state, user_id)

    sync_msg = sync_state_by_positions(user, s)

    result = {
        "user": user_id,
        "stage": s["stage"],
        "sync": sync_msg,
        "action_taken": "WAIT"
    }

    if s.get("mistakes_today", 0) >= 2:
        result["reason"] = "今日判錯2次，停手"
        save_state(state)
        return result

    if s["stage"] in ["FIRST_ENTRY", "LEFT_2_LOSS", "RESCUE_2", "FINAL_4"]:
        result["protection"] = ensure_stage_protection(user, s)
        s["updated_at"] = int(time.time())
        save_state(state)
        return result

    sig = strategy_signal(user)
    result["signal"] = sig

    if s["stage"] == "IDLE":
        if position_by_side(user, "LONG") or position_by_side(user, "SHORT"):
            result["reason"] = "已有倉位，等待同步"
        elif sig["action"] in ["LONG", "SHORT"]:
            order = market_order(user, sig["action"], 1)

            if order["ok"]:
                s["stage"] = "FIRST_ENTRY"
                s["main_direction"] = sig["action"]
                s["hedge_direction"] = opposite(sig["action"])
                s["protection_stage"] = None
                result["action_taken"] = "第一筆試倉"
                result["order"] = order
                result["protection"] = ensure_stage_protection(user, s)

    elif s["stage"] == "HEDGE_LOCK":
        main = s["main_direction"]
        hedge = s["hedge_direction"]

        main_pnl = pnl_percent(user, main)
        reverse_ok = (
            sig["action"] == hedge
            and sig["confirm_15m"]
            and sig["one_h"]["score"] >= MIN_SCORE
        )

        result["main_pnl"] = main_pnl

        if main_pnl is not None and main_pnl >= 50 and reverse_ok:
            cancel_all_open_orders(user)
            close_win = close_position(user, main)
            add_reverse = market_order(user, hedge, 1)

            if close_win["ok"] and add_reverse["ok"]:
                s["stage"] = "LEFT_2_LOSS"
                s["main_direction"] = hedge
                s["hedge_direction"] = opposite(hedge)
                s["protection_stage"] = None
                result["action_taken"] = "原單+50%且技術轉向，平獲利單，加碼反向1單位"
                result["close_win"] = close_win
                result["add_reverse"] = add_reverse
                result["protection"] = ensure_stage_protection(user, s)

    s["updated_at"] = int(time.time())
    save_state(state)
    return result


def token_ok(user=None):
    token = request.args.get("token", "")
    return token == MASTER_TOKEN or (user and token == user.get("trade_token", ""))


@app.route("/")
def home():
    return "BTC BingX Bot 已啟動"


@app.route("/ping")
def ping():
    return "ok", 200


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


@app.route("/user/<user_id>/positions")
def user_positions(user_id):
    user = load_users().get(user_id)
    if not user:
        return jsonify({"ok": False, "error": "找不到 user"})
    return jsonify(get_positions_raw(user))


@app.route("/user/<user_id>/reset_state")
def user_reset_state(user_id):
    if request.args.get("token") != MASTER_TOKEN:
        return jsonify({"ok": False, "error": "token錯誤"})

    state = load_state()
    s = get_state(state, user_id)
    cancel_all_open_orders(load_users()[user_id])
    reset_state(s)
    save_state(state)

    return jsonify({"ok": True, "state": s})


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

    for user_id, user in load_users().items():
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
        for user_id, user in load_users().items():
            if not user.get("enabled", False):
                continue
            try:
                result = run_strategy_for_user(user_id, user)
                print("BOT_RESULT", user_id, result, flush=True)
            except Exception as e:
                print("BOT_ERROR", user_id, str(e), flush=True)
    except Exception as e:
        print("RUN_BOT_ERROR", str(e), flush=True)


@app.route("/cron204")
def cron204():
    if request.args.get("token") != MASTER_TOKEN:
        return Response("BAD", status=403, mimetype="text/plain")

    threading.Thread(target=run_bot_job).start()
    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
