"""
Agent de Trading Automatique v4.0
Architecture : Pine Script = capteur, Agent IA = décideur
Broker : Capital.com (démo)

Version :
- API officielle uniquement
- Une seule position
- TP fixe classique
- SL classique
- Fermeture anticipée si forte mèche de retournement
"""

import os
import json
import logging
import requests

from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ══════════════════════════════════════════════
# CONFIGURATION API CAPITAL.COM
# ══════════════════════════════════════════════

API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()

API_URL = "https://demo-api-capital.backend-capital.com/api/v1"

DEFAULT_EPIC = "BTCUSD"

# ══════════════════════════════════════════════
# RISK MANAGEMENT
# ══════════════════════════════════════════════

CAPITAL_DEMO = 1000.0

RISK_PCT = 0.01

DAILY_LOSS_LIMIT = 0.02
MAX_DRAWDOWN_PCT = 0.04
PROFIT_TARGET = 0.06

TP_RATIO = 2.0

# Ratio mèche / taille totale bougie
WICK_THRESHOLD = 0.6

# ══════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("agent.log")
    ]
)

log = logging.getLogger(__name__)

SESSION_CST = None
SESSION_XST = None

# ══════════════════════════════════════════════
# ACCOUNT STATE
# ══════════════════════════════════════════════

class AccountState:

    def __init__(self):

        self.capital = CAPITAL_DEMO
        self.peak_equity = CAPITAL_DEMO

        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.best_day_pnl = 0.0

        self.trades_today = 0

        self.position_side = None
        self.position_size = 0.0

        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0

        self.last_day = datetime.now(timezone.utc).date()

    def reset_daily(self):

        today = datetime.now(timezone.utc).date()

        if today != self.last_day:

            if self.daily_pnl > self.best_day_pnl:
                self.best_day_pnl = self.daily_pnl

            self.daily_pnl = 0.0
            self.trades_today = 0
            self.last_day = today

            log.info("Reset journalier")

    def can_trade(self):

        self.reset_daily()

        if self.daily_pnl <= -(CAPITAL_DEMO * DAILY_LOSS_LIMIT):
            return False, "daily_loss_limit"

        drawdown = self.peak_equity - self.capital

        if drawdown >= CAPITAL_DEMO * MAX_DRAWDOWN_PCT:
            return False, "max_drawdown"

        if self.total_pnl >= CAPITAL_DEMO * PROFIT_TARGET:
            return False, "profit_target_reached"

        if has_open_position():
            return False, "position_already_open"

        return True, "ok"

state = AccountState()

# ══════════════════════════════════════════════
# DECISION ENGINE
# ══════════════════════════════════════════════

class TradingDecisionEngine:

    def analyze(self, data):

        signal = data.get("signal", "")

        adx = float(data.get("adx", 0))
        adx_rising = data.get("adx_rising", False)

        last_pivot = data.get("last_pivot", "")

        di_plus = float(data.get("di_plus", 0))
        di_minus = float(data.get("di_minus", 0))

        score = 0

        if adx > 20:
            score += 30

        if adx_rising:
            score += 20

        if signal == "long" and last_pivot == "low":
            score += 30

        elif signal == "short" and last_pivot == "high":
            score += 30

        else:
            score += 10

        if signal == "long" and di_plus > di_minus:
            score += 20

        elif signal == "short" and di_minus > di_plus:
            score += 20

        if score < 50:
            return False, "low_confidence", score, "Score insuffisant"

        return True, "entry_validated", score, "Setup validé"

engine = TradingDecisionEngine()

# ══════════════════════════════════════════════
# SESSION API
# ══════════════════════════════════════════════

def get_session():

    global SESSION_CST
    global SESSION_XST

    if SESSION_CST and SESSION_XST:
        return SESSION_CST, SESSION_XST

    try:

        response = requests.post(
            f"{API_URL}/session",
            headers={
                "X-CAP-API-KEY": API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "identifier": API_EMAIL,
                "password": API_PASSWORD,
                "encryptedPassword": False
            },
            timeout=10
        )

        if response.status_code != 200:

            log.error(
                f"Erreur session API | "
                f"{response.status_code} | "
                f"{response.text}"
            )

            return None, None

        SESSION_CST = response.headers.get("CST")
        SESSION_XST = response.headers.get("X-SECURITY-TOKEN")

        log.info("Nouvelle session API ouverte")

        return SESSION_CST, SESSION_XST

    except Exception as e:

        log.error(f"Exception session API: {e}")

        return None, None

def get_headers():

    cst, xst = get_session()

    if not cst or not xst:
        return None

    return {
        "X-CAP-API-KEY": API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": xst,
        "Content-Type": "application/json"
    }

# ══════════════════════════════════════════════
# POSITIONS
# ══════════════════════════════════════════════

