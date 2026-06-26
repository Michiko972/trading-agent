"""
Agent Trading IA v6.5 — Structure Gemini + Règles Topstep
Architecture v6.4 préservée + gestion du risque journalier/total
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

# --- CONFIGURATION CAPITAL / RISQUE (règles Topstep 50K) ---
CAPITAL_START = 50000.0
RISK_PCT = 0.005          # 0.5% du capital par trade = 250€ sur 50000€
DAILY_LOSS_LIMIT = 1000.0  # Perte journalière max autorisée
MAX_DRAWDOWN = 2000.0      # Perte totale max autorisée (trailing)
PROFIT_TARGET = 3000.0     # Objectif de profit pour réussir le Combine
TP_RATIO = 1.5             # Take profit = 1.5x la distance du stop


class AccountState:

    def __init__(self):
        self.last_trade_time = None
        self.position_open = False
        self.position_side = None

        # Suivi du P&L pour les règles Topstep
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.current_day = datetime.now(timezone.utc).date()
        self.trading_halted = False
        self.halt_reason = None

    def reset_daily_if_new_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self.current_day:
            log.info(f"=== NOUVEAU JOUR === Reset daily_pnl (était {self.daily_pnl})")
            self.daily_pnl = 0.0
            self.current_day = today

    def update_pnl(self, trade_pnl):
        self.reset_daily_if_new_day()
        self.daily_pnl += trade_pnl
        self.total_pnl += trade_pnl
        log.info(f"P&L mis à jour | trade={trade_pnl} | daily={self.daily_pnl} | total={self.total_pnl}")
        self.check_limits()

    def check_limits(self):

        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            self.trading_halted = True
            self.halt_reason = f"Limite de perte journalière atteinte ({self.daily_pnl}€)"
            log.error(f"=== TRADING STOPPE === {self.halt_reason}")

        if self.total_pnl <= -MAX_DRAWDOWN:
            self.trading_halted = True
            self.halt_reason = f"Drawdown maximum atteint ({self.total_pnl}€)"
            log.error(f"=== TRADING STOPPE === {self.halt_reason}")

        if self.total_pnl >= PROFIT_TARGET:
            log.info(f"=== OBJECTIF ATTEINT === Profit total: {self.total_pnl}€ (objectif: {PROFIT_TARGET}€)")

    def can_trade(self):

        self.reset_daily_if_new_day()

        if self.trading_halted:
            return False, self.halt_reason

        if self.position_open:
            return False, "position_already_open"

        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            return False, f"daily_loss_limit_reached ({self.daily_pnl}€)"

        if self.total_pnl <= -MAX_DRAWDOWN:
            return False, f"max_drawdown_reached ({self.total_pnl}€)"

        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < 60:
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
            log.info("CONNEXION CAPITAL OK")
            return {
                "X-CAP-API-KEY": API_KEY,
                "CST": response.headers.get("CST"),
                "X-SECURITY-TOKEN": response.headers.get("X-SECURITY-TOKEN"),
                "Content-Type": "application/json"
            }
        else:
            log.error(f"ECHEC CONNEXION CAPITAL | Status={response.status_code} | {response.text}")
    except Exception as e:
        log.error(f"Erreur session: {e}")
    return None


def get_market_rules(epic, headers):
    try:
        res = requests.get(f"{API_URL}/markets/{epic}", headers=headers, timeout=10)
        data = res.json()
        rules = data.get("dealingRules", {})
        snapshot = data.get("snapshot", {})

        return {
            "min_stop_raw": float(rules.get("minNormalStopOrLimitDistance", {}).get("value", 20.0)),
            "min_size": float(rules.get("minDealSize", {}).get("value", 0.1)),
            "decimals": int(snapshot.get("decimalPlacesFactor", 2))
        }
    except Exception as e:
        log.error(f"Erreur market rules: {e}")
        return {"min_stop_raw": 30.0, "min_size": 0.1, "decimals": 2}


def get_real_positions(headers):
    """Récupère les positions réellement ouvertes sur Capital.com."""
    try:
        res = requests.get(f"{API_URL}/positions", headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("positions", [])
    except Exception as e:
        log.error(f"Erreur get_real_positions: {e}")
    return []


def sync_position_state(headers):
    """Vérifie auprès de Capital.com l'état réel des positions."""

    positions = get_real_positions(headers)

    if len(positions) == 0:
        if state.position_open:
            log.info("Aucune position réelle — position fermée (TP/SL probablement touché)")
            state.position_open = False
    else:
        state.position_open = True


def calculate_stop_distance(price, epic, min_stop_raw):
    """Calcule une distance de stop cohérente, en ignorant min_stop_raw
    s'il est disproportionné par rapport au prix (bug connu sur certains
    marchés où Capital.com retourne une unité incohérente)."""

    stop_distance = price * 0.003

    if min_stop_raw < price * 0.02:
        stop_distance = max(stop_distance, min_stop_raw)
    else:
        log.info(f"min_stop_raw ({min_stop_raw}) ignoré — disproportionné par rapport au prix ({price})")

    return stop_distance


