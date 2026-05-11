"""
FNO Options Worksheet — Flask Backend
Angel One SmartAPI integration
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import pyotp
import datetime
import requests
import os

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
#  CONFIG — Set these as environment variables
#  (never hardcode credentials in source code)
# ─────────────────────────────────────────────
API_KEY     = os.environ.get("ANGEL_API_KEY", "")
CLIENT_ID   = os.environ.get("ANGEL_CLIENT_ID", "")
PASSWORD    = os.environ.get("ANGEL_PASSWORD", "")
TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET", "")

BASE_URL    = "https://apiconnect.angelbroking.com"
INSTRUMENT_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# ─────────────────────────────────────────────
#  Trade parameters
# ─────────────────────────────────────────────
TP_PCT     = 11.0
SL_PCT     = 6.0
BE_PCT     = 5.0

STRIKE_INTERVALS = {
    "NIFTY":50,"BANKNIFTY":100,"FINNIFTY":50,
    "RELIANCE":100,"TCS":100,"INFY":50,
    "HDFCBANK":50,"ICICIBANK":50,"AXISBANK":50,
    "SBIN":20,"TATAMOTORS":50,"MARUTI":500,
    "BAJFINANCE":100,"WIPRO":20,"LT":100,
    "NTPC":20,"ONGC":20,"COALINDIA":20,
    "ADANIENT":100,"JSWSTEEL":50,"TATASTEEL":20,
    "HINDALCO":20,"DIVISLAB":500,"CIPLA":50,
    "SUNPHARMA":50,"DRREDDY":100,"APOLLOHOSP":100,
    "HCLTECH":50,"BHARTIARTL":50,"TITAN":100,
    "ASIANPAINT":100,"KOTAKBANK":50,"INDUSINDBK":100,
    "BAJAJFINSV":50,"ULTRACEMCO":200,"HEROMOTOCO":100,
    "EICHERMOT":200,"ITC":10,"HINDUNILVR":100,
    "VEDL":20,"UPL":20,"TECHM":50,
}

# ── In-memory session cache (reuse auth token) ──
_session = {"token": None, "expiry": None}
_instruments_cache = None


# ════════════════════════════════════════════════
#  Auth helpers
# ════════════════════════════════════════════════
def get_headers(auth_token):
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "X-UserType":    "USER",
        "X-SourceID":    "WEB",
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress":  "00:00:00:00:00:00",
        "X-PrivateKey":  API_KEY,
    }


def login():
    global _session
    now = datetime.datetime.now()
    if _session["token"] and _session["expiry"] and now < _session["expiry"]:
        return _session["token"], None

    totp = pyotp.TOTP(TOTP_SECRET).now()
    payload = {
        "clientcode": CLIENT_ID,
        "password":   PASSWORD,
        "totp":       totp,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "X-UserType":   "USER",
        "X-SourceID":   "WEB",
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress": "00:00:00:00:00:00",
        "X-PrivateKey": API_KEY,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword",
            json=payload, headers=headers, timeout=10
        )
        data = r.json()
        if data.get("status"):
            token = data["data"]["jwtToken"]
            _session["token"]  = token
            _session["expiry"] = now + datetime.timedelta(hours=6)
            return token, None
        return None, data.get("message", "Login failed")
    except Exception as e:
        return None, str(e)


# ════════════════════════════════════════════════
#  Instrument master
# ════════════════════════════════════════════════
def get_instruments():
    global _instruments_cache
    if _instruments_cache:
        return _instruments_cache, None
    try:
        r = requests.get(INSTRUMENT_URL, timeout=20)
        _instruments_cache = r.json()
        return _instruments_cache, None
    except Exception as e:
        return None, str(e)


def find_token(instruments, symbol):
    for i in instruments:
        if (i.get("name","").upper() == symbol.upper()
                and i.get("exch_seg","") == "NSE"
                and i.get("symbol","").endswith("-EQ")):
            return i["token"], int(i.get("lotsize", 1))
    return None, 1


def find_option_token(instruments, symbol, strike, opt_type, expiry_str):
    target = f"{symbol}{expiry_str}{int(strike)}{opt_type}".upper()
    for i in instruments:
        if i.get("exch_seg","") == "NFO" and i.get("symbol","").upper() == target:
            return i["token"]
    # fallback partial match
    for i in instruments:
        sym = i.get("symbol","").upper()
        if (i.get("exch_seg","") == "NFO"
                and symbol.upper() in sym
                and str(int(strike)) in sym
                and opt_type in sym
                and expiry_str[:5] in sym):
            return i["token"]
    return None


# ════════════════════════════════════════════════
#  Market data helpers
# ════════════════════════════════════════════════
def get_spot_price(token, auth_token):
    headers = get_headers(auth_token)
    payload = {"exchange": "NSE", "tradingsymbol": "", "symboltoken": token}
    try:
        r = requests.post(
            f"{BASE_URL}/rest/secure/angelbroking/market/v1/getLTPData",
            json=payload, headers=headers, timeout=8
        )
        data = r.json()
        if data.get("status"):
            return float(data["data"]["ltp"]), None
        return None, data.get("message","LTP fetch failed")
    except Exception as e:
        return None, str(e)


def get_days_high(token, auth_token):
    headers = get_headers(auth_token)
    now   = datetime.datetime.now()
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    payload = {
        "exchange":    "NFO",
        "symboltoken": token,
        "interval":    "FIVE_MINUTE",
        "fromdate":    start.strftime("%Y-%m-%d %H:%M"),
        "todate":      now.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        r = requests.post(
            f"{BASE_URL}/rest/secure/angelbroking/historical/v1/getCandleData",
            json=payload, headers=headers, timeout=10
        )
        data = r.json()
        if data.get("status") and data.get("data"):
            highs = [float(c[2]) for c in data["data"]]
            return max(highs), None
        return None, "No candle data"
    except Exception as e:
        return None, str(e)


# ════════════════════════════════════════════════
#  Expiry & strike helpers
# ════════════════════════════════════════════════
def get_monthly_expiry():
    import calendar
    today = datetime.date.today()

    def last_thursday(y, m):
        last = calendar.monthrange(y, m)[1]
        d = datetime.date(y, m, last)
        while d.weekday() != 3:
            d -= datetime.timedelta(days=1)
        return d

    exp = last_thursday(today.year, today.month)
    if today > exp:
        exp = last_thursday(today.year, today.month % 12 + 1) if today.month < 12 \
              else last_thursday(today.year + 1, 1)
    return exp


def trade_levels(entry):
    tp   = round(entry * (1 + TP_PCT/100), 2)
    sl   = round(entry * (1 - SL_PCT/100), 2)
    be   = round(entry * (1 + BE_PCT/100), 2)
    trail_rows = []
    for p in [5, 8, 10, 12, 14, 16, 18, 20]:
        price    = round(entry * (1 + p/100), 2)
        sl_lock  = 0 if p == BE_PCT else p * 0.5
        sl_price = round(entry * (1 + sl_lock/100), 2)
        trail_rows.append({
            "profit_pct": p,
            "price":      price,
            "sl_lock_pct": sl_lock,
            "sl_price":   sl_price,
            "pts_saved":  round(sl_price - entry, 2),
            "is_be":      p == BE_PCT,
        })
    return {"entry": entry, "tp": tp, "sl": sl, "be": be, "trail": trail_rows}


# ════════════════════════════════════════════════
#  Routes
# ════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def scan():
    body   = request.json or {}
    symbol = body.get("symbol","").strip().upper()
    if not symbol:
        return jsonify({"ok": False, "error": "Symbol is required"}), 400

    # 1 — Login
    auth_token, err = login()
    if err:
        return jsonify({"ok": False, "error": f"Login failed: {err}"}), 500

    # 2 — Instruments
    instruments, err = get_instruments()
    if err:
        return jsonify({"ok": False, "error": f"Instrument load failed: {err}"}), 500

    # 3 — Equity token
    token, lot_size = find_token(instruments, symbol)
    if not token:
        return jsonify({"ok": False, "error": f"'{symbol}' not found. Check the symbol."}), 404

    # 4 — Spot price
    spot, err = get_spot_price(token, auth_token)
    if not spot:
        return jsonify({"ok": False, "error": f"Spot price fetch failed: {err}"}), 500

    # 5 — Strikes & expiry
    interval  = STRIKE_INTERVALS.get(symbol, 100)
    base      = int(spot // interval) * interval
    ce_strike = base
    pe_strike = base + interval
    expiry    = get_monthly_expiry()
    exp_angel = expiry.strftime("%d%b%Y").upper()
    exp_disp  = expiry.strftime("%d %b %Y")

    # 6 — Option day's high
    ce_token = find_option_token(instruments, symbol, ce_strike, "CE", exp_angel)
    pe_token = find_option_token(instruments, symbol, pe_strike, "PE", exp_angel)

    ce_high, ce_err = (get_days_high(ce_token, auth_token) if ce_token else (None, "Token not found"))
    pe_high, pe_err = (get_days_high(pe_token, auth_token) if pe_token else (None, "Token not found"))

    return jsonify({
        "ok":        True,
        "symbol":    symbol,
        "spot":      spot,
        "lot_size":  lot_size,
        "expiry":    exp_disp,
        "ce_strike": ce_strike,
        "pe_strike": pe_strike,
        "ce_high":   ce_high,
        "pe_high":   pe_high,
        "ce_err":    ce_err,
        "pe_err":    pe_err,
        "ce_levels": trade_levels(ce_high) if ce_high else None,
        "pe_levels": trade_levels(pe_high) if pe_high else None,
        "timestamp": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
    })


@app.route("/api/override", methods=["POST"])
def override():
    """Manual entry if auto-fetch fails."""
    body      = request.json or {}
    symbol    = body.get("symbol","").upper()
    ce_high   = body.get("ce_high")
    pe_high   = body.get("pe_high")
    ce_strike = body.get("ce_strike")
    pe_strike = body.get("pe_strike")
    expiry    = body.get("expiry","")
    lot_size  = body.get("lot_size", 1)
    spot      = body.get("spot", 0)

    return jsonify({
        "ok":        True,
        "symbol":    symbol,
        "spot":      spot,
        "lot_size":  lot_size,
        "expiry":    expiry,
        "ce_strike": ce_strike,
        "pe_strike": pe_strike,
        "ce_high":   ce_high,
        "pe_high":   pe_high,
        "ce_err":    None,
        "pe_err":    None,
        "ce_levels": trade_levels(float(ce_high)) if ce_high else None,
        "pe_levels": trade_levels(float(pe_high)) if pe_high else None,
        "timestamp": datetime.datetime.now().strftime("%d %b %Y, %I:%M %p"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
