"""
Agent Trading IA v3.0 — Adaptatif
TradingView -> Railway -> Capital.com
"""

import os
import json
import logging
import requests

from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ==========================================
# CONFIG
# ==========================================

API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()

API_URL = "https://demo-api-capital.backend-capital.com/api/v1"

DEFAULT_EPIC = "BTCUSD"

EPIC_MAP = {
    "NASDAQ": "US100",
    "NAS100": "US100",
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
    "EURUSD": "EURUSD",
    "USDJPY": "USDJPY",
    "GBPUSD": "GBPUSD",
}

CAPITAL_DEMO = 1000.0
RISK_PCT = 0.01
TP_RATIO = 1.5

# ==========================================
# LOGS
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger(__name__)

# ==========================================
# STATE
# ==========================================

class AccountState:

    def __init__(self):

        self.capital = CAPITAL_DEMO

        self.position_open = False
        self.position_side = None

        self.last_trade_time = None

    def can_trade(self):

        if self.last_trade_time:

            elapsed = (
                datetime.now(timezone.utc)
                - self.last_trade_time
            ).total_seconds()

            if elapsed < 120:
                return False, "cooldown"

        return True, "ok"

state = AccountState()

# ==========================================
# DIAGNOSTIC DEMARRAGE
# ==========================================

log.info("=== DIAGNOSTIC CAPITAL ===")
log.info(f"API_KEY présente : {bool(API_KEY)}")
log.info(f"EMAIL présent : {bool(API_EMAIL)}")
log.info(f"PASSWORD présent : {bool(API_PASSWORD)}")

# ==========================================
# ENGINE
# ==========================================

class TradingDecisionEngine:

    def analyze(self, data):

        signal = data.get("signal", "")

        adx = float(data.get("adx", 0))

        adx_rising = data.get("adx_rising", False)

        di_plus = float(data.get("di_plus", 0))
        di_minus = float(data.get("di_minus", 0))

        last_pivot = data.get("last_pivot", "")

        impulse = data.get("impulse", "")

        score = 0

        # ADX

        if adx >= 25:
            score += 25
        else:
            score -= 10

        # ADX rising

        if adx_rising:
            score += 15

        # LONG

        if signal == "long":

            if di_plus > di_minus:
                score += 10
            else:
                score -= 5

            if last_pivot == "low":
                score += 20
            else:
                score -= 10

            if impulse == "bullish":
                score += 20
            else:
                score -= 5

        # SHORT

        elif signal == "short":

            if di_minus > di_plus:
                score += 10
            else:
                score -= 5

            if last_pivot == "high":
                score += 20
            else:
                score -= 10

            if impulse == "bearish":
                score += 20
            else:
                score -= 5

        log.info(f"Score final: {score}")

        if score >= 55:
            return True, score, "Setup validé"

        return False, score, "Setup refusé"

engine = TradingDecisionEngine()

# ==========================================
# CAPITAL API
# ==========================================

def get_session():

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
                f"ECHEC CONNEXION CAPITAL | "
                f"Status={response.status_code} | "
                f"Response={response.text}"
            )

            return None

        log.info("CONNEXION CAPITAL OK")

        cst = response.headers.get("CST")
        xst = response.headers.get("X-SECURITY-TOKEN")

        if not cst or not xst:
            log.error("ECHEC GET_HEADERS | CST ou X-SECURITY-TOKEN manquant")
            return None

        return {
            "X-CAP-API-KEY": API_KEY,
            "CST": cst,
            "X-SECURITY-TOKEN": xst,
            "Content-Type": "application/json"
        }

    except Exception as e:

        log.error(f"Erreur session: {e}")

        return None

# ==========================================
# MARKET RULES
# ==========================================

