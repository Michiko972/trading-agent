"""
Agent Trading IA v4.9 — Stratégie & Gestion du Risque Topstep $50,000
Risque Strict de $400 par Trade | Correction du Calcul Forex (USDJPY)
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

DEFAULT_EPIC = "US100"
EPIC_MAP = {
    "NASDAQ": "US100", "NAS100": "US100", "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD", "EURUSD": "EURUSD", "USDJPY": "USDJPY", "GBPUSD": "GBPUSD"
}

# CONFIGURATION STRICTE TOPSTEP
RISK_PER_TRADE_USD = 400.0  # Perte maximale autorisée de $400

# ==========================================
# GESTION DES LOGS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

class AccountState:
    def __init__(self):
        self.last_trade_time = None

    def can_trade(self):
        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < 1800:  # Cooldown 30 min
                return False, f"cooldown_active_{int((1800-elapsed)/60)}__min_remaining"
        return True, "ok"

state = AccountState()

def get_session():
    try:
        response = requests.post(
            f"{API_URL}/session",
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False},
            timeout=10
        )
        if response.status_code != 200:
            log.error(f"ECHEC SESSION BROKER : {response.text}")
            return None
        return {
            "X-CAP-API-KEY": API_KEY,
            "CST": response.headers.get("CST"),
            "X-SECURITY-TOKEN": response.headers.get("X-SECURITY-TOKEN"),
            "Content-Type": "application/json"
        }
    except Exception as e:
        log.error(f"Erreur connexion session : {e}")
        return None

def check_any_active_position(headers):
    try:
        response = requests.get(f"{API_URL}/positions", headers=headers, timeout=10)
        if response.status_code == 200:
            positions = response.json().get("positions", [])
            if len(positions) > 0:
                return True
        return False
    except Exception as e:
        log.error(f"Erreur verification positions : {e}")
        return True

def get_market_rules(epic, headers):
    try:
        response = requests.get(f"{API_URL}/markets/{epic}", headers=headers, timeout=10)
        if response.status_code != 200: return None
        market = response.json()
        return {
            "min_size": float(market["dealingRules"].get("minDealSize", {}).get("value", 0.01)),
            "min_stop": float(market["dealingRules"].get("minNormalStopOrLimitDistance", {}).get("value", 1)),
            "decimals": int(market["snapshot"].get("decimalPlacesFactor", 2)),
            "scaling_factor": float(market["snapshot"].get("scalingFactor", 1.0))
        }
    except:
        return None

# ==========================================
# CALCUL DE TAILLE ET ENVOI DE L'ORDRE
# ==========================================
def open_position(direction, price, epic, headers):
    rules = get_market_rules(epic, headers)
    if not rules: 
        log.error(f"Impossible de récuperer les regles pour {epic}")
        return False

    # 1. Gestion des distances de Stop Loss / Take Profit
    if "USDJPY" in epic:
        stop_distance = max(0.15, rules["min_stop"]) # 15 pips réels (ex: de 161.50 à 161.35)
        profit_distance = stop_distance * 1.5       # Ratios 1:1.5 standard
        
        # Formule Forex Capital.com réajustée :
        # Risque ($400) / (Distance en pips * scaling factor du yen pour obtenir la valeur réelle d'un lot)
        size = round(RISK_PER_TRADE_USD / (stop_distance * 100), 2)
    elif "EURUSD" in epic or "GBPUSD" in epic:
        stop_distance = max(0.0015, rules["min_stop"]) # 15 pips
        profit_distance = stop_distance * 1.5
        size = round(RISK_PER_TRADE_USD / (stop_distance * 10000), 2)
    else:
        # Indices (NASDAQ, etc.)
        stop_distance = max(15.0, rules["min_stop"])
        profit_distance = 22.5
        size = round(RISK_PER_TRADE_USD / stop_distance, 2)

    # Sécurité taille minimale imposée par le broker
    size = max(size, rules["min_size"])
    side = "BUY" if direction == "long" else "SELL"

    payload = {
        "epic": epic,
        "direction": side,
        "size": size,
        "guaranteedStop": True,
        "stopDistance": round(stop_distance, rules["decimals"]),
        "profitDistance": round(profit_distance, rules["decimals"])
    }

    try:
        log.info(f"Envoi de l'ordre calibré à $400 : {payload}")
        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            state.last_trade_time = datetime.now(timezone.utc)
            log.info(f"SUCCÈS TOPSTEP | Ordre exécuté : {side} {size} {epic}")
            return True
        log.error(f"REJET BROKER | Code: {res.status_code} | Message: {res.text}")
        return False
    except Exception as e:
        log.error(f"Erreur open_position : {e}")
        return False

# ==========================================
# FLASK COMPOSANT ROUTE
# ==========================================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Agent Risque Calibre Topstep Actif", "version": "4.9"})

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = json.loads(request.data.decode("utf-8"))
        signal = data.get("signal")
        epic = EPIC_MAP.get(data.get("epic"), DEFAULT_EPIC)
        price = float(data.get("price", 0))

        headers = get_session()
        if not headers: 
            return jsonify({"status": "error", "message": "Auth failed"}), 500

        if check_any_active_position(headers):
            log.warning("Signal ignoré : Position déjà en cours.")
            return jsonify({"status": "blocked", "reason": "position_open"})

        can_trade, reason = state.can_trade()
        if not can_trade:
            log.warning(f"Signal refusé : {reason}")
            return jsonify({"status": "blocked", "reason": reason})

        if signal in ("long", "short"):
            success = open_position(signal, price, epic, headers)
            return jsonify({"status": "processed", "success": success})

        return jsonify({"status": "ignored", "reason": "invalid_signal"})
    except Exception as e:
        log.error(f"Erreur critique : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
