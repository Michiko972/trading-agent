"""
Agent de Trading Automatique v2.3
Architecture : Pine Script = capteur, Agent IA = décideur
Broker : Capital.com (démo)

Version :
- Une seule position
- TP fixe basé sur objectif €
- SL fixe basé sur risque €
- Gestion type prop firm
- Fermeture anticipée sur mèche retournement
"""

import os
import json
import logging
import requests

from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ══════════════════════════════════════════════
# CONFIGURATION CAPITAL.COM API
# ══════════════════════════════════════════════

API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()

API_URL = "https://demo-api-capital.backend-capital.com/api/v1"

DEFAULT_EPIC = "BTCUSD"

# ══════════════════════════════════════════════
# OBJECTIFS PROP FIRM
# ══════════════════════════════════════════════

CAPITAL_DEMO = 1000.0

# Objectif journalier
DAILY_TARGET_EUR = 20

# Risque max par trade
RISK_PER_TRADE_EUR = 13

DAILY_LOSS_LIMIT = 40
MAX_DRAWDOWN_PCT = 0.04
PROFIT_TARGET = 60

# Ratio TP / SL
TP_RATIO = DAILY_TARGET_EUR / RISK_PER_TRADE_EUR

# Détection retournement
WICK_THRESHOLD = 0.6

# Distance stop minimale
MIN_STOP_DISTANCE_PCT = 0.003

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

log.info(f"API KEY loaded: {'YES' if API_KEY else 'NO'}")
log.info(f"EMAIL loaded: {'YES' if API_EMAIL else 'NO'}")
log.info(f"PASSWORD loaded: {'YES' if API_PASSWORD else 'NO'}")

# ══════════════════════════════════════════════
# ÉTAT DU COMPTE
# ══════════════════════════════════════════════

class AccountState:

    def __init__(self):

        self.capital = CAPITAL_DEMO
        self.peak_equity = CAPITAL_DEMO

        self.daily_pnl = 0.0
        self.total_pnl = 0.0

        self.trades_today = 0

        self.position_open = False
        self.position_side = None

        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0

        self.last_day = datetime.now(timezone.utc).date()

    def reset_daily(self):

        today = datetime.now(timezone.utc).date()

        if today != self.last_day:

            self.daily_pnl = 0.0
            self.trades_today = 0
            self.last_day = today

            log.info("Reset journalier")

    def can_trade(self):

        self.reset_daily()

        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            return False, "daily_loss_limit"

        if self.total_pnl >= PROFIT_TARGET:
            return False, "profit_target_reached"

        if self.position_open:
            return False, "position_already_open"

        return True, "ok"

    def update_after_trade(self, pnl):

        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.capital += pnl

        self.trades_today += 1

        log.info(
            f"Trade fermé | "
            f"PnL: {pnl:+.2f}€ | "
            f"Jour: {self.daily_pnl:+.2f}€ | "
            f"Total: {self.total_pnl:+.2f}€"
        )

state = AccountState()

# ══════════════════════════════════════════════
# MOTEUR DE DÉCISION
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

    if not API_KEY or not API_EMAIL or not API_PASSWORD:

        log.error("Variables API manquantes")

        return None, None

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

        cst = response.headers.get("CST")
        xst = response.headers.get("X-SECURITY-TOKEN")

        return cst, xst

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
# CALCUL POSITION SIZE
# ══════════════════════════════════════════════

def calculate_position_size(entry_price, stop_price):

    distance = abs(entry_price - stop_price)

    if distance <= 0:
        return 0.1

    size = RISK_PER_TRADE_EUR / distance

    size = round(size, 2)

    return max(0.1, size)

# ══════════════════════════════════════════════
# STOP LOSS
# ══════════════════════════════════════════════

def calculate_stop(signal, price):

    stop_distance = price * MIN_STOP_DISTANCE_PCT

    if signal == "long":
        return price - stop_distance

    return price + stop_distance

# ══════════════════════════════════════════════
# TAKE PROFIT
# ══════════════════════════════════════════════

def calculate_take_profit(direction, entry_price, stop_price):

    distance = abs(entry_price - stop_price)

    tp_distance = distance * TP_RATIO

    if direction == "long":
        return entry_price + tp_distance

    return entry_price - tp_distance

# ══════════════════════════════════════════════
# DÉTECTION MÈCHE RETOURNEMENT
# ══════════════════════════════════════════════

def detect_reversal_wick(data):

    signal = data.get("signal", "")

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

    bearish_candle = close_price < open_price
    bullish_candle = close_price > open_price

    if signal == "long":

        if upper_ratio >= WICK_THRESHOLD and bearish_candle:

            log.info("Mèche haute retournement détectée")

            return True

    if signal == "short":

        if lower_ratio >= WICK_THRESHOLD and bullish_candle:

            log.info("Mèche basse retournement détectée")

            return True

    return False

# ══════════════════════════════════════════════
# OUVERTURE POSITION
# ══════════════════════════════════════════════

def open_position(direction, entry_price, stop_price, epic=DEFAULT_EPIC):

    headers = get_headers()

    if not headers:
        return False

    size = calculate_position_size(entry_price, stop_price)

    take_profit = calculate_take_profit(
        direction,
        entry_price,
        stop_price
    )

    payload = {
        "epic": epic,
        "direction": "BUY" if direction == "long" else "SELL",
        "size": size,
        "guaranteedStop": False,
        "stopLevel": round(stop_price, 2),
        "profitLevel": round(take_profit, 2)
    }

    log.info(
        f"OUVERTURE TRADE | "
        f"Size={size} | "
        f"SL={round(stop_price, 2)} | "
        f"TP={round(take_profit, 2)}"
    )

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

        state.position_open = True
        state.position_side = direction

        state.entry_price = entry_price
        state.stop_loss = stop_price
        state.take_profit = take_profit

        log.info("Position ouverte avec succès")

        return True

    except Exception as e:

        log.error(f"Exception ouverture position: {e}")

        return False

# ══════════════════════════════════════════════
# FERMETURE POSITION
# ══════════════════════════════════════════════

def close_all_positions(epic=DEFAULT_EPIC):

    headers = get_headers()

    if not headers:
        return False

    try:

        response = requests.get(
            f"{API_URL}/positions",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            return False

        positions = response.json().get("positions", [])

        for pos in positions:

            if pos["market"]["epic"] == epic:

                deal_id = pos["position"]["dealId"]

                close_response = requests.delete(
                    f"{API_URL}/positions/{deal_id}",
                    headers=headers,
                    timeout=10
                )

                if close_response.status_code == 200:

                    pnl = pos["position"].get("upl", 0)

                    state.update_after_trade(pnl)

                    state.position_open = False

                    log.info(f"Position fermée | PnL: {pnl}")

                    return True

        return False

    except Exception as e:

        log.error(f"Exception fermeture position: {e}")

        return False

# ══════════════════════════════════════════════
# FLASK WEBHOOK
# ══════════════════════════════════════════════

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        data = request.get_json(force=True)

        log.info(f"Signal reçu: {json.dumps(data)}")

        # Fermeture retournement

        if state.position_open:

            if detect_reversal_wick(data):

                close_all_positions()

                return jsonify({
                    "status": "position_closed_reversal"
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

            stop = calculate_stop(signal, price)

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

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "Agent Trading actif",
        "version": "2.3"
    }), 200

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port)
