"""
Agent Trading IA v3.0 — Adaptatif
TradingView -> Railway -> Capital.com
"""

import os
import json
import logging
import requests

from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ==========================================
# CONFIG
# ==========================================

API_KEY = os.environ.get("CAPITAL_API_KEY", "").strip()
API_EMAIL = os.environ.get("CAPITAL_EMAIL", "").strip()
API_PASSWORD = os.environ.get("CAPITAL_PASSWORD", "").strip()

API_URL = "https://demo-api-capital.backend-capital.com/api/v1"

DEFAULT_EPIC = "BTCUSD"

EPIC_MAP = {
    "NASDAQ": "US100",
    "NAS100": "US100",
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
    "EURUSD": "EURUSD",
    "USDJPY": "USDJPY",
    "GBPUSD": "GBPUSD",
}

CAPITAL_DEMO = 1000.0
RISK_PCT = 0.03
TP_RATIO = 1.5

# ==========================================
# LOGS
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger(__name__)

# ==========================================
# STATE
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

            elapsed = (
                datetime.now(timezone.utc)
                - self.last_trade_time
            ).total_seconds()

            if elapsed < 120:
                return False, "cooldown"

        return True, "ok"

state = AccountState()

# ==========================================
# DIAGNOSTIC DEMARRAGE
# ==========================================

log.info("=== DIAGNOSTIC CAPITAL ===")
log.info(f"API_KEY présente : {bool(API_KEY)}")
log.info(f"EMAIL présent : {bool(API_EMAIL)}")
log.info(f"PASSWORD présent : {bool(API_PASSWORD)}")

# ==========================================
# ENGINE
# ==========================================

class TradingDecisionEngine:

    def analyze(self, data):

        signal = data.get("signal", "")

        # La stratégie Pine Script v8 (EMA + RSI + Stoch + ADX)
        # filtre déjà toutes les conditions avant d'envoyer le signal.
        # L'agent fait confiance au signal reçu et ouvre directement.

        if signal in ("long", "short"):
            log.info(f"Signal {signal} reçu — toutes conditions déjà validées par Pine Script")
            return True, 100, "Setup validé (filtré en amont)"

        return False, 0, "Signal invalide"

engine = TradingDecisionEngine()

# ==========================================
# CAPITAL API
# ==========================================

def get_session():

    try:

        response = requests.post(
            f"{API_URL}/session",
            headers={
                "X-CAP-API-KEY": API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "identifier": API_EMAIL,
                "password": API_PASSWORD,
                "encryptedPassword": False
            },
            timeout=10
        )

        if response.status_code != 200:

            log.error(
                f"ECHEC CONNEXION CAPITAL | "
                f"Status={response.status_code} | "
                f"Response={response.text}"
            )

            return None

        log.info("CONNEXION CAPITAL OK")

        cst = response.headers.get("CST")
        xst = response.headers.get("X-SECURITY-TOKEN")

        if not cst or not xst:
            log.error("ECHEC GET_HEADERS | CST ou X-SECURITY-TOKEN manquant")
            return None

        return {
            "X-CAP-API-KEY": API_KEY,
            "CST": cst,
            "X-SECURITY-TOKEN": xst,
            "Content-Type": "application/json"
        }

    except Exception as e:

        log.error(f"Erreur session: {e}")

        return None

# ==========================================
# MARKET RULES
# ==========================================

def get_market_rules(epic, headers):

    try:

        response = requests.get(
            f"{API_URL}/markets/{epic}",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:

            log.error(
                f"ECHEC MARKET RULES | "
                f"Status={response.status_code} | "
                f"Response={response.text}"
            )

            return None

        market = response.json()

        return {
            "min_size": float(
                market["dealingRules"]
                .get("minDealSize", {})
                .get("value", 0.01)
            ),

            "min_stop": float(
                market["dealingRules"]
                .get("minNormalStopOrLimitDistance", {})
                .get("value", 1)
            ),

            "min_guaranteed_stop": float(
                market["dealingRules"]
                .get("minGuaranteedStopOrLimitDistance", {})
                .get("value", 0)
            ),

            "decimals": int(
                market["snapshot"]
                .get("decimalPlacesFactor", 2)
            )
        }

    except Exception as e:

        log.error(f"Erreur market rules: {e}")

        return None


def calculate_atr(epic, headers, period=14, resolution="MINUTE_2"):
    """Calcule l'ATR (Average True Range) à partir des bougies récentes
    récupérées directement depuis Capital.com."""

    try:

        response = requests.get(
            f"{API_URL}/prices/{epic}",
            headers=headers,
            params={
                "resolution": resolution,
                "max": period + 1
            },
            timeout=10
        )

        if response.status_code != 200:
            log.error(f"ECHEC RECUPERATION BOUGIES | Status={response.status_code} | {response.text}")
            return None

        prices = response.json().get("prices", [])

        if len(prices) < 2:
            log.error("Pas assez de bougies pour calculer l'ATR")
            return None

        true_ranges = []

        for i in range(1, len(prices)):

            high = float(prices[i]["highPrice"]["bid"])
            low = float(prices[i]["lowPrice"]["bid"])
            prev_close = float(prices[i - 1]["closePrice"]["bid"])

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )

            true_ranges.append(tr)

        atr = sum(true_ranges) / len(true_ranges)

        log.info(f"ATR calculé sur {len(true_ranges)} bougies ({resolution}) : {atr}")

        return atr

    except Exception as e:
        log.error(f"Erreur calculate_atr: {e}")
        return None

