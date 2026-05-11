"""
Agent de Trading Automatique v2
Architecture : Pine Script = capteur, Agent IA = décideur
Broker : Capital.com (démo)
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ══════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════
API_KEY      = os.environ.get("CAPITAL_API_KEY", "")
API_PASSWORD = os.environ.get("CAPITAL_API_PASSWORD", "")
API_EMAIL    = os.environ.get("CAPITAL_EMAIL", "")
API_URL      = "https://demo-api-capital.backend-capital.com/api/v1"
# EPIC défini dynamiquement depuis le signal TradingView
DEFAULT_EPIC = "BTCUSD"

CAPITAL_DEMO     = 1000.0
RISK_PCT         = 0.01
DAILY_LOSS_LIMIT = 0.02
MAX_DRAWDOWN_PCT = 0.04
PROFIT_TARGET    = 0.06
TP1_RATIO        = 1.5
TP2_RATIO        = 3.0
CONSISTENCY_MAX  = 0.50

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("agent.log")]
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  ÉTAT DU COMPTE
# ══════════════════════════════════════════════
class AccountState:
    def __init__(self):
        self.capital       = CAPITAL_DEMO
        self.peak_equity   = CAPITAL_DEMO
        self.daily_pnl     = 0.0
        self.total_pnl     = 0.0
        self.best_day_pnl  = 0.0
        self.trades_today  = 0
        self.position_open = False
        self.position_side = None
        self.position_size = 0.0
        self.entry_price   = 0.0
        self.stop_loss     = 0.0
        self.take_profit1  = 0.0
        self.take_profit2  = 0.0
        self.last_day      = datetime.now(timezone.utc).date()
        # Historique des signaux pour détecter les ranges
        self.signal_history = []

    def reset_daily(self):
        today = datetime.now(timezone.utc).date()
        if today != self.last_day:
            if self.daily_pnl > self.best_day_pnl:
                self.best_day_pnl = self.daily_pnl
            self.daily_pnl    = 0.0
            self.trades_today = 0
            self.last_day     = today
            log.info("Nouveau jour — Reset PnL journalier")

    def can_trade(self):
        self.reset_daily()
        if self.daily_pnl <= -(CAPITAL_DEMO * DAILY_LOSS_LIMIT):
            return False, "daily_loss_limit"
        drawdown = self.peak_equity - self.capital
        if drawdown >= CAPITAL_DEMO * MAX_DRAWDOWN_PCT:
            return False, "max_drawdown"
        if self.total_pnl >= CAPITAL_DEMO * PROFIT_TARGET:
            return False, "profit_target_reached"
        if self.position_open:
            return False, "position_already_open"
        return True, "ok"

    def update_after_trade(self, pnl):
        self.daily_pnl  += pnl
        self.total_pnl  += pnl
        self.capital    += pnl
        if self.capital > self.peak_equity:
            self.peak_equity = self.capital
        self.trades_today += 1
        log.info(f"Trade | PnL: {pnl:+.2f}€ | Jour: {self.daily_pnl:+.2f}€ | Total: {self.total_pnl:+.2f}€")


state = AccountState()


# ══════════════════════════════════════════════
#  MOTEUR DE DÉCISION INTELLIGENT
# ══════════════════════════════════════════════
class TradingDecisionEngine:
    """
    L'agent IA décide — pas Pine Script.
    Pine Script envoie le contexte, l'agent analyse et pondère.
    Logique simplifiée : score basé sur pivot, ADX et DI uniquement.
    """

    def analyze(self, data):
        signal     = data.get("signal", "")
        adx        = float(data.get("adx", 0))
        adx_rising = data.get("adx_rising", False)
        last_pivot = data.get("last_pivot", "")
        di_plus    = float(data.get("di_plus", 0))
        di_minus   = float(data.get("di_minus", 0))
        price      = float(data.get("price", 0))

        log.info(f"Contexte reçu: signal={signal}, adx={adx:.1f}, adx_rising={adx_rising}, pivot={last_pivot}, DI+={di_plus:.1f}, DI-={di_minus:.1f}")

        # ── SCORE DE CONFIANCE ──
        score = 0

        # ADX > 20 — tendance présente
        if adx > 20:
            score += 30

        # ADX en hausse — tendance qui s'accélère
        if adx_rising:
            score += 20

        # Pivot cohérent avec le signal
        if signal == "long" and last_pivot == "low":
            score += 30
        elif signal == "short" and last_pivot == "high":
            score += 30
        else:
            score += 10  # pivot incohérent mais pas bloquant

        # DI cohérent avec le signal
        if signal == "long" and di_plus > di_minus:
            score += 20
        elif signal == "short" and di_minus > di_plus:
            score += 20

        log.info(f"Score de confiance: {score}/100")

        # Seuil minimum : 50/100
        if score < 50:
            return False, "low_confidence", score, f"Score insuffisant ({score}/100) — setup faible"

        return True, "entry_validated", score, f"Setup validé — score {score}/100"


engine = TradingDecisionEngine()


# ══════════════════════════════════════════════
#  API CAPITAL.COM
# ══════════════════════════════════════════════
def get_session():
    try:
        r = requests.post(
            f"{API_URL}/session",
            headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
            json={"identifier": API_EMAIL, "password": API_PASSWORD, "encryptedPassword": False},
            timeout=10
        )
        if r.status_code == 200:
            return r.headers.get("CST"), r.headers.get("X-SECURITY-TOKEN")
        log.error(f"Session error: {r.text}")
        return None, None
    except Exception as e:
        log.error(f"Session exception: {e}")
        return None, None


def get_headers():
    cst, xst = get_session()
    return {
        "X-CAP-API-KEY": API_KEY,
        "CST": cst or "",
        "X-SECURITY-TOKEN": xst or "",
        "Content-Type": "application/json"
    }


def calculate_position_size(entry_price, stop_price):
    risk_amount    = state.capital * RISK_PCT
    price_distance = abs(entry_price - stop_price)
    if price_distance == 0:
        return 0.0001
    size = round(risk_amount / price_distance, 4)
    return max(0.0001, size)


def open_position(direction, entry_price, stop_price, epic=DEFAULT_EPIC):
    size = calculate_position_size(entry_price, stop_price)
    dist = abs(entry_price - stop_price)

    if direction == "long":
        tp1 = entry_price + dist * TP1_RATIO
        tp2 = entry_price + dist * TP2_RATIO
    else:
        tp1 = entry_price - dist * TP1_RATIO
        tp2 = entry_price - dist * TP2_RATIO

    log.info(f"""
    ══════════════════════════════════
    ORDRE {direction.upper()} — {epic}
    Entrée   : {entry_price:.2f}
    Stop     : {stop_price:.2f}
    TP1      : {tp1:.2f} (×{TP1_RATIO})
    TP2      : {tp2:.2f} (×{TP2_RATIO})
    Taille   : {size} BTC
    Risque   : {state.capital * RISK_PCT:.2f}€
    ══════════════════════════════════
    """)

    try:
        payload = {
            "epic": epic,
            "direction": "BUY" if direction == "long" else "SELL",
            "size": size,
            "guaranteedStop": False,
            "stopLevel": stop_price,
            "profitLevel": tp1
        }
        r = requests.post(f"{API_URL}/positions", headers=get_headers(), json=payload, timeout=10)
        if r.status_code == 200:
            state.position_open = True
            state.position_side = direction
            state.position_size = size
            state.entry_price   = entry_price
            state.stop_loss     = stop_price
            state.take_profit1  = tp1
            state.take_profit2  = tp2
            log.info(f"Position ouverte — Deal: {r.json().get('dealId', 'N/A')}")
            return True
        log.error(f"Erreur ouverture: {r.text}")
        return False
    except Exception as e:
        log.error(f"Exception ouverture: {e}")
        return False


def close_all_positions(epic=DEFAULT_EPIC):
    try:
        r = requests.get(f"{API_URL}/positions", headers=get_headers(), timeout=10)
        if r.status_code == 200:
            for pos in r.json().get("positions", []):
                if pos["market"]["epic"] == epic:
                    deal_id = pos["position"]["dealId"]
                    cr = requests.delete(f"{API_URL}/positions/{deal_id}", headers=get_headers(), timeout=10)
                    if cr.status_code == 200:
                        pnl = pos["position"].get("upl", 0)
                        state.update_after_trade(pnl)
                        state.position_open = False
                        log.info(f"Position fermée — PnL: {pnl:.2f}€")
                        return True
    except Exception as e:
        log.error(f"Exception fermeture: {e}")
    return False


# ══════════════════════════════════════════════
#  CALCUL DU STOP LOSS
# ══════════════════════════════════════════════
def calculate_stop(signal, price, data):
    atr_pct = 0.015  # 1.5% par défaut sur BTC
    if signal == "long":
        pivot_low = float(data.get("pivot_low", 0)) if data.get("pivot_low") else 0
        if pivot_low > 0:
            return pivot_low * 0.998
        return price * (1 - atr_pct)
    else:
        pivot_high = float(data.get("pivot_high", 0)) if data.get("pivot_high") else 0
        if pivot_high > 0:
            return pivot_high * 1.002
        return price * (1 + atr_pct)


# ══════════════════════════════════════════════
#  SERVEUR WEBHOOK
# ══════════════════════════════════════════════
app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        log.info(f"Signal reçu: {json.dumps(data)}")

        can, reason = state.can_trade()
        if not can:
            log.warning(f"Trading bloqué: {reason}")
            return jsonify({"status": "blocked", "reason": reason}), 200

        should_enter, reason, score, message = engine.analyze(data)
        log.info(f"Décision: {reason} — {message}")

        if should_enter:
            signal = data.get("signal")
            price  = float(data.get("price", 0))
            stop   = calculate_stop(signal, price, data)
            # Symbole dynamique depuis l'alerte TradingView
            epic   = data.get("epic", DEFAULT_EPIC)

            if price and stop:
                success = open_position(signal, price, stop, epic)
                return jsonify({
                    "status": "trade_opened" if success else "order_failed",
                    "direction": signal,
                    "price": price,
                    "stop": stop,
                    "score": score,
                    "message": message
                }), 200

        return jsonify({
            "status": "no_trade",
            "reason": reason,
            "score": score,
            "message": message
        }), 200

    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    state.reset_daily()
    can, reason = state.can_trade()
    return jsonify({
        "capital":       round(state.capital, 2),
        "total_pnl":     round(state.total_pnl, 2),
        "daily_pnl":     round(state.daily_pnl, 2),
        "best_day":      round(state.best_day_pnl, 2),
        "drawdown":      round(state.peak_equity - state.capital, 2),
        "trades_today":  state.trades_today,
        "position_open": state.position_open,
        "position_side": state.position_side,
        "entry_price":   state.entry_price,
        "stop_loss":     state.stop_loss,
        "take_profit1":  state.take_profit1,
        "take_profit2":  state.take_profit2,
        "objectif":      f"{round(state.total_pnl / (CAPITAL_DEMO * PROFIT_TARGET) * 100, 1)}%",
        "can_trade":     can,
        "block_reason":  reason if not can else None
    }), 200


@app.route("/close", methods=["POST"])
def close():
    if state.position_open:
        success = close_all_positions()
        return jsonify({"status": "closed" if success else "error"}), 200
    return jsonify({"status": "no_position"}), 200


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Agent trading actif", "mode": "DEMO", "version": "2.0"}), 200


if __name__ == "__main__":
    log.info("=" * 50)
    log.info("Agent Trading v2 — MODE DEMO")
    log.info(f"Capital: {CAPITAL_DEMO}€ | Objectif: {CAPITAL_DEMO * PROFIT_TARGET}€")
    log.info(f"Stop journalier: {CAPITAL_DEMO * DAILY_LOSS_LIMIT}€ | Drawdown max: {CAPITAL_DEMO * MAX_DRAWDOWN_PCT}€")
    log.info("=" * 50)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
