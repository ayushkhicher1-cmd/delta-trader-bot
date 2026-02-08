from flask import Flask, request
import time, hmac, hashlib, json, requests, os

# ========== USER SETTINGS ==========
API_KEY    = os.environ.get("DELTA_API_KEY")
API_SECRET = os.environ.get("DELTA_SECRET")

BASE_URL = "https://api.delta.exchange"

BASE_CAPITAL = 20.0
RISK_PERCENT = 2.0
ONE_TRADE_ONLY = True
KILL_SWITCH = False

app = Flask(__name__)

# =================================

def log(msg):
    print(msg)

def sign(method, path, body=""):
    ts = str(int(time.time()))
    msg = method + ts + path + body
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {
        "api-key": API_KEY,
        "timestamp": ts,
        "signature": sig,
        "Content-Type": "application/json"
    }

# ================= ACCOUNT =================

def get_balance():
    path = "/wallet/balances"
    headers = sign("GET", path)
    res = requests.get(BASE_URL + path, headers=headers).json()

    try:
        for asset in res["result"]:
            if asset["asset_symbol"] == "USDT":
                return float(asset["balance"])
    except:
        pass

    return 0.0

def get_position(symbol):
    path = "/positions"
    headers = sign("GET", path)
    res = requests.get(BASE_URL + path, headers=headers).json()

    try:
        for pos in res["result"]:
            if pos["product_id"] == symbol:
                return float(pos["size"])
    except:
        pass

    return 0.0

# ================= ORDER =================

def place_market(symbol, side, size):
    path = "/orders"
    payload = {
        "product_id": symbol,
        "size": round(size,4),
        "side": side,
        "order_type": "market"
    }

    body = json.dumps(payload)
    headers = sign("POST", path, body)
    return requests.post(BASE_URL + path, headers=headers, data=body).json()

# ================= EXECUTION =================

def execute(symbol, side, entry, sl, tp):

    if KILL_SWITCH:
        log("❌ KILL SWITCH ENABLED")
        return

    # check open position
    if ONE_TRADE_ONLY:
        pos = get_position(symbol)
        if abs(pos) > 0:
            log("⚠️ Trade blocked – position already open")
            return

    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        log("❌ Invalid SL")
        return

    balance = get_balance()
    if balance <= 0:
        log("❌ Balance error")
        return

    # ===== HYBRID RISK MODEL =====
    effective_capital = min(balance, BASE_CAPITAL)
    max_risk = effective_capital * (RISK_PERCENT / 100)

    qty = max_risk / risk_per_unit

    log(f"Real Balance: {balance}")
    log(f"Effective Capital: {effective_capital}")
    log(f"Risk Used: {max_risk}")
    log(f"Qty: {qty}")

    delta_side = "buy" if side == "LONG" else "sell"
    res = place_market(symbol, delta_side, qty)

    log("Delta response: " + str(res))

# ================= WEBHOOK =================

@app.route("/", methods=["POST"])
def webhook():
    try:
        msg = request.data.decode()
        parts = msg.split("|")

        symbol = parts[1]
        side   = parts[2]
        entry  = float(parts[3].split("=")[1])
        sl     = float(parts[4].split("=")[1])
        tp     = float(parts[5].split("=")[1])

        execute(symbol, side, entry, sl, tp)
        return "OK"

    except Exception as e:
        log("Webhook error: " + str(e))
        return "ERR"

# ================= IP CHECK =================

@app.route("/ip")
def ip():
    try:
        return requests.get("https://api.ipify.org").text
    except:
        return "IP_ERROR"

# ================= RUN =================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))