def get_open_positions():

    headers = get_headers()

    if not headers:
        return []

    try:

        response = requests.get(
            f"{API_URL}/positions",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            return []

        return response.json().get("positions", [])

    except Exception as e:

        log.error(f"Exception récupération positions: {e}")

        return []

def has_open_position():

    positions = get_open_positions()

    return len(positions) > 0

# ══════════════════════════════════════════════
# POSITION SIZE
# ══════════════════════════════════════════════

def calculate_position_size(entry_price, stop_price):

    risk_amount = state.capital * RISK_PCT

    distance = abs(entry_price - stop_price)

    if distance == 0:
        return 0.0001

    size = round(risk_amount / distance, 4)

    return max(0.0001, size)

# ══════════════════════════════════════════════
# STOP LOSS
# ══════════════════════════════════════════════

def calculate_stop(signal, price, data):

    atr_pct = 0.015

    if signal == "long":

        pivot_low = float(data.get("pivot_low", 0))

        if pivot_low > 0:
            return pivot_low * 0.998

        return price * (1 - atr_pct)

    else:

        pivot_high = float(data.get("pivot_high", 0))

        if pivot_high > 0:
            return pivot_high * 1.002

        return price * (1 + atr_pct)

# ══════════════════════════════════════════════
# OPEN POSITION
# ══════════════════════════════════════════════

def open_position(direction, entry_price, stop_price, epic=DEFAULT_EPIC):

    headers = get_headers()

    if not headers:
        return False

    size = calculate_position_size(entry_price, stop_price)

    dist = abs(entry_price - stop_price)

    if direction == "long":
        take_profit = entry_price + dist * TP_RATIO
    else:
        take_profit = entry_price - dist * TP_RATIO

    payload = {
        "epic": epic,
        "direction": "BUY" if direction == "long" else "SELL",
        "size": size,
        "guaranteedStop": False,
        "stopLevel": round(stop_price, 2),
        "profitLevel": round(take_profit, 2)
    }

    try:

        response = requests.post(
            f"{API_URL}/positions",
            headers=headers,
            json=payload,
            timeout=10
        )

        if response.status_code != 200:

            log.error(
                f"Erreur ouverture position | "
                f"{response.status_code} | "
                f"{response.text}"
            )

            return False

        state.position_side = direction
        state.position_size = size

        state.entry_price = entry_price
        state.stop_loss = stop_price
        state.take_profit = take_profit

        log.info(
            f"Trade ouvert | "
            f"{direction.upper()} | "
            f"{epic}"
        )

        return True

    except Exception as e:

        log.error(f"Exception ouverture position: {e}")

        return False

# ══════════════════════════════════════════════
# DÉTECTION MÈCHE RETOURNEMENT
# ══════════════════════════════════════════════

def detect_reversal_wick(data):

    signal = data.get("signal")

    open_price = float(data.get("open", 0))
    close_price = float(data.get("close", 0))
    high_price = float(data.get("high", 0))
    low_price = float(data.get("low", 0))

    candle_size = high_price - low_price

    if candle_size <= 0:
        return False

    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price

    upper_ratio = upper_wick / candle_size
    lower_ratio = lower_wick / candle_size

    bearish_confirmation = close_price < open_price
    bullish_confirmation = close_price > open_price

    # LONG -> rejet en haut

    if signal == "long":

        if upper_ratio >= WICK_THRESHOLD and bearish_confirmation:
            return True

    # SHORT -> rejet en bas

    if signal == "short":

        if lower_ratio >= WICK_THRESHOLD and bullish_confirmation:
            return True

    return False

# ══════════════════════════════════════════════
# CLOSE POSITIONS
# ══════════════════════════════════════════════

def close_all_positions():

    headers = get_headers()

    if not headers:
        return False

    try:

        positions = get_open_positions()

        if not positions:
            return False

        for pos in positions:

            deal_id = pos["position"]["dealId"]

            response = requests.delete(
                f"{API_URL}/positions/{deal_id}",
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                log.info("Position fermée")

        return True

    except Exception as e:

        log.error(f"Exception fermeture positions: {e}")

        return False

# ══════════════════════════════════════════════
# FLASK APP
# ══════════════════════════════════════════════

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        data = request.get_json(force=True)

        # Fermeture anticipée

        if has_open_position():

            if detect_reversal_wick(data):

                log.info("Mèche de retournement détectée")

                close_all_positions()

                return jsonify({
                    "status": "position_closed_reversal"
                }), 200

            return jsonify({
                "status": "position_already_open"
            }), 200

        can_trade, reason = state.can_trade()

        if not can_trade:

            return jsonify({
                "status": "blocked",
                "reason": reason
            }), 200

        should_enter, reason, score, message = engine.analyze(data)

        if should_enter:

            signal = data.get("signal")

            price = float(data.get("price", 0))

            stop = calculate_stop(signal, price, data)

            epic = data.get("epic", DEFAULT_EPIC)

            success = open_position(
                signal,
                price,
                stop,
                epic
            )

            return jsonify({
                "status": "trade_opened" if success else "order_failed",
                "score": score,
                "message": message
            }), 200

        return jsonify({
            "status": "no_trade",
            "reason": reason,
            "score": score
        }), 200

    except Exception as e:

        log.error(f"Webhook error: {e}")

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ══════════════════════════════════════════════
# STATUS
# ══════════════════════════════════════════════

@app.route("/status", methods=["GET"])
def status():

    positions = get_open_positions()

    return jsonify({
        "capital": round(state.capital, 2),
        "daily_pnl": round(state.daily_pnl, 2),
        "total_pnl": round(state.total_pnl, 2),
        "open_positions": len(positions),
        "position_side": state.position_side
    }), 200

# ══════════════════════════════════════════════
# CLOSE
# ══════════════════════════════════════════════

@app.route("/close", methods=["POST"])
def close():

    success = close_all_positions()

    return jsonify({
        "status": "closed" if success else "no_position"
    }), 200

# ══════════════════════════════════════════════
# HOME
# ══════════════════════════════════════════════

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "Agent Trading actif",
        "mode": "DEMO",
        "version": "4.0"
    }), 200

# ══════════════════════════════════════════════
# START
# ══════════════════════════════════════════════

if __name__ == "__main__":

    log.info("=" * 50)
    log.info("AGENT TRADING IA — MODE DEMO")
    log.info("=" * 50)

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port)
