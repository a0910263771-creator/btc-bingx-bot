import time
import hmac
import hashlib
import requests

API_KEY = "DyI5UFtDsDyeXtdF7fwxfgorc4JkrfUjqZdAI0vfpV8PVwXnlGU2CmvuNDvcmCTIUUoPKoh0sXiDfNPLVrNg"
SECRET_KEY = "p96JagTP7WynYX8xuhPbF0djW65cIBjQH2w2zXQgLTOEPMGlAKFw4eZDl8fN1KyfNgi28TdzhA5KmMeEJA"

BASE_URL = "https://open-api.bingx.com"

symbol = "BTC-USDT"

# ===== 下單參數 =====
side = "BUY"
positionSide = "LONG"

margin = 100   # 1單位USDT
leverage = 30

# ===== 設定槓桿 =====
path = "/openApi/swap/v2/trade/leverage"

params = {
    "symbol": symbol,
    "side": side,
    "leverage": leverage,
    "timestamp": int(time.time() * 1000)
}

query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
signature = hmac.new(
    SECRET_KEY.encode(),
    query.encode(),
    hashlib.sha256
).hexdigest()

url = f"{BASE_URL}{path}?{query}&signature={signature}"

headers = {
    "X-BX-APIKEY": API_KEY
}

res = requests.post(url, headers=headers)
print("槓桿設定:", res.json())

# ===== 市價下單 =====
path = "/openApi/swap/v2/trade/order"

params = {
    "symbol": symbol,
    "side": side,
    "positionSide": positionSide,
    "type": "MARKET",
    "quantity": margin,
    "timestamp": int(time.time() * 1000)
}

query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
signature = hmac.new(
    SECRET_KEY.encode(),
    query.encode(),
    hashlib.sha256
).hexdigest()

url = f"{BASE_URL}{path}?{query}&signature={signature}"

res = requests.post(url, headers=headers)

print("下單結果:")
print(res.json())
