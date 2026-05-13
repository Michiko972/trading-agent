"""
Agent de Trading Automatique v2.5
Architecture : Pine Script = capteur, Agent IA = décideur
Broker : Capital.com (démo)

Corrections v2.5 :
- guaranteedStop: False
- Synchronisation état position avec Capital.com
- Détection fermeture TP/SL automatique
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

API_KEY      = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL    = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()

API_URL      = "https://demo-api-capital.backend-capital.com/api/v1"
DEFAULT_EPIC = "BTCUSD"

# ══════════════════════════════════════════════
# OBJECTIFS PROP FIRM
# ══════════════════════════════════════════════

CAPITAL_DEMO       = 1000.0
DAILY_TARGET_EUR   = 20
RISK_PER_TRADE_EUR = 13
DAILY_LOSS_LIMIT   = 40
PROFIT_TARGET      = 60
TP_RATIO           = DAILY_TARGET_EUR / RISK_PER_TRADE_EUR
WICK_THRESHOLD     = 0.6

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
        self.capital       = CAPITAL_DEMO
        self.peak_equity   = CAPITAL_DEMO
        self.daily_pnl     = 0.0
        self.total_pnl     = 0.0
        self.trades_today  = 0
        self.position_open = False
        self.position_side = None
        self.entry_price   = 0.0
        self.stop_loss     = 0.0
        self.take_profit   = 0.0
        self.last_day      = datetime.now(timezone.utc).date()

    def reset_daily(self):
        today = datetime.now(timezone.utc).date()
        if today != self.last_day:
            self.daily_pnl    = 0.0
            self.trades_today = 0
            self.last_day     = today
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
        self.daily_pnl  += pnl
        self.total_pnl  += pnl
        self.capital    += pnl
        self.trades_today += 1
        log.info(f"Trade fermé | PnL: {pnl:+.2f}€ | Jour: {self.daily_pnl:+.2f}€ | Total: {self.total_pnl:+.2f}€")

state = AccountState()

# ══════════════════════════════════════════════
# MOTEUR DE DÉCISION
# ══════════════════════════════════════════════

class TradingDecisionEngine:

    def analyze(self, data):
        signal     = data.get("signal", "")
        adx        = float(data.get("adx", 0))
        adx_rising = data.get("adx_rising", False)
        last_pivot = data.get("last_pivot", "")
        di_plus    = float(data.get("di_plus", 0))
        di_minus   = float(data.get("di_minus", 0))

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

        log.info(f"Score de confiance: {score}/100")

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
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False},
            timeout=10
        )
        if response.status_code != 200:
            log.error(f"Erreur session API | {response.status_code} | {response.text}")
            return None, None
        return response.headers.get("CST"), response.headers.get("X-SECURITY-TOKEN")
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
# SYNCHRONISATION ÉTAT POSITION
# Vérifie sur Capital.com si une position est
# réellement ouverte — évite le blocage après
# fermeture automatique TP/SL
# ══════════════════════════════════════════════

def sync_position_state(epic=DEFAULT_EPIC):
    headers = get_headers()
    if not headers:
        return
    try:
        r = requests.get(f"{API_URL}/positions", headers=headers, timeout=10)
        if r.status_code == 200:
            positions = r.json().get("positions", [])
            epic_open = any(p["market"]["epic"] == epic for p in positions)
            if state.position_open and not epic_open:
                log.info("Position fermée détectée (TP/SL atteint) — reset état")
                state.position_open = False
                state.position_side = None
                state.entry_price   = 0.0
                state.stop_loss     = 0.0
                state.take_profit   = 0.0
    except Exception as e:
        log.error(f"Erreur sync position: {e}")

# ══════════════════════════════════════════════
# CALCUL POSITION SIZE
# ══════════════════════════════════════════════

def calculate_position_size(entry_price, stop_price):
    distance = abs(entry_price - stop_price)
    if distance <= 0:
        return 1
    size = round(RISK_PER_TRADE_EUR / distance)
    return max(1, size)

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
# TAKE PROFIT
# ══════════════════════════════════════════════

def calculate_take_profit(direction, entry_price, stop_price):
    distance   = abs(entry_price - stop_price)
    tp_distance = distance * TP_RATIO
    if direction == "long":
        return entry_price + tp_distance
    return entry_price - tp_distance

# ══════════════════════════════════════════════
# DÉTECTION MÈCHE RETOURNEMENT
# ══════════════════════════════════════════════

def detect_reversal_wick(data):
    signal      = data.get("signal", "")
    open_price  = float(data.get("open", 0))
    close_price = float(data.get("close", 0))
    high_price  = float(data.get("high", 0))
    low_price   = float(data.get("low", 0))
    candle_size = high_price - low_price
    if candle_size <= 0:
        return False
    upper_wick    = high_price - max(open_price, close_price)
    lower_wick    = min(open_price, close_price) - low_price
    upper_ratio   = upper_wick / candle_size
    lower_ratio   = lower_wick / candle_size
    bearish_candle = close_price < open_price
    bullish_candle = close_price > open_price
    if signal == "long" and upper_ratio >= WICK_THRESHOLD and bearish_candle:
        log.info("Mèche haute retournement détectée")
        return True
    if signal == "short" and lower_ratio >= WICK_THRESHOLD and bullish_candle:
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
    size        = calculate_position_size(entry_price, stop_price)
    take_profit = calculate_take_profit(direction, entry_price, stop_price)
    payload = {
        "epic": epic,
        "direction": "BUY" if direction == "long" else "SELL",
        "size": size,
        "guaranteedStop": False,
        "stopLevel": round(stop_price, 2),
        "profitLevel": round(take_profit, 2)
    }
    log.info(f"OUVERTURE TRADE | Size={size} | SL={round(stop_price, 2)} | TP={round(take_profit, 2)}")
    try:
        response = requests.post(
            f"{API_URL}/positions",
            headers=headers,
            json=payload,
            timeout=10
        )
        if response.status_code != 200:
            log.error(f"Erreur ouverture position | {response.status_code} | {response.text}")
            return False
        state.position_open = True
        state.position_side = direction
        state.entry_price   = entry_price
        state.stop_loss     = stop_price
        state.take_profit   = take_profit
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
        response = requests.get(f"{API_URL}/positions", headers=headers, timeout=10)
        if response.status_code != 200:
            return False
        for pos in response.json().get("positions", []):
            if pos["market"]["epic"] == epic:
                deal_id = pos["position"]["dealId"]
                cr = requests.delete(f"{API_URL}/positions/{deal_id}", headers=headers, timeout=10)
                if cr.status_code == 200:
                    pnl = pos["position"].get("upl", 0)
                    state.update_after_trade(pnl)
                    state.position_open = False
                    state.position_side = None
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

        epic = data.get("epic", DEFAULT_EPIC)

        # Synchronisation état position avant toute décision
        sync_position_state(epic)

        # Fermeture sur mèche retournement
        if state.position_open:
            if detect_reversal_wick(data):
                close_all_positions(epic)
                return jsonify({"status": "position_closed_reversal"}), 200

        can_trade, reason = state.can_trade()
        if not can_trade:
            return jsonify({"status": "blocked", "reason": reason}), 200

        should_enter, reason, score, message = engine.analyze(data)

        if should_enter:
            signal = data.get("signal")
            price  = float(data.get("price", 0))
            stop   = calculate_stop(signal, price, data)
            success = open_position(signal, price, stop, epic)
            return jsonify({
                "status": "trade_opened" if success else "order_failed",
                "score": score,
                "message": message
            }), 200

        return jsonify({"status": "no_trade", "reason": reason, "score": score}), 200

    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/status", methods=["GET"])
def status():
    state.reset_daily()
    can, reason = state.can_trade()
    return jsonify({
        "capital":       round(state.capital, 2),
        "total_pnl":     round(state.total_pnl, 2),
        "daily_pnl":     round(state.daily_pnl, 2),
        "trades_today":  state.trades_today,
        "position_open": state.position_open,
        "position_side": state.position_side,
        "entry_price":   state.entry_price,
        "stop_loss":     state.stop_loss,
        "take_profit":   state.take_profit,
        "objectif":      f"{round(state.total_pnl / PROFIT_TARGET * 100, 1)}%",
        "can_trade":     can,
        "block_reason":  reason if not can else None
    }), 200

@app.route("/close", methods=["POST"])
def close():
    epic = request.get_json(force=True).get("epic", DEFAULT_EPIC) if request.data else DEFAULT_EPIC
    if state.position_open:
        success = close_all_positions(epic)
        return jsonify({"status": "closed" if success else "error"}), 200
    return jsonify({"status": "no_position"}), 200

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Agent Trading actif", "version": "2.5"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
