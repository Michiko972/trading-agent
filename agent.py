"""
Agent de Trading Automatique v3
Architecture : Pine Script = capteur, Agent IA = décideur
Broker : Capital.com (démo)

Nouveautés :
- Synchronisation réelle des positions
- TP partiel 50 %
- Runner TP2
- Plus de faux "position_already_open"
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

CAPITAL_DEMO = 1000.0

RISK_PCT = 0.01
DAILY_LOSS_LIMIT = 0.02
MAX_DRAWDOWN_PCT = 0.04
PROFIT_TARGET = 0.06

TP1_RATIO = 1.5
TP2_RATIO = 3.0

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
        self.best_day_pnl = 0.0

        self.trades_today = 0

        self.position_side = None
        self.position_size = 0.0

        self.entry_price = 0.0
        self.stop_loss = 0.0

        self.take_profit1 = 0.0
        self.take_profit2 = 0.0

        self.last_day = datetime.now(timezone.utc).date()

    def reset_daily(self):

        today = datetime.now(timezone.utc).date()

        if today != self.last_day:

            if self.daily_pnl > self.best_day_pnl:
                self.best_day_pnl = self.daily_pnl

            self.daily_pnl = 0.0
            self.trades_today = 0
            self.last_day = today

            log.info("Nouveau jour — reset journalier")

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

    def update_after_trade(self, pnl):

        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.capital += pnl

        if self.capital > self.peak_equity:
            self.peak_equity = self.capital

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

        log.info(
            f"Contexte reçu | "
            f"signal={signal} | "
            f"adx={adx} | "
            f"pivot={last_pivot}"
        )

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

        log.info(f"Score confiance: {score}/100")

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
                f"Code: {response.status_code} | "
                f"Réponse: {response.text}"
            )

            return None, None

        cst = response.headers.get("CST")
        xst = response.headers.get("X-SECURITY-TOKEN")

        if not cst or not xst:
            log.error("Tokens session manquants")
            return None, None

        log.info("Session API ouverte avec succès")

        return cst, xst

    except Exception as e:

        log.error(f"Exception session API: {e}")

        return None, None


def get_headers():

    cst, xst = get_session()

    if not cst or not xst:
        log.error("Session invalide")
        return None

    return {
        "X-CAP-API-KEY": API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": xst,
        "Content-Type": "application/json"
    }

# ══════════════════════════════════════════════
# POSITIONS RÉELLES CAPITAL.COM
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
# OUVERTURE POSITION
# ══════════════════════════════════════════════

def open_position(direction, entry_price, stop_price, epic=DEFAULT_EPIC):

    headers = get_headers()

    if not headers:
        return False

    total_size = calculate_position_size(entry_price, stop_price)

    half_size = round(total_size / 2, 4)

    dist = abs(entry_price - stop_price)

    if direction == "long":

        tp1 = entry_price + dist * TP1_RATIO
        tp2 = entry_price + dist * TP2_RATIO

    else:

        tp1 = entry_price - dist * TP1_RATIO
        tp2 = entry_price - dist * TP2_RATIO

    direction_api = "BUY" if direction == "long" else "SELL"

    # ══════════════════════════════════
    # POSITION TP1
    # ══════════════════════════════════

    payload_tp1 = {
        "epic": epic,
        "direction": direction_api,
        "size": half_size,
        "guaranteedStop": True,
        "stopLevel": stop_price,
        "profitLevel": tp1
    }

    # ══════════════════════════════════
    # POSITION TP2 (RUNNER)
    # ══════════════════════════════════

    payload_tp2 = {
        "epic": epic,
        "direction": direction_api,
        "size": half_size,
        "guaranteedStop": True,
        "stopLevel": stop_price,
        "profitLevel": tp2
    }

    try:

        response1 = requests.post(
            f"{API_URL}/positions",
            headers=headers,
            json=payload_tp1,
            timeout=10
        )

        if response1.status_code != 200:

            log.error(
                f"Erreur ouverture TP1 | "
                f"{response1.status_code} | "
                f"{response1.text}"
            )

            return False

        log.info("Position TP1 ouverte")

        response2 = requests.post(
            f"{API_URL}/positions",
            headers=headers,
            json=payload_tp2,
            timeout=10
        )

        if response2.status_code != 200:

            log.error(
                f"Erreur ouverture TP2 | "
                f"{response2.status_code} | "
                f"{response2.text}"
            )

            return False

        log.info("Position TP2 runner ouverte")

        state.position_side = direction
        state.position_size = total_size

        state.entry_price = entry_price
        state.stop_loss = stop_price

        state.take_profit1 = tp1
        state.take_profit2 = tp2

        log.info(
            f"Trade ouvert | "
            f"{direction.upper()} | "
            f"{epic} | "
            f"Taille totale: {total_size}"
        )

        return True

    except Exception as e:

        log.error(f"Exception ouverture position: {e}")

        return False

# ══════════════════════════════════════════════
# FERMETURE POSITION
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

                pnl = pos["position"].get("upl", 0)

                state.update_after_trade(pnl)

                log.info(f"Position fermée | PnL: {pnl}")

        return True

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

        can_trade, reason = state.can_trade()

        if not can_trade:

            log.warning(f"Trading bloqué: {reason}")

            return jsonify({
                "status": "blocked",
                "reason": reason
            }), 200

        should_enter, reason, score, message = engine.analyze(data)

        log.info(f"Décision: {message}")

        if should_enter:

            signal = data.get("signal")

            price = float(data.get("price", 0))

            stop = calculate_stop(signal, price, data)

            epic = data.get("epic", DEFAULT_EPIC)

            success = open_position(signal, price, stop, epic)

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

    open_positions = get_open_positions()

    return jsonify({
        "capital": round(state.capital, 2),
        "daily_pnl": round(state.daily_pnl, 2),
        "total_pnl": round(state.total_pnl, 2),
        "open_positions": len(open_positions),
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
        "version": "3.0"
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
