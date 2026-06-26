"""
Agent Trading IA v6.4 — Structure Complète
Architecture originale préservée + Correction dynamique des erreurs de seuils
"""
import os
import json
import logging
import requests
import sys
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# --- CONFIGURATION LOGS ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# --- CONFIGURATION API ---
API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()
API_URL = "https://demo-api-capital.backend-capital.com/api/v1"
RISK_AMOUNT = 400.0

class AccountState:
    def __init__(self):
        self.last_trade_time = None

    def can_trade(self):
        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < 1800:
                return False, "cooldown_active"
        return True, "ok"

state = AccountState()
app = Flask(__name__)

def get_session():
    try:
        response = requests.post(
            f"{API_URL}/session",
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False},
            timeout=10
        )
        if response.status_code == 200:
            return {
                "X-CAP-API-KEY": API_KEY,
                "CST": response.headers.get("CST"),
                "X-SECURITY-TOKEN": response.headers.get("X-SECURITY-TOKEN"),
                "Content-Type": "application/json"
            }
    except Exception as e:
        log.error(f"Erreur session: {e}")
    return None

def get_market_rules(epic, headers):
    try:
        res = requests.get(f"{API_URL}/markets/{epic}", headers=headers, timeout=10)
        data = res.json()
        rules = data.get("dealingRules", {})
        return {
            "min_stop": float(rules.get("minNormalStopOrLimitDistance", {}).get("value", 20.0)),
            "min_size": float(rules.get("minDealSize", {}).get("value", 0.1))
        }
    except:
        return {"min_stop": 30.0, "min_size": 0.1}

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Agent v6.4 Actif"})

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        epic, signal = data.get("epic"), data.get("signal")
        headers = get_session()
        if not headers: return jsonify({"status": "error"}), 500

        rules = get_market_rules(epic, headers)
        
        # Calcul : Risque / Distance dynamique
        size = round(max(rules["min_size"], (RISK_AMOUNT / rules["min_stop"])), 2)
        
        payload = {
            "epic": epic,
            "direction": "BUY" if signal == "long" else "SELL",
            "size": size,
            "guaranteedStop": True,
            "stopDistance": rules["min_stop"]
        }
        
        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        log.info(f"REPONSE BROKER | EPIC: {epic} | STATUS: {res.status_code} | MSG: {res.text}")
        
        return jsonify({"status": "success" if res.status_code == 200 else "failed"})
    except Exception as e:
        log.error(f"Erreur Webhook: {e}")
        return jsonify({"status": "error"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