# ==========================================
# POSITION SIZE
# ==========================================

def calculate_position_size(epic, price, stop_distance, min_size):

    risk_amount = state.capital * RISK_PCT

    if stop_distance <= 0:
        return min_size

    # Forex (paires non-JPY) : pip = 0.0001, valeur pip = (taille / 100000) * 10
    # On résout : risk_amount = (stop_distance / 0.0001) * (size / 100000) * 10
    is_forex = any(p in epic for p in ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "CAD", "NZD"]) and "BTC" not in epic and "ETH" not in epic

    if is_forex:

        pip_size = 0.01 if "JPY" in epic else 0.0001

        stop_pips = stop_distance / pip_size

        # valeur d'un pip pour 100000 unités = 10 (approx, en devise de cotation)
        size = (risk_amount / stop_pips) * (100000 / 10)

    else:

        # Crypto / indices : risque direct = size * stop_distance
        size = risk_amount / stop_distance

    size = round(size, 4)

    return max(size, min_size)

# ==========================================
# OPEN POSITION
# ==========================================

def check_real_positions(headers=None):
    """Vérifie auprès de Capital.com si des positions sont réellement ouvertes.
    Met à jour state.position_open en conséquence.
    Retourne les headers utilisés (pour réutilisation immédiate)."""

    if not headers:
        headers = get_session()

    if not headers:
        log.error("Impossible de vérifier les positions réelles (échec session)")
        return None

    try:

        response = requests.get(
            f"{API_URL}/positions",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            log.error(f"Echec vérification positions | Status={response.status_code}")
            return headers

        positions = response.json().get("positions", [])

        if len(positions) == 0:
            if state.position_open:
                log.info("Aucune position réelle trouvée — réinitialisation position_open")
            state.position_open = False
        else:
            state.position_open = True
            log.info(f"Positions réelles ouvertes : {len(positions)}")

        return headers

    except Exception as e:
        log.error(f"Erreur check_real_positions: {e}")
        return headers


def close_all_positions(headers=None):
    """Ferme toutes les positions ouvertes sur Capital.com."""

    if not headers:
        headers = get_session()

    if not headers:
        log.error("Impossible de fermer les positions (échec session)")
        return False

    try:

        response = requests.get(
            f"{API_URL}/positions",
            headers=headers,
            timeout=10
        )

        if response.status_code != 200:
            log.error(f"Echec récupération positions pour fermeture | Status={response.status_code}")
            return False

        positions = response.json().get("positions", [])

        if len(positions) == 0:
            log.info("Aucune position à fermer")
            return True

        all_closed = True

        for pos in positions:

            deal_id = pos.get("position", {}).get("dealId")

            if not deal_id:
                continue

            close_response = requests.delete(
                f"{API_URL}/positions/{deal_id}",
                headers=headers,
                timeout=10
            )

            log.info(f"Fermeture position {deal_id} | Status={close_response.status_code} | {close_response.text}")

            if close_response.status_code != 200:
                all_closed = False

        state.position_open = False

        return all_closed

    except Exception as e:
        log.error(f"Erreur close_all_positions: {e}")
        return False


def open_position(direction, price, epic, headers=None, atr=None):

    log.info("=== OPEN_POSITION ===")
    log.info(f"Direction : {direction}")
    log.info(f"Epic : {epic}")
    log.info(f"Prix : {price}")

    if not headers:
        headers = get_session()

    log.info(f"Headers OK : {headers is not None}")

    if not headers:
        return False

    rules = get_market_rules(epic, headers)

    log.info(f"Rules : {rules}")

    if not rules:
        return False

    min_size = rules["min_size"]
    min_stop_raw = rules["min_guaranteed_stop"] or rules["min_stop"]
    decimals = rules["decimals"]

    # Garde-fou : si min_stop_raw dépasse 5% du prix, c'est probablement
    # une unité incohérente (ex: min_stop=1.0 sur EURUSD = 10000 pips).
    # On l'ignore dans ce cas et on se base uniquement sur notre propre calcul.
    if min_stop_raw > price * 0.05:
        log.info(f"min_stop ({min_stop_raw}) semble disproportionné par rapport au prix ({price}) — ignoré")
        min_stop = 0
    else:
        min_stop = min_stop_raw

    # Distance stop : 0.3% du prix (cohérent avec un timeframe court de 2 minutes)
    stop_distance = max(price * 0.003, min_stop)

    log.info(f"Stop distance utilisé : {stop_distance} (min_stop brut Capital.com : {min_stop_raw})")

    is_forex_pair = any(c in epic for c in ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "CAD", "NZD"]) and "BTC" not in epic and "ETH" not in epic

    if is_forex_pair:
        pip_size = 0.01 if "JPY" in epic else 0.0001
        min_stop_pips = min_stop / pip_size
        stop_distance_pips = stop_distance / pip_size
        log.info(f"Min stop garanti : {min_stop} = {min_stop_pips:.1f} pips")
        log.info(f"Stop distance utilisé : {stop_distance} = {stop_distance_pips:.1f} pips")

    log.info(f"Stop distance : {stop_distance} (min garanti : {min_stop})")

    if direction == "long":

        side = "BUY"

        stop_level = price - stop_distance
        take_profit = price + (stop_distance * TP_RATIO)

    else:

        side = "SELL"

        stop_level = price + stop_distance
        take_profit = price - (stop_distance * TP_RATIO)

    guaranteed_stop = True

    size = calculate_position_size(
        epic,
        price,
        stop_distance,
        min_size
    )

    payload = {
        "epic": epic,
        "direction": side,
        "size": size,
        "guaranteedStop": True,
        "stopDistance": round(stop_distance, decimals),
        "profitDistance": round(stop_distance * TP_RATIO, decimals)
    }

    log.info(f"Payload ordre: {payload}")

    try:

        response = requests.post(
            f"{API_URL}/positions",
            headers=headers,
            json=payload,
            timeout=10
        )

        log.info(f"Status broker : {response.status_code}")
        log.info(f"Réponse broker: {response.text}")

        if response.status_code != 200:

            log.error(
                f"ECHEC OUVERTURE POSITION | "
                f"Status={response.status_code} | "
                f"Response={response.text}"
            )

            return False

        state.position_open = True
        state.position_side = direction
        state.last_trade_time = datetime.now(timezone.utc)

        log.info("Position ouverte")

        return True

    except Exception as e:

        log.error(f"Erreur open_position: {e}")

        return False