def calculate_position_size(risk_amount, stop_distance, min_size, epic, price):
    """Calcule la taille de position pour respecter le montant de risque visé.
    Distinction forex (valeur de pip spécifique) vs crypto/indices (risque direct)."""

    if stop_distance <= 0:
        return min_size

    is_forex = any(c in epic for c in ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "CAD", "NZD"]) \
               and "BTC" not in epic and "ETH" not in epic

    if is_forex:
        pip_size = 0.01 if "JPY" in epic else 0.0001
        stop_pips = stop_distance / pip_size
        # Valeur d'un pip pour 100 000 unités ≈ 10 (devise de cotation)
        size = (risk_amount / stop_pips) * (100000 / 10)
    else:
        size = risk_amount / stop_distance

    size = round(size, 4)

    return max(size, min_size)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "Agent v6.5 Actif",
        "capital_start": CAPITAL_START,
        "daily_pnl": state.daily_pnl,
        "total_pnl": state.total_pnl,
        "trading_halted": state.trading_halted,
        "halt_reason": state.halt_reason
    })


@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "daily_pnl": state.daily_pnl,
        "total_pnl": state.total_pnl,
        "daily_loss_limit": DAILY_LOSS_LIMIT,
        "max_drawdown": MAX_DRAWDOWN,
        "profit_target": PROFIT_TARGET,
        "trading_halted": state.trading_halted,
        "halt_reason": state.halt_reason,
        "position_open": state.position_open,
        "position_side": state.position_side
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        log.info(f"Signal reçu: {json.dumps(data)}")

        epic = data.get("epic")
        signal = data.get("signal")
        price = float(data.get("price", 0))

        headers = get_session()
        if not headers:
            return jsonify({"status": "error", "message": "session_failed"}), 500

        sync_position_state(headers)

        # === GESTION DES SIGNAUX DE SORTIE ===
        if signal in ("exit_long", "exit_short"):

            if not state.position_open:
                log.info(f"{signal} reçu mais aucune position ouverte")
                return jsonify({"status": "no_position_to_close"})

            expected_side = "long" if signal == "exit_long" else "short"

            if state.position_side != expected_side:
                log.info(f"{signal} ignoré — position actuelle est {state.position_side}")
                return jsonify({"status": "exit_ignored_wrong_side"})

            positions = get_real_positions(headers)

            for pos in positions:
                deal_id = pos.get("position", {}).get("dealId")
                if deal_id:
                    close_res = requests.delete(
                        f"{API_URL}/positions/{deal_id}",
                        headers=headers,
                        timeout=10
                    )
                    log.info(f"Fermeture position {deal_id} | Status={close_res.status_code} | {close_res.text}")

                    if close_res.status_code == 200:
                        pnl = float(pos.get("position", {}).get("upl", 0))
                        state.update_pnl(pnl)

            state.position_open = False
            state.position_side = None

            return jsonify({"status": "position_closed"})

        # === GESTION DES SIGNAUX D'ENTRÉE ===
        if signal not in ("long", "short"):
            return jsonify({"status": "invalid_signal"})

        can_trade, reason = state.can_trade()

        if not can_trade:
            log.info(f"Trading bloqué: {reason}")
            return jsonify({"status": "blocked", "reason": reason})

        rules = get_market_rules(epic, headers)

        stop_distance = calculate_stop_distance(price, epic, rules["min_stop_raw"])

        risk_amount = CAPITAL_START * RISK_PCT

        size = calculate_position_size(risk_amount, stop_distance, rules["min_size"], epic, price)

        direction = "BUY" if signal == "long" else "SELL"

        decimals = rules["decimals"]

        profit_distance = stop_distance * TP_RATIO

        payload = {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": True,
            "stopDistance": round(stop_distance, decimals),
            "profitDistance": round(profit_distance, decimals)
        }

        log.info(f"Payload ordre: {payload} | risk_amount={risk_amount}")

        res = requests.post(f"{API_URL}/positions", headers=headers, json=payload, timeout=10)
        log.info(f"REPONSE BROKER | EPIC: {epic} | STATUS: {res.status_code} | MSG: {res.text}")

        if res.status_code == 200:
            state.position_open = True
            state.position_side = signal
            state.last_trade_time = datetime.now(timezone.utc)
            return jsonify({"status": "trade_opened"})
        else:
            return jsonify({"status": "trade_failed", "broker_response": res.text}), 200

    except Exception as e:
        log.error(f"Erreur Webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    log.info("=== DIAGNOSTIC DEMARRAGE ===")
    log.info(f"API_KEY présente : {bool(API_KEY)}")
    log.info(f"EMAIL présent : {bool(API_EMAIL)}")
    log.info(f"PASSWORD présent : {bool(API_PASSWORD)}")
    log.info(f"Capital de départ : {CAPITAL_START}€")
    log.info(f"Risque par trade : {RISK_PCT*100}% = {CAPITAL_START * RISK_PCT}€")
    log.info(f"Limite perte journalière : {DAILY_LOSS_LIMIT}€")
    log.info(f"Drawdown max : {MAX_DRAWDOWN}€")
    log.info(f"Objectif profit : {PROFIT_TARGET}€")

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
