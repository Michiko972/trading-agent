"""
Agent de Trading Automatique v2.7
Architecture : Pine Script = capteur, Agent IA = décideur
Broker : Capital.com (démo)

Version adaptative :
- Synchronisation automatique des positions
- Gestion multi-marchés
- Stop garanti adaptatif
- Distances TP/SL automatiques
- Taille minimum automatique
- Décimales automatiques
- Filtrage des faux setups
"""

import os
import json
import logging
import requests

from datetime import datetime, timezone
from flask import Flask, request, jsonify

# CONFIGURATION

API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()

API_URL = "https://demo-api-capital.backend-capital.com/api/v1"

DEFAULT_EPIC = "BTCUSD"

CAPITAL_DEMO = 1000.0

RISK_PCT = 0.01

DAILY_LOSS_LIMIT = 0.02
MAX_DRAWDOWN_PCT = 0.04
PROFIT_TARGET = 0.06

TP1_RATIO = 1.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("agent.log")
    ]
)

log = logging.getLogger(__name__)

class AccountState:

    def __init__(self):

        self.capital = CAPITAL_DEMO
        self.peak_equity = CAPITAL_DEMO

        self.daily_pnl = 0.0
        self.total_pnl = 0.0

        self.position_open = False
        self.position_side = None
        self.position_size = 0.0

        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0

        self.last_trade_time = None

        self.last_day = datetime.now(timezone.utc).date()

    def reset_daily(self):

        today = datetime.now(timezone.utc).date()

        if today != self.last_day:

            self.daily_pnl = 0.0
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

        if self.last_trade_time:

            elapsed = (
                datetime.now(timezone.utc)
                - self.last_trade_time
            ).total_seconds()

            if elapsed < 600:
                return False, "cooldown_after_trade"

        if self.position_open:
            return False, "position_already_open"

        return True, "ok"

    def update_after_trade(self, pnl):

        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.capital += pnl

        if self.capital > self.peak_equity:
            self.peak_equity = self.capital

        self.last_trade_time = datetime.now(timezone.utc)

state = AccountState()

class TradingDecisionEngine:

    def analyze(self, data):

        signal = data.get("signal", "")

        adx = float(data.get("adx", 0))

        adx_rising = data.get("adx_rising", False)

        last_pivot = data.get("last_pivot", "")

        di_plus = float(data.get("di_plus", 0))
        di_minus = float(data.get("di_minus", 0))

        impulse = data.get("impulse", "")

        if adx < 25:
            return False, "weak_trend", 0, "ADX trop faible"

        if impulse == "weak":
            return False, "weak_impulse", 0, "Impulsion faible"

        score = 0

       
if signal == "long":

    if di_plus > di_minus:
        score += 15
    else:
        score -= 5

    if last_pivot != "low":
        return False, "pivot_invalid", 0, "Pivot invalide"

    score += 50

    if adx_rising:
        score += 25

    if impulse == "bullish":
        score += 25

elif signal == "short":

    if di_minus > di_plus:
        score += 15
    else:
        score -= 5

    if last_pivot != "high":
        return False, "pivot_invalid", 0, "Pivot invalide"

    score += 50

    if adx_rising:
        score += 25

    if impulse == "bearish":
        score += 25

        if score < 75:
            return False, "low_confidence", score, "Score insuffisant"

        return True, "entry_validated", score, "Setup validé"

engine = TradingDecisionEngine()


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
            return None, None

        cst = response.headers.get("CST")
        xst = response.headers.get("X-SECURITY-TOKEN")

        return cst, xst

    except Exception:
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


def get_market_rules(epic):

    headers = get_headers()

    if not headers:
        return None

    try:

        response = requests.get(
            f"{API_URL}/markets/{epic}",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            return None

        market = response.json()

        rules = {
            "min_stop_distance": float(
                market["dealingRules"]
                .get("minNormalStopOrLimitDistance", {})
                .get("value", 0)
            ),

            "min_size": float(
                market["dealingRules"]
                .get("minDealSize", {})
                .get("value", 0.0001)
            ),

            "decimal_places": int(
                market["snapshot"]
                .get("decimalPlacesFactor", 2)
            )
        }

        return rules

    except Exception:
        return None


def sync_position_state(epic=DEFAULT_EPIC):

    headers = get_headers()

    if not headers:
        return

    try:

        response = requests.get(
            f"{API_URL}/positions",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            return

        positions = response.json().get("positions", [])

        has_position = False

        for pos in positions:

            if pos["market"]["epic"] == epic:
                has_position = True
                break

        state.position_open = has_position

        if not has_position:
            state.position_side = None

    except Exception:
        pass


def calculate_position_size(entry_price, stop_price, min_size):

    risk_amount = state.capital * RISK_PCT

    distance = abs(entry_price - stop_price)

    if distance <= 0:
        return min_size

    size = round(risk_amount / distance, 4)

    return max(min_size, size)


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


def open_position(direction, entry_price, stop_price, epic):

    headers = get_headers()

    if not headers:
        return False

    rules = get_market_rules(epic)

    if not rules:
        return False

    decimals = rules["decimal_places"]
    min_stop = rules["min_stop_distance"]
    min_size = rules["min_size"]

    guaranteed_stop = False

    if "BTC" in epic or "ETH" in epic:
        guaranteed_stop = True

    dist = abs(entry_price - stop_price)

    if dist < min_stop:
        dist = min_stop

    if direction == "long":

        stop_price = entry_price - dist

        take_profit = entry_price + (dist * TP1_RATIO)

    else:

        stop_price = entry_price + dist

        take_profit = entry_price - (dist * TP1_RATIO)

    size = calculate_position_size(
        entry_price,
        stop_price,
        min_size
    )

    payload = {
        "epic": epic,

        "direction": (
            "BUY" if direction == "long"
            else "SELL"
        ),

        "size": round(size, 4),

        "guaranteedStop": guaranteed_stop,

        "stopLevel": round(stop_price, decimals),

        "profitLevel": round(take_profit, decimals)
    }

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
    state.position_size = size

    state.entry_price = entry_price
    state.stop_loss = stop_price
    state.take_profit = take_profit

    return True


app = Flask(__name__)
@app.route("/test", methods=["GET"])
def test():
    return "TEST OK", 200

@app.route("/webhook", methods=["POST"])
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

        sync_position_state(
            data.get("epic", DEFAULT_EPIC)
        )

        can_trade, reason = state.can_trade()

        if not can_trade:

            return jsonify({
                "status": "blocked",
                "reason": reason
            }), 200

        should_enter, reason, score, message = (
            engine.analyze(data)
        )
        log.info(f"Décision: {message}")

        if should_enter:

            signal = data.get("signal")

            price = float(data.get("price", 0))

            stop = calculate_stop(
                signal,
                price,
                data
            )

            epic = data.get(
                "epic",
                DEFAULT_EPIC
            )

            success = open_position(
                signal,
                price,
                stop,
                epic
            )

            return jsonify({
                "status": (
                    "trade_opened"
                    if success
                    else "order_failed"
                ),
                "score": score,
                "message": message
            }), 200

        return jsonify({
            "status": "no_trade",
            "reason": reason,
            "score": score
        }), 200

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "Agent Trading actif",
        "version": "2.7"
    }), 200


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port)
