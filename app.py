import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# =========================================================
# 環境變數
# =========================================================
API_KEY = os.getenv("BINGX_API_KEY", "")
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

# =========================================================
# 基本設定
# =========================================================
BASE_URL = "https://open-api.bingx.com"

SYMBOL = "BTC-USDT"

LEVERAGE = 30
QUANTITY = 0.001

# =========================================================
# 第一階段條件
# =========================================================

# 第一段：
# +20% 平倉
# -20% 加碼

PNL_TARGET = 0.20

# 30倍槓桿換算價格波動
PRICE_MOVE = PNL_TARGET / LEVERAGE

# 並單後：
# +5% 平倉
MERGED_TP = 0.05

# 並單後：
# -20% 市價停損
MERGED_SL = -0.20


# =========================================================
# 簽名
# =========================================================
def sign(query_string):

    return hmac.new(
        SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


# =========================================================
# POST
# =========================================================
def bingx_post(path, params):

    params["timestamp"] = int(time.time() * 1000)

    query = "&".join(
        [f"{k}={params[k]}" for k in sorted(params)]
    )

    signature = sign(query)

    url = (
        BASE_URL
        + path
        + "?"
        + query
        + "&signature="
        + signature
    )

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    response = requests.post(
        url,
        headers=headers
    )

    return response.json()


# =========================================================
# GET
# =========================================================
def bingx_get(path, params=None):

    if params is None:
        params = {}

    params["timestamp"] = int(time.time() * 1000)

    query = "&".join(
        [f"{k}={params[k]}" for k in sorted(params)]
    )

    signature = sign(query)

    url = (
        BASE_URL
        + path
        + "?"
        + query
        + "&signature="
        + signature
    )

    headers = {
        "X-BX-APIKEY": API_KEY
    }

    response = requests.get(
        url,
        headers=headers
    )

    return response.json()


# =========================================================
# 設定槓桿
# =========================================================
def set_leverage(side):

    path = "/openApi/swap/v2/trade/leverage"

    params = {
        "symbol": SYMBOL,
        "side": side,
        "leverage": LEVERAGE
    }

    return bingx_post(path, params)


# =========================================================
# 市價單
# =========================================================
def market_order(
    side,
    position_side,
    quantity
):

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity
    }

    return bingx_post(path, params)


# =========================================================
# 限價單
# =========================================================
def limit_order(
    side,
    position_side,
    price,
    quantity
):

    path = "/openApi/swap/v2/trade/order"

    params = {
        "symbol": SYMBOL,
        "side": side,
        "positionSide": position_side,
        "type": "LIMIT",
        "price": round(price, 1),
        "quantity": quantity,
        "timeInForce": "GTC"
    }

    return bingx_post(path, params)


# =========================================================
# 查持倉
# =========================================================
def get_positions():

    path = "/openApi/swap/v2/user/positions"

    params = {
        "symbol": SYMBOL
    }

    return bingx_get(path, params)


# =========================================================
# 平均成交價
# =========================================================
def get_avg_price(order_result):

    try:
        return float(
            order_result["data"]["order"]["avgPrice"]
        )

    except Exception:
        return None


# =========================================================
# 解析持倉
# =========================================================
def parse_positions(result):

    positions = result.get("data", [])

    long_pos = None
    short_pos = None

    for p in positions:

        if (
            p.get("symbol") == SYMBOL
            and p.get("positionSide") == "LONG"
        ):
            long_pos = p

        if (
            p.get("symbol") == SYMBOL
            and p.get("positionSide") == "SHORT"
        ):
            short_pos = p

    return long_pos, short_pos


# =========================================================
# 持倉數量
# =========================================================
def pos_qty(pos):

    if not pos:
        return 0

    return abs(
        float(pos.get("positionAmt", 0))
    )


# =========================================================
# 盈虧率
# =========================================================
def pos_pnl_rate(pos):

    if not pos:
        return 0

    unrealized = float(
        pos.get("unrealizedProfit", 0)
    )

    margin = float(
        pos.get("initialMargin", 0)
    )

    if margin == 0:
        return 0

    return unrealized / margin


# =========================================================
# 持倉均價
# =========================================================
def pos_avg(pos):

    return float(
        pos.get("avgPrice", 0)
    )


# =========================================================
# 首頁
# =========================================================
@app.route("/")
def home():

    return "ok"


