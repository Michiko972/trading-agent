"""
Agent Trading IA v6.1 — Architecture Complète
Gestionnaire d'ordres sécurisé avec vérification des règles broker
"""
import os
import logging
import requests
import sys
from flask import Flask, request, jsonify

# Configuration des logs pour voir chaque étape dans Railway
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# Paramètres
API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()
API_URL = "https://demo-api-capital.backend-capital.com/api/v1"
RISK_AMOUNT = 400.0

app = Flask(__name__)

def get_session():
    """Initialise ou récupère la session active"""
    try:
        res = requests.post(f"{API_URL}/session", 
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False}, timeout=10)
        if res.status_code == 200:
            return {"X-CAP-API-KEY": API_KEY, "CST": res.headers.get("CST"), "X-SECURITY-TOKEN": res.headers.get("X-SECURITY-TOKEN"), "Content-Type": "application/json"}
    except Exception as e:
        log.error(f"Session Error: {e}")
    return None

def execute_trade(epic, direction, price, headers):
    """Logique de pré-vol avant exécution"""
    # 1. Récupération des règles en temps réel
    market_url = f"{API_URL}/markets/{epic}"
    res = requests.get(market_url, headers=headers, timeout=10)
    if res.status_code != 200:
        log.error(f"Impossible de récupérer les règles pour {epic}")
        return False, "Market Data Error"
    
    rules = res.json().get("dealingRules", {})
    min_stop = float(rules.get("minNormalStopOrLimitDistance", {}).get("value", 30.0))
    min_size = float(rules.get("minDealSize", {}).get("value", 0.01))
    
    # 2. Calcul du risque et taille
    # Normalisation : Le risque de 400$ divisé par la distance minimale
    size = round(max(min_size, (RISK_AMOUNT / min_stop)), 2)
    
    # 3. Construction de l'ordre
    payload = {
        "epic": epic,
        "direction": direction,
        "size": size,
        "guaranteedStop": True,
        "stopDistance": min_stop,
        "profitDistance": min_stop * 1.5
    }
    
    # 4. Exécution
    order_res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
    log.info(f"ORDRE | Epic: {epic} | Size: {size} | Payload: {payload} | Res: {order_res.text}")
    return order_res.status_code == 200, order_res.text

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        epic, signal, price = data.get("epic"), data.get("signal"), float(data.get("price"))
        direction = "BUY" if signal == "long" else "SELL"
        
        headers = get_session()
        if not headers: return jsonify({"status": "error", "message": "Auth Failed"}), 500
        
        success, msg = execute_trade(epic, direction, price, headers)
        return jsonify({"status": "success" if success else "failed", "details": msg})
    except Exception as e:
        log.error(f"Critical Webhook Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route("/", methods=["GET"])
def health():
    return "Agent v6.1 Opérationnel", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
