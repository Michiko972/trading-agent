"""
Agent Trading IA v4.7 — Strategie & Gestion du Risque Topstep $50,000
Securite Globale: Max 1 Position | Risque Max $400 par Trade | Correctif Port Railway
TradingView -> Railway -> Capital.com
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ==========================================
# CONFIGURATION ENVIRONNEMENT
# ==========================================
API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()
API_URL = "https://demo-api-capital.backend-capital.com/api/v1"

DEFAULT_EPIC = "BTCUSD"
EPIC_MAP = {
    "NASDAQ": "US100", "NAS100": "US100", "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD", "EURUSD": "EURUSD", "USDJPY": "USDJPY", "GBPUSD": "GBPUSD"
}

# CONFIGURATION FINANCIERE STRATEGIE TOPSTEP ACCELEREE
RISK_PER_TRADE_USD = 400.0  # Risque de $400 par trade
SL_POINTS = 15.0            # Distance fixe du Stop Loss (15 points / pips)
TP_POINTS = 22.5            # Distance fixe du Take Profit (22.5 points / pips) -> Gain de $600

# ==========================================
# GESTION DES LOGS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ==========================================
# ETAT DE L'AGENT (COOLDOWN STRATEGIE)
# ==========================================
class AccountState:
    def __init__(self):
        self.last_trade_time = None

    def can_trade(self):
        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < 1800:  # Cooldown de 30 minutes
                return False, f"cooldown_active_{int((1800-elapsed)/60)}_min_remaining"
        return True, "ok"

state = AccountState()

# ==========================================
# FONCTIONS API CAPITAL.COM
# ==========================================
def get_session():
    try:
        response = requests.post(
            f"{API_URL}/session",
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False},
            timeout=10
        )
        if response.status_code != 200:
            log.error(f"ECHEC APPAIRAGE SESSION : {response.text}")
            return None
        return {
            "X-CAP-API-KEY": API_KEY,
            "CST": response.headers.get("CST"),
            "X-SECURITY-TOKEN": response.headers.get("X-SECURITY-TOKEN"),
            "Content-Type": "application/json"
        }
    except Exception as e:
        log.error(f"Erreur lors de la creation de session : {e}")
        return None

def check_any_active_position(headers):
    """VERROU COMPTE VIDE REEL : Bloque s'il y a la moindre position ouverte, tous marchés confondus"""
    try:
        response = requests.get(f"{API_URL}/positions", headers=headers, timeout=10)
        if response.status_code == 200:
            positions = response.json().get("positions", [])
            if len(positions) > 0:
                return True
        return False
    except Exception as e:
        log.error(f"Erreur lors de la verification globale des positions : {e}")
        return True

def get_market_rules(epic, headers):
    try:
        response = requests.get(f"{API_URL}/markets/{epic}", headers=headers, timeout=10)
        if response.status_code != 200: return None
        market = response.json()
        return {
            "min_size": float(market["dealingRules"].get("minDealSize", {}).get("value", 0.01)),
            "min_stop": float(market["dealingRules"].get("minNormalStopOrLimitDistance", {}).get("value", 1)),
            "decimals": int(market["snapshot"].get("decimalPlacesFactor", 2))
        }
    except:
        return None

# ==========================================
# EXECUTION DE L'ORDRE AVEC RISK MANAGEMENT
# ==========================================
def open_position(direction, price, epic, headers):
    rules = get_market_rules(epic, headers)
    if not rules: return False

    stop_distance = max(SL_POINTS, rules["min_stop"])
    profit_distance = max(TP_POINTS, rules["min_stop"] * 1.5)
    
    size = max(round(RISK_PER_TRADE_USD / stop_distance, 4), rules["min_size"])
    side = "BUY" if direction == "long" else "SELL"

    payload = {
        "epic": epic,
        "direction": side,
        "size": size,
        "guaranteedStop": True,  # Vrai booleen sans guillemets pour l'API Capital
        "stopDistance": round(stop_distance, rules["decimals"]),
        "profitDistance": round(profit_distance, rules["decimals"])
    }

    try:
        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            state.last_trade_time = datetime.now(timezone.utc)
            log.info(f"ORDRE REALISE AVEC SUCCES | {side} {size} {epic}")
            return True
        log.error(f"REJET PAR LE BROKER | Code: {res.status_code} | Reponse: {res.text}")
        return False
    except Exception as e:
        log.error(f"Erreur d'execution open_position : {e}")
        return False

# ==========================================
# FLASK ROUTAGE WEBHOOK
# ==========================================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Agent Strat Topstep Mode Accelere Actif", "version": "4.7"})

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = json.loads(request.data.decode("utf-8"))
        signal = data.get("signal")
        epic = EPIC_MAP.get(data.get("epic"), DEFAULT_EPIC)
        price = float(data.get("price", 0))

        headers = get_session()
        if not headers: 
            return jsonify({"status": "error", "message": "Authentication failed"}), 500

        # 1. Verification verrou global position ouverte
        if check_any_active_position(headers):
            log.warning("Signal ignore : Une position est deja en cours sur le compte.")
            return jsonify({"status": "blocked", "reason": "account_has_open_position"})

        # 2. Cooldown securite 30 minutes
        can_trade, reason = state.can_trade()
        if not can_trade:
            log.warning(f"Signal refuse : {reason}")
            return jsonify({"status": "blocked", "reason": reason})

        # 3. Envoi de l'ordre
        if signal in ("long", "short"):
            success = open_position(signal, price, epic, headers)
            return jsonify({"status": "processed", "success": success})

        return jsonify({"status": "ignored", "reason": "unknown_signal"})
    except Exception as e:
        log.error(f"Erreur critique Webhook : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================
# POINT D'ENTREE ADAPTE A RAILWAY
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
