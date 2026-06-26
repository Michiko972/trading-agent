"""
Agent Trading IA v5.2 - FIXE (Erreur Size)
Gestion dynamique des minimums par instrument
"""

import os
import json
import logging
import requests
import sys
from flask import Flask, request, jsonify

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()
API_URL = "https://demo-api-capital.backend-capital.com/api/v1"
RISK_PER_TRADE_USD = 400.0

app = Flask(__name__)

def get_session():
    try:
        res = requests.post(f"{API_URL}/session", headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
                            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False}, timeout=10)
        if res.status_code != 200: return None
        return {"X-CAP-API-KEY": API_KEY, "CST": res.headers.get("CST"), "X-SECURITY-TOKEN": res.headers.get("X-SECURITY-TOKEN"), "Content-Type": "application/json"}
    except: return None

def get_market_min_size(epic, headers):
    """Récupère le minimum autorisé pour l'actif"""
    try:
        res = requests.get(f"{API_URL}/markets/{epic}", headers=headers, timeout=10)
        data = res.json()
        return float(data["dealingRules"]["minDealSize"]["value"])
    except: return 0.1 # Valeur par défaut prudente

def open_position(direction, epic, headers):
    min_size = get_market_min_size(epic, headers)
    # Calcul : Risque / Distance (15 points/pips). 
    # Pour USDJPY, si le résultat est < min_size, on force min_size pour éviter l'erreur.
    raw_size = RISK_PER_TRADE_USD / 15.0
    final_size = max(raw_size, min_size)
    
    payload = {
        "epic": epic,
        "direction": "BUY" if direction == "long" else "SELL",
        "size": round(final_size, 2),
        "guaranteedStop": True,
        "stopDistance": 15.0,
        "profitDistance": 22.5
    }
    
    res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
    log.info(f"REPONSE BROKER POUR {epic} (Size {final_size}): {res.status_code} - {res.text}")
    return res.status_code == 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        log.info(f"SIGNAL RECU: {data}")
        headers = get_session()
        if not headers: return jsonify({"status": "error"}), 500
        
        success = open_position(data.get("signal"), data.get("epic", "USDJPY"), headers)
        return jsonify({"status": "ok" if success else "failed"})
    except Exception as e:
        log.error(f"ERREUR: {e}")
        return jsonify({"status": "error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
