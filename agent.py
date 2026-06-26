"""
Agent Trading IA v5.4 — Production Ready
Gestion automatique du Risque ($400) et des Règles Broker
"""
import os
import logging
import requests
import sys
from flask import Flask, request, jsonify

# --- CONFIGURATION LOGGING ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# --- CONFIGURATION VARIABLES ---
API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()
API_URL = "https://demo-api-capital.backend-capital.com/api/v1"
RISK_AMOUNT = 400.0

app = Flask(__name__)

def get_session():
    try:
        res = requests.post(f"{API_URL}/session", 
                            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
                            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False}, timeout=10)
        if res.status_code == 200:
            return {"X-CAP-API-KEY": API_KEY, "CST": res.headers.get("CST"), "X-SECURITY-TOKEN": res.headers.get("X-SECURITY-TOKEN"), "Content-Type": "application/json"}
    except Exception as e:
        log.error(f"Auth error: {e}")
    return None

def get_market_rules(epic, headers):
    """Récupère les contraintes du broker pour éviter les erreurs 400/Minvalue"""
    try:
        res = requests.get(f"{API_URL}/markets/{epic}", headers=headers, timeout=10)
        data = res.json()
        rules = data.get("dealingRules", {})
        return {
            "min_stop": float(rules.get("minNormalStopOrLimitDistance", {}).get("value", 15.0)),
            "min_size": float(rules.get("minDealSize", {}).get("value", 0.01))
        }
    except:
        return {"min_stop": 15.0, "min_size": 0.01}

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        log.info(f"SIGNAL REÇU: {data}")
        
        headers = get_session()
        if not headers: return jsonify({"status": "error", "message": "Auth failed"}), 500
        
        epic, signal = data.get("epic"), data.get("signal")
        rules = get_market_rules(epic, headers)
        
        # CALCUL DE TAILLE : Risque de 400$ / distance de stop minimale
        # Cela empêche les tailles erronées (trop grosses ou trop petites)
        size = round(max(rules["min_size"], (RISK_AMOUNT / rules["min_stop"])), 2)
        
        payload = {
            "epic": epic,
            "direction": "BUY" if signal == "long" else "SELL",
            "size": size,
            "guaranteedStop": True,
            "stopDistance": rules["min_stop"],
            "profitDistance": rules["min_stop"] * 1.5
        }
        
        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        log.info(f"BROKER RESPONSE: {res.status_code} | Payload: {payload} | Message: {res.text}")
        
        return jsonify({"status": "success" if res.status_code == 200 else "failed", "res": res.text})
    except Exception as e:
        log.error(f"CRITICAL ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/", methods=["GET"])
def health():
    return "Agent v5.4 Actif", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
