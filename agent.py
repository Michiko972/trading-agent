"""
Agent Trading IA v4.5 — Strategie & Gestion du Risque Topstep $50,000 (Mode Acceleré)
Securite Globale: Max 1 Position | Risque Max $400 par Trade | Cooldown 30 min
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
RISK_PER_TRADE_USD = 400.0  # Risque passe a $400 pour atteindre l'objectif plus rapidement
SL_POINTS = 15.0            # Distance fixe du Stop Loss (15 points / pips)
TP_POINTS = 22.5            # Distance fixe du Take Profit (22.5 points / pips) -> Gain cible de $600

# ==========================================
# GESTION DES LOGS
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ==========================================
# ETAT DE L'AGENT (COOLDOWN STRAGÉGIQUE)
# ==========================================
class AccountState:
    def __init__(self):
        self.last_trade_time = None

    def can_trade(self):
        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < 1800:  # Cooldown de 30 minutes (1800 secondes) pour eviter l'overtrading
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
    """VERROW TOTAL PROP FIRM : Renvoie True s'il y a la moindre position ouverte sur le compte"""
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
    
    # CALCUL ALGORITHMIQUE DE LA TAILLE DES LOTS
    # Taille = Risque souhaite en dollars ($400) / distance du Stop Loss
    size = max(round(RISK_PER_TRADE_USD / stop_distance, 4), rules["min_size"])
    side = "BUY" if direction == "long" else "SELL"

    payload = {
        "epic": epic,
        "direction": side,
        "size": size,
        "guaranteedStop": "true",  # Protection absolue et obligatoire contre le slippage
        "stopDistance": round(stop_distance, rules["decimals"]),
        "profitDistance": round(profit_distance, rules["decimals"])
    }

    try:
        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            state.last_trade_time = datetime.now(timezone.utc)
            log.info(f"ORDRE STRATEGIQUE TOPSTEP ENVOYE | {side} {size} {epic}")
            log.info(f"PARAMETRES | SL: {stop_distance} pts (~$400) | TP: {profit_distance} pts (~$600)")
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
    return jsonify({"status": "Agent Strat Topstep Mode Accelere Actif", "version": "4.5"})

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

        # 1. VERROU COMPTE VIDE REEL (Maximum 1 position active sur tout le compte)
        if check_any_active_position(headers):
            log.warning("Signal ignore : Une position est deja en cours sur le compte.")
            return jsonify({"status": "blocked", "reason": "account_has_open_position"})

        # 2. FILTRE SECURITE : Cooldown temporel interne de 30 minutes
        can_trade, reason = state.can_trade()
        if not can_trade:
            log.warning(f"Signal refuse : {reason}")
            return jsonify({"status": "blocked", "reason": reason})

        # 3. ENVOI SI LE FEU EST VERT
        if signal in ("long", "short"):
            success = open_position(signal, price, epic, headers)
            return jsonify({"status": "processed", "success": success})

        return jsonify({"status": "ignored", "reason": "unknown_signal"})
    except Exception as e:
        log.error(f"Erreur critique Webhook : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
