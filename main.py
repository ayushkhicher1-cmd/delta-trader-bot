from flask import Flask, request
import time, hmac, hashlib, json, requests, os

# ========== USER SETTINGS ==========
API_KEY    = os.environ.get("DELTA_API_KEY")
API_SECRET = os.environ.get("DELTA_SECRET")

BASE_URL = "https://api.delta.exchange"

CAPITAL = 20.0
RISK_PERCENT = 2.0
MAX_RISK = CAPITAL * (RISK_PERCENT / 100)

ONE_TRADE_ONLY = True
KILL_SWITCH = False

current_position = None

app = Flask(__name__)

# =================================

def log(msg):
    with open("trades.log","a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
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

def execute(symbol, side, entry, sl, tp):
    global current_position

    if KILL_SWITCH:
        log("❌ KILL SWITCH ENABLED")
        return

    if ONE_TRADE_ONLY and current_position:
        log("⚠️ Trade blocked – position already open")
        return

    risk = abs(entry - sl)
    if risk <= 0:
        log("❌ Invalid SL")
        return

    qty = MAX_RISK / risk
    log(f"Signal {symbol} {side} Entry={entry} SL={sl} TP={tp}")
    log(f"Risk=${MAX_RISK} → Qty={qty}")

    delta_side = "buy" if side == "LONG" else "sell"
    res = place_market(symbol, delta_side, qty)

    log("Delta: " + str(res))

    if "id" in str(res):
        current_position = symbol

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)