def get_market_rules(epic, headers):

    try:

        response = requests.get(
            f"{API_URL}/markets/{epic}",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:

            log.error(
                f"ECHEC MARKET RULES | "
                f"Status={response.status_code} | "
                f"Response={response.text}"
            )

            return None

        market = response.json()

        return {
            "min_size": float(
                market["dealingRules"]
                .get("minDealSize", {})
                .get("value", 0.01)
            ),

            "min_stop": float(
                market["dealingRules"]
                .get("minNormalStopOrLimitDistance", {})
                .get("value", 1)
            ),

            "decimals": int(
                market["snapshot"]
                .get("decimalPlacesFactor", 2)
            )
        }

    except Exception as e:

        log.error(f"Erreur market rules: {e}")

        return None

# ==========================================
# POSITION SIZE
# ==========================================

def calculate_position_size(epic, price, stop_distance, min_size):

    risk_amount = state.capital * RISK_PCT

    if stop_distance <= 0:
        return min_size

    size = risk_amount / stop_distance

    # Crypto
    if "BTC" in epic:
        size *= 0.01

    elif "ETH" in epic:
        size *= 0.05

    # NASDAQ
    elif "NAS" in epic:
        size *= 0.5

    size = round(size, 4)

    return max(size, min_size)

# ==========================================
# OPEN POSITION
# ==========================================

def open_position(direction, price, epic):

    log.info("=== OPEN_POSITION ===")
    log.info(f"Direction : {direction}")
    log.info(f"Epic : {epic}")
    log.info(f"Prix : {price}")

    headers = get_session()

    log.info(f"Headers OK : {headers is not None}")

    if not headers:
        return False

    rules = get_market_rules(epic, headers)

    log.info(f"Rules : {rules}")

    if not rules:
        return False

    min_size = rules["min_size"]
    min_stop = rules["min_stop"]
    decimals = rules["decimals"]

    # ATR simplifié adaptatif

    stop_distance = price * 0.005

    if stop_distance < min_stop:
        stop_distance = min_stop

    # Long

    if direction == "long":

        side = "BUY"

        stop_level = price - stop_distance
        take_profit = price + (stop_distance * TP_RATIO)

    # Short

    else:

        side = "SELL"

        stop_level = price + stop_distance
        take_profit = price - (stop_distance * TP_RATIO)

    # Stop garanti auto

    guaranteed_stop = False

    if "BTC" in epic or "ETH" in epic:
        guaranteed_stop = True

    size = calculate_position_size(
        epic,
        price,
        stop_distance,
        min_size
    )

    payload = {
        "epic": epic,
        "direction": side,
        "size": size,
        "guaranteedStop": guaranteed_stop,
        "stopLevel": round(stop_level, decimals),
        "profitLevel": round(take_profit, decimals)
    }

    log.info(f"Payload ordre: {payload}")

    try:

        response = requests.post(
            f"{API_URL}/positions",
            headers=headers,
            json=payload,
            timeout=10
        )

        log.info(f"Réponse broker: {response.text}")
        log.info(f"Status broker : {response.status_code}")

        if response.status_code != 200:

            log.error(
                f"ECHEC OUVERTURE POSITION | "
                f"Status={response.status_code} | "
                f"Response={response.text}"
            )

            return False

        state.position_open = True
        state.position_side = direction
        state.last_trade_time = datetime.now(timezone.utc)

        log.info("Position ouverte")

        return True

    except Exception as e:

        log.error(f"Erreur open_position: {e}")

        return False

# ==========================================
# FLASK
# ==========================================

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "Agent IA adaptatif actif",
        "version": "3.0"
    })

@app.route("/search-market", methods=["GET"])
def search_market():

    term = request.args.get("q", "nasdaq")

    headers = get_session()

    if not headers:
        return jsonify({"error": "auth failed"}), 500

    try:

        response = requests.get(
            f"{API_URL}/markets",
            headers=headers,
            params={"searchTerm": term},
            timeout=10
        )

        return jsonify(response.json())

    except Exception as e:

        return jsonify({"error": str(e)}), 500


def webhook():

    try:

        raw_data = request.data.decode("utf-8")

        log.info(f"RAW WEBHOOK: {raw_data}")

        if not raw_data:

            return jsonify({
                "status": "empty_webhook"
            }), 400

        data = json.loads(raw_data)

        log.info(f"Signal reçu: {json.dumps(data)}")

        can_trade, reason = state.can_trade()

        if not can_trade:

            log.info(f"Trading bloqué: {reason}")

            return jsonify({
                "status": "blocked",
                "reason": reason
            })

        should_enter, score, message = (
            engine.analyze(data)
        )

        log.info(f"Décision: {message}")

        if should_enter:

            signal = data.get("signal")
            price = float(data.get("price", 0))
            epic_raw = data.get("epic", DEFAULT_EPIC)
            epic = EPIC_MAP.get(epic_raw, epic_raw)

            log.info(f"Epic reçu: {epic_raw} → converti: {epic}")

            success = open_position(
                signal,
                price,
                epic
            )

            return jsonify({
                "status": (
                    "trade_opened"
                    if success
                    else "order_failed"
                ),
                "score": score
            })

        return jsonify({
            "status": "no_trade",
            "score": score,
            "message": message
        })

    except Exception as e:

        log.error(f"Webhook error: {e}")

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ==========================================
# START
# ==========================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port)
