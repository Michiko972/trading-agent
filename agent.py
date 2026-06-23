"""
Agent Trading IA v4.0 — Strategie Range Filter Momentum Strict
Version Prop Firm : Verification en temps reel des positions sur le Broker
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

# PARAMETRES FINANCIERS PROP FIRM
CAPITAL_DEMO = 41000.0  
RISK_PCT = 0.01         # 1% du capital par trade
SL_PCT = 0.0006         # Stop Loss serré : 0.06%
TP_RATIO = 1.5          # Ratio 1.5x (TP = 0.09%)

# ==========================================
# GESTION DES LOGS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ==========================================
# ETAT DE L'AGENT (COOLDOWN)
# ==========================================
class AccountState:
    def __init__(self):
        self.last_trade_time = None

    def can_trade(self):
        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < 120:  # Cooldown de 2 minutes de sécurité
                return False, "cooldown"
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

def check_active_positions(epic, headers):
    """Verifie directement chez le broker s'il y a un trade en cours sur cet actif"""
    try:
        response = requests.get(f"{API_URL}/positions", headers=headers, timeout=10)
        if response.status_code == 200:
            positions = response.json().get("positions", [])
            for pos in positions:
                if pos.get("market", {}).get("epic") == epic:
                    return True  # Une position existe deja sur cet actif
        return False
    except Exception as e:
        log.error(f"Erreur lors de la verification des positions : {e}")
        return True  # Par securite, on bloque si l'API ne repond pas

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
# EXECUTION DE L'ORDRE
# ==========================================
def open_position(direction, price, epic, headers):
    rules = get_market_rules(epic, headers)
    if not rules: return False

    stop_distance = max(price * SL_PCT, rules["min_stop"])
    risk_amount = CAPITAL_DEMO * RISK_PCT
    size = max(round(risk_amount / stop_distance, 4), rules["min_size"])
    side = "BUY" if direction == "long" else "SELL"

    payload = {
        "epic": epic,
        "direction": side,
        "size": size,
        "guaranteedStop": "true",
        "stopDistance": round(stop_distance, rules["decimals"]),
        "profitDistance": round(stop_distance * TP_RATIO, rules["decimals"])
    }

    try:
        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            state.last_trade_time = datetime.now(timezone.utc)
            log.info(f"ORDRE EXECUTE AVEC SUCCES | {side} {size} {epic}")
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
    return jsonify({"status": "Agent IA Synchro Live Actif", "version": "4.0"})

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

        # 1. VERIFICATION REELLE SUR LE BROKER (Anti-chevauchement strict)
        if check_active_positions(epic, headers):
            log.warning(f"Signal ignore : Une position est deja en cours sur {epic} chez Capital.com.")
            return jsonify({"status": "blocked", "reason": "position_already_open_on_broker"})

        # 2. FILTRE SECURITE : Cooldown temporel interne
        can_trade, reason = state.can_trade()
        if not can_trade:
            log.warning(f"Signal refuse : {reason}")
            return jsonify({"status": "blocked", "reason": reason})

        # 3. ENVOI DE L'ORDRE SI TOUT EST OK
        if signal in ("long", "short"):
            success = open_position(signal, price, epic, headers)
            return jsonify({"status": "processed", "success": success})

        return jsonify({"status": "ignored", "reason": "unknown_signal"})
    except Exception as e:
        log.error(f"Erreur critique lors de la reception du Webhook : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
