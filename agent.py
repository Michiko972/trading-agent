"""
Agent Trading IA v3.7 — Strategie Range Filter Momentum Strict
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

CAPITAL_DEMO = 1000.0
RISK_PCT = 0.03   # Gestion stricte du risque : 3% du capital par trade
TP_RATIO = 1.5    # Ratio R:R (Si SL = 0.3%, alors TP = 0.45%)

# ==========================================
# GESTION DES LOGS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ==========================================
# ETAT DE L'AGENT (ANTI-DOUBLONS)
# ==========================================
class AccountState:
    def __init__(self):
        self.capital = CAPITAL_DEMO
        self.position_open = False
        self.position_side = None
        self.last_trade_time = None

    def can_trade(self):
        if self.position_open:
            return False, "position_already_open"
        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < 120:  # Cooldown de 2 minutes entre deux ordres
                return False, "cooldown"
        return True, "ok"

state = AccountState()

# ==========================================
# CONNEXION API CAPITAL.COM
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
# EXECUTION DES ORDRES (ENTREE AVEC TP/SL GARANTI)
# ==========================================
def open_position(direction, price, epic, headers):
    rules = get_market_rules(epic, headers)
    if not rules: return False

    # Calcul du Stop Loss : Fixe a 0.3% du prix d'entree
    stop_distance = max(price * 0.003, rules["min_stop"])
    
    # Calcul de la taille de la position liee au risque de 3%
    risk_amount = state.capital * RISK_PCT
    size = max(round(risk_amount / stop_distance, 4), rules["min_size"])
    side = "BUY" if direction == "long" else "SELL"

    # Payload mis a jour avec "guaranteedStop": "true" pour Capital.com Europe
    payload = {
        "epic": epic,
        "direction": side,
        "size": size,
        "guaranteedStop": "true",  # Active obligatoirement le stop garanti exigé
        "stopDistance": round(stop_distance, rules["decimals"]),
        "profitDistance": round(stop_distance * TP_RATIO, rules["decimals"])
    }

    try:
        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            state.position_open = True
            state.position_side = direction
            state.last_trade_time = datetime.now(timezone.utc)
            log.info(f"ORDRE REALISE AVEC SUCCES | {side} {size} {epic} (Stop Garanti applique)")
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
    return jsonify({"status": "Agent IA Range Filter Connecte", "version": "3.7"})

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

        # FILTRE SECURITE : Verification de la disponibilite de l'agent
        can_trade, reason = state.can_trade()
        if not can_trade:
            log.warning(f"Signal refuse : {reason}")
            return jsonify({"status": "blocked", "reason": reason})

        # DECLENCHEMENT DES ENTREES STRATEGIQUES
        if signal in ("long", "short"):
            success = open_position(signal, price, epic, headers)
            return jsonify({"status": "processed", "success": success})

        return jsonify({"status": "ignored", "reason": "unknown_signal"})
    except Exception as e:
        log.error(f"Erreur critique lors de la reception du Webhook : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    # Liaison au port Railway standard
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
