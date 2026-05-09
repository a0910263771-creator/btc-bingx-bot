import os
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.getenv("BINGX_API_KEY")
API_SECRET = os.getenv("BINGX_API_SECRET")
TRADE_TOKEN = os.getenv("TRADE_TOKEN")
BASE_URL = "https://open-api.bingx.com"

SYMBOL = "BTC-USDT"
LEVERAGE = 1
RISK_DIVISOR = 10

def sign(params):
    query = urlencode(sorted(params.items()))
    return hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def bingx_get(path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": API_KEY}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bingx_post(path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-BX-APIKEY": API_KEY}
    r = requests.post(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def check_token():
    return TRADE_TOKEN and request.args.get("token") == TRADE_TOKEN

def get_available_usdt():
