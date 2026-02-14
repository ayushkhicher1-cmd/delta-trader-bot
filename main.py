from flask import Flask, request
import time, hmac, hashlib, json, requests, os

API_KEY    = os.environ.get("DELTA_API_KEY")
API_SECRET = os.environ.get("DELTA_SECRET")

BASE_URL = "https://api.delta.exchange"

BASE_CAPITAL = 20.0
RISK_PERCENT = 2.0
ONE_TRADE_ONLY = True
KILL_SWITCH = False

app = Flask(__name__)

LAST_SIGNAL = {"sig": None, "time": 0}
PRODUCT_CACHE = {}
PRODUCT_META  = {}

# ================= LOGGER =================
def log(msg):
    print(msg)

# ================= SIGN =================
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

# ================= PRELOAD PRODUCTS =================
def load_products():
    try:
        res = requests.get(BASE_URL + "/products").json()

        for p in res.get("result", []):
            sym = p["symbol"].upper()

            PRODUCT_CACHE[sym] = int(p["id"])

            # 🔥 Step size meta
            step = float(p.get("contract_value", 0.001))
            PRODUCT_META[sym] = {
                "id": int(p["id"]),
                "step": step
            }

        log(f"Loaded {len(PRODUCT_CACHE)} products into cache")

    except Exception as e:
        log("Product preload failed: " + str(e))

# ================= GET PRODUCT ID =================
def get_product_id(tv_symbol):
    tv_symbol = tv_symbol.upper().replace(".P","")
    pid = PRODUCT_CACHE.get(tv_symbol)

    if pid:
        return int(pid)

    log(f"Product not found in cache: {tv_symbol}")
    return tv_symbol

# ================= ALIGN QTY =================
def align_qty(symbol, qty):

    symbol = symbol.upper().replace(".P","")
    meta = PRODUCT_META.get(symbol)

    if not meta:
        return qty

    step = meta["step"]

    aligned = (qty // step) * step
    return float(aligned)

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
            if int(pos["product_id"]) == int(symbol):
                return float(pos["size"])
    except:
        pass
    return 0.0

# ================= ORDER =================
def place_order(payload):
    path = "/orders"
    body = json.dumps(payload)
    headers = sign("POST", path, body)
    return requests.post(BASE_URL + path, headers=headers, data=body).json()

# ================= EXECUTION =================
def execute(symbol, side, entry, sl, tp):

    global LAST_SIGNAL

    if KILL_SWITCH:
        log("KILL SWITCH ENABLED")
        return

    current_sig = f"{symbol}-{side}-{entry}-{sl}-{tp}"
    now = time.time()

    if current_sig == LAST_SIGNAL["sig"] and now - LAST_SIGNAL["time"] < 60:
        log("Duplicate ignored")
        return

    LAST_SIGNAL["sig"] = current_sig
    LAST_SIGNAL["time"] = now

    product_id = get_product_id(symbol)

    if ONE_TRADE_ONLY:
        pos = get_position(product_id)
        if abs(pos) > 0:
            log("Trade blocked – position already open")
            return

    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        log("Invalid SL")
        return

    balance = get_balance()
    if balance <= 0:
        log("Balance error")
        return

    effective_capital = min(balance, BASE_CAPITAL)
    max_risk = effective_capital * (RISK_PERCENT / 100)

    qty = max_risk / risk_per_unit
    qty = align_qty(symbol, qty)

    log(f"PID={product_id} Balance={balance} Qty={qty}")

    delta_side = "buy" if side == "LONG" else "sell"
    opposite   = "sell" if side == "LONG" else "buy"

    # ===== ENTRY =====
    entry_payload = {
        "product_id": int(product_id),
        "size": round(qty,4),
        "side": delta_side,
        "order_type": "market"
    }

    res = place_order(entry_payload)
    log("ENTRY: " + str(res))

    # 🔥 STRONG ENTRY VERIFY
    state = res.get("result", {}).get("state")
    if not res.get("success") or state not in ["open","filled"]:
        log("Entry rejected — aborting SL/TP")
        return

    # ===== WAIT FOR POSITION CONFIRMATION =====
    filled = False
    for _ in range(10):
        pos = get_position(product_id)
        if abs(pos) > 0:
            filled = True
            break
        time.sleep(0.2)

    if not filled:
        log("Position not confirmed — aborting SL/TP")
        return

    # ===== SL =====
    sl_payload = {
        "product_id": int(product_id),
        "size": round(qty,4),
        "side": opposite,
        "order_type": "stop_market",
        "stop_price": round(sl,2),
        "reduce_only": True
    }

    res2 = place_order(sl_payload)
    log("SL: " + str(res2))

    # ===== TP =====
    tp_payload = {
        "product_id": int(product_id),
        "size": round(qty,4),
        "side": opposite,
        "order_type": "limit",
        "limit_price": round(tp,2),
        "reduce_only": True
    }

    res3 = place_order(tp_payload)
    log("TP: " + str(res3))

# ================= WEBHOOK =================
@app.route("/", methods=["POST"])
def webhook():
    try:
        raw = request.data.decode().strip()

        if raw == "" or "|" not in raw:
            log("Ignored empty/non-strategy alert")
            return "IGNORED"

        parts = raw.split("|")

        if len(parts) < 6:
            log("Invalid alert format")
            return "BAD"

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
    load_products()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