# =========================================================
# 第一階段
# =========================================================
@app.route("/phase1")
def phase1():

    if not API_KEY or not SECRET_KEY:

        return jsonify({
            "ok": False,
            "error": "缺少 API KEY"
        })

    # 設定槓桿
    long_lev = set_leverage("BUY")
    short_lev = set_leverage("SELL")

    # 同時開多空
    long_order = market_order(
        "BUY",
        "LONG",
        QUANTITY
    )

    short_order = market_order(
        "SELL",
        "SHORT",
        QUANTITY
    )

    long_avg = get_avg_price(long_order)
    short_avg = get_avg_price(short_order)

    if not long_avg or not short_avg:

        return jsonify({
            "ok": False,
            "error": "無法取得成交均價",
            "long_order": long_order,
            "short_order": short_order
        })

    # =====================================================
    # +20% / -20%
    # =====================================================

    upper_price = long_avg * (
        1 + PRICE_MOVE
    )

    lower_price = short_avg * (
        1 - PRICE_MOVE
    )

    # 上方：
    # 平多 + 空單加碼

    close_long_limit = limit_order(
        "SELL",
        "LONG",
        upper_price,
        QUANTITY
    )

    add_short_limit = limit_order(
        "SELL",
        "SHORT",
        upper_price,
        QUANTITY
    )

    # 下方：
    # 平空 + 多單加碼

    close_short_limit = limit_order(
        "BUY",
        "SHORT",
        lower_price,
        QUANTITY
    )

    add_long_limit = limit_order(
        "BUY",
        "LONG",
        lower_price,
        QUANTITY
    )

    return jsonify({

        "ok": True,

        "strategy": "phase1_20_percent",

        "symbol": SYMBOL,

        "leverage": LEVERAGE,

        "quantity": QUANTITY,

        "long_avg": long_avg,

        "short_avg": short_avg,

        "upper_price": round(
            upper_price,
            1
        ),

        "lower_price": round(
            lower_price,
            1
        ),

        "long_order": long_order,

        "short_order": short_order,

        "close_long_limit": close_long_limit,

        "add_short_limit": add_short_limit,

        "close_short_limit": close_short_limit,

        "add_long_limit": add_long_limit
    })


# =========================================================
# 第6步驟監控
# =========================================================
@app.route("/monitor")
def monitor():

    positions_result = get_positions()

    long_pos, short_pos = parse_positions(
        positions_result
    )

    long_qty = pos_qty(long_pos)
    short_qty = pos_qty(short_pos)

    actions = []

    # =====================================================
    # 多單並單
    # =====================================================

    if long_qty >= QUANTITY * 2:

        pnl = pos_pnl_rate(long_pos)

        avg = pos_avg(long_pos)

        tp_price = avg * (
            1 + MERGED_TP / LEVERAGE
        )

        # -20% 市價停損
        if pnl <= MERGED_SL:

            actions.append({

                "type": "market_stop_long",

                "result": market_order(
                    "SELL",
                    "LONG",
                    long_qty
                )
            })

        # +5% 限價平倉
        else:

            actions.append({

                "type": "set_long_tp_5_percent",

                "price": round(
                    tp_price,
                    1
                ),

                "result": limit_order(
                    "SELL",
                    "LONG",
                    tp_price,
                    long_qty
                )
            })

    # =====================================================
    # 空單並單
    # =====================================================

    if short_qty >= QUANTITY * 2:

        pnl = pos_pnl_rate(short_pos)

        avg = pos_avg(short_pos)

        tp_price = avg * (
            1 - MERGED_TP / LEVERAGE
        )

        # -20% 市價停損
        if pnl <= MERGED_SL:

            actions.append({

                "type": "market_stop_short",

                "result": market_order(
                    "BUY",
                    "SHORT",
                    short_qty
                )
            })

        # +5% 限價平倉
        else:

            actions.append({

                "type": "set_short_tp_5_percent",

                "price": round(
                    tp_price,
                    1
                ),

                "result": limit_order(
                    "BUY",
                    "SHORT",
                    tp_price,
                    short_qty
                )
            })

    return jsonify({

        "ok": True,

        "stage": "monitor_phase6",

        "long_qty": long_qty,

        "short_qty": short_qty,

        "actions": actions,

        "positions": positions_result
    })


# =========================================================
# 檢查 KEY
# =========================================================
@app.route("/check_key")
def check_key():

    return jsonify({

        "API_KEY_EXISTS": bool(API_KEY),

        "SECRET_KEY_EXISTS": bool(SECRET_KEY)
    })


# =========================================================
# 啟動
# =========================================================
if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