# ==========================================
# FLASK
# ==========================================

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "Agent IA adaptatif actif",
        "version": "3.0"
    })

@app.route("/search-market", methods=["GET"])
def search_market():

    term = request.args.get("q", "nasdaq")

    headers = get_session()

    if not headers:
        return jsonify({"error": "auth failed"}), 500

    try:

        response = requests.get(
            f"{API_URL}/markets",
            headers=headers,
            params={"searchTerm": term},
            timeout=10
        )

        return jsonify(response.json())

    except Exception as e:

        return jsonify({"error": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        raw_data = request.data.decode("utf-8")

        log.info(f"RAW WEBHOOK: {raw_data}")

        if not raw_data:

            return jsonify({
                "status": "empty_webhook"
            }), 400

        data = json.loads(raw_data)

        log.info(f"Signal reçu: {json.dumps(data)}")

        signal_type = data.get("signal")

        session_headers = check_real_positions()

        # Signal de sortie : ferme seulement si la position correspond au bon sens
        if signal_type in ("exit_long", "exit_short"):

            if not state.position_open:
                log.info(f"{signal_type} reçu mais aucune position ouverte")
                return jsonify({"status": "no_position_to_close"})

            expected_side = "long" if signal_type == "exit_long" else "short"

            if state.position_side != expected_side:
                log.info(
                    f"{signal_type} reçu mais position actuelle est {state.position_side} "
                    f"— signal ignoré (pas le bon sens)"
                )
                return jsonify({"status": "exit_signal_ignored_wrong_side"})

            log.info(f"=== {signal_type.upper()} — fermeture des positions ===")

            closed = close_all_positions(headers=session_headers)

            return jsonify({
                "status": "positions_closed" if closed else "close_failed"
            })

        can_trade, reason = state.can_trade()

        if not can_trade:

            log.info(f"Trading bloqué: {reason}")

            return jsonify({
                "status": "blocked",
                "reason": reason
            })

        should_enter, score, message = (
            engine.analyze(data)
        )

        log.info(f"Décision: {message}")

        if should_enter:

            signal = data.get("signal")
            price = float(data.get("price", 0))
            atr = float(data.get("atr", 0)) or None
            epic_raw = data.get("epic", DEFAULT_EPIC)
            epic = EPIC_MAP.get(epic_raw, epic_raw)

            log.info(f"Epic reçu: {epic_raw} → converti: {epic}")
            log.info(f"ATR reçu: {atr}")

            success = open_position(
                signal,
                price,
                epic,
                headers=session_headers,
                atr=atr
            )

            return jsonify({
                "status": (
                    "trade_opened"
                    if success
                    else "order_failed"
                ),
                "score": score
            })

        return jsonify({
            "status": "no_trade",
            "score": score,
            "message": message
        })

    except Exception as e:

        log.error(f"Webhook error: {e}")

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ==========================================
# START
# ==========================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(host="0.0.0.0", port=port)
