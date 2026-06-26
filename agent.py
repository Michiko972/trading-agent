"""
Agent Trading IA v4.9 — COMPLET ET FORCE
Railway Deployment Ready
"""

import os
import json
import logging
import requests
import sys
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# Force l'affichage des logs dans la console Railway
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# Configuration
API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()
API_URL = "https://demo-api-capital.backend-capital.com/api/v1"
RISK_PER_TRADE_USD = 400.0

app = Flask(__name__)

# --- FONCTIONS CŒUR ---
def get_session():
    try:
        response = requests.post(
            f"{API_URL}/session",
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False},
            timeout=10
        )
        if response.status_code != 200:
            log.error(f"AUTH ECHEC: {response.text}")
            return None
        return {
            "X-CAP-API-KEY": API_KEY,
            "CST": response.headers.get("CST"),
            "X-SECURITY-TOKEN": response.headers.get("X-SECURITY-TOKEN"),
            "Content-Type": "application/json"
        }
    except Exception as e:
        log.error(f"ERREUR AUTH: {e}")
        return None

def open_position(direction, epic, headers):
    # Calcul dynamique selon l'instrument
    # Utilisation du risque de $400 pour calibrer la taille (size)
    # Pour USDJPY, distance 0.15 * 100 = 15. size = 400 / 15
    size = round(RISK_PER_TRADE_USD / 15.0, 2) 
    
    payload = {
        "epic": epic,
        "direction": "BUY" if direction == "long" else "SELL",
        "size": size,
        "guaranteedStop": True,
        "stopDistance": 15.0,
        "profitDistance": 22.5
    }
    
    res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
    log.info(f"REPONSE BROKER POUR {epic}: {res.status_code} - {res.text}")
    return res.status_code == 200

# --- ROUTES ---
@app.route("/", methods=["GET"])
def home():
    return "Agent Operationnel et En Loute", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        log.info(f"SIGNAL RECU: {data}")
        
        headers = get_session()
        if not headers: return jsonify({"status": "error"}), 500
        
        success = open_position(data.get("signal"), data.get("epic", "US100"), headers)
        return jsonify({"status": "ok" if success else "failed"})
    except Exception as e:
        log.error(f"ERREUR CRITIQUE: {e}")
        return jsonify({"status": "error"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    log.info(f"SERVER STARTING ON PORT {port}")
    app.run(host="0.0.0.0", port=port)
