"""
Agent de Trading Automatique v3
Architecture :
- Pine Script = détecteur d'opportunités
- Agent = décideur contextuel intelligent

Broker : Capital.com (DEMO)
"""

import os
import json
import logging
import requests

from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ══════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════

API_KEY = os.environ.get("CAPITAL_API_KEY", "")
API_PASSWORD = os.environ.get("CAPITAL_API_PASSWORD", "")

API_URL = "https://demo-api-capital.backend-capital.com/api/v1"

DEFAULT_EPIC = "BTCUSD"

CAPITAL_DEMO = 1000.0

RISK_PCT = 0.01
DAILY_LOSS_LIMIT = 0.02
MAX_DRAWDOWN_PCT = 0.04
PROFIT_TARGET = 0.06

TP1_RATIO = 1.5
TP2_RATIO = 3.0

# ══════════════════════════════════════
# LOGGING
# ══════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("agent.log")
    ]
)

log = logging.getLogger(__name__)

# ══════════════════════════════════════
# ÉTAT DU COMPTE
# ══════════════════════════════════════

class AccountState:

    def __init__(self):

        self.capital = CAPITAL_DEMO
        self.peak_equity = CAPITAL_DEMO

        self.daily_pnl = 0.0
        self.total_pnl = 0.0

        self.best_day_pnl = 0.0

        self.trades_today = 0

        self.position_open = False
        self.position_side = None

        self.position_size = 0.0

        self.entry_price = 0.0
        self.stop_loss = 0.0

        self.take_profit1 = 0.0
        self.take_profit2 = 0.0

        self.last_day = datetime.now(timezone.utc).date()

    def reset_daily(self):

        today = datetime.now(timezone.utc).date()

        if today != self.last_day:

            if self.daily_pnl > self.best_day_pnl:
                self.best_day_pnl = self.daily_pnl

            self.daily_pnl = 0.0
            self.trades_today = 0
            self.last_day = today

            log.info("Reset journalier")

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

        self.daily_pnl += pnl
        self.total_pnl += pnl

        self.capital += pnl

        if self.capital > self.peak_equity:
            self.peak_equity = self.capital

        self.trades_today += 1

        log.info(
            f"Trade clôturé | PnL {pnl:+.2f}€ | "
            f"Capital {self.capital:.2f}€"
        )

state = AccountState()

# ══════════════════════════════════════
# MOTEUR DE DÉCISION
# ══════════════════════════════════════

class TradingDecisionEngine:

    def analyze(self, data):

        signal = data.get("signal", "")

        adx = float(data.get("adx", 0))
        adx_rising = data.get("adx_rising", False)

        macd_state = data.get("macd_state", "")

        last_pivot = data.get("last_pivot", "")
        bars_since = int(data.get("bars_since_pivot", 99))

        volatility = data.get("volatility", "low")
        impulse = data.get("impulse", "")

        di_plus = float(data.get("di_plus", 0))
        di_minus = float(data.get("di_minus", 0))

        log.info(
            f"""
══════════════════════════════════
ANALYSE SIGNAL

Signal       : {signal}
ADX          : {adx}
ADX Rising   : {adx_rising}
MACD         : {macd_state}
Pivot        : {last_pivot}
Bars Pivot   : {bars_since}
Volatility   : {volatility}
Impulse      : {impulse}
DI+          : {di_plus}
DI-          : {di_minus}

══════════════════════════════════
"""
        )

        # ──────────────────────────────
        # REFUS FORTS
        # ──────────────────────────────

        if adx < 18 and not adx_rising:

            return (
                False,
                "range_market",
                0,
                "Marché sans tendance"
            )

        if bars_since > 15:

            return (
                False,
                "pivot_too_old",
                0,
                "Pivot trop ancien"
            )

        # ──────────────────────────────
        # SCORE
        # ──────────────────────────────

        score = 50

        reasons = []

        # ───── ADX

        if adx >= 20:
            score += 10
            reasons.append("ADX > 20")

        if adx >= 25:
            score += 10
            reasons.append("ADX fort")

        if adx >= 30:
            score += 5
            reasons.append("ADX très fort")

        if adx_rising:
            score += 15
            reasons.append("ADX accélère")

        # ───── PIVOT

        if signal == "long":

            if last_pivot == "low":
                score += 15
                reasons.append("Pivot long cohérent")
            else:
                score -= 10
                reasons.append("Pivot incohérent")

        elif signal == "short":

            if last_pivot == "high":
                score += 15
                reasons.append("Pivot short cohérent")
            else:
                score -= 10
                reasons.append("Pivot incohérent")

        # ───── MACD

        if signal == "long":

            if macd_state == "bullish":
                score += 10
                reasons.append("MACD bullish")
            else:
                score -= 15
                reasons.append("MACD incohérent")

        elif signal == "short":

            if macd_state == "bearish":
                score += 10
                reasons.append("MACD bearish")
            else:
                score -= 15
                reasons.append("MACD incohérent")

        # ───── DI

        if signal == "long":

            if di_plus > di_minus:
                score += 10
                reasons.append("DI+ dominant")
            else:
                score -= 5
                reasons.append("DI incohérent")

        elif signal == "short":

            if di_minus > di_plus:
                score += 10
                reasons.append("DI- dominant")
            else:
                score -= 5
                reasons.append("DI incohérent")

        # ───── RÉCENCE PIVOT

        if bars_since <= 2:
            score += 15
            reasons.append("Pivot très récent")

        elif bars_since <= 5:
            score += 10
            reasons.append("Pivot récent")

        elif bars_since <= 8:
            score += 5
            reasons.append("Pivot acceptable")

        # ───── VOLATILITÉ

        if volatility == "high":
            score += 5
            reasons.append("Bonne volatilité")

        # ───── IMPULSION

        if signal == "long" and impulse == "bullish":
            score += 5
            reasons.append("Impulsion bullish")

        if signal == "short" and impulse == "bearish":
            score += 5
            reasons.append("Impulsion bearish")

        score = max(0, min(score, 100))

        log.info(
            f"""
══════════════════════════════════
SCORE FINAL : {score}/100

{', '.join(reasons)}

══════════════════════════════════
"""
        )

        if score >= 70:

            return (
                True,
                "high_confidence",
                score,
                f"Setup fort ({score}/100)"
            )

        elif score >= 55:

            return (
                True,
                "medium_confidence",
                score,
                f"Setup acceptable ({score}/100)"
            )

        else:

            return (
                False,
                "low_confidence",
                score,
                f"Setup refusé ({score}/100)"
            )

engine = TradingDecisionEngine()

# ══════════════════════════════════════
# CAPITAL API
# ══════════════════════════════════════

def get_session():

    try:

        r = requests.post(
            f"{API_URL}/session",
            headers={
                "X-CAP-API-KEY": API_KEY
            },
            json={
                "identifier": API_KEY,
                "password": API_PASSWORD
            },
            timeout=10
        )

        if r.status_code == 200:

            return (
                r.headers.get("CST"),
                r.headers.get("X-SECURITY-TOKEN")
            )

        log.error(f"Erreur session : {r.text}")

        return None, None

    except Exception as e:

        log.error(f"Session exception : {e}")

        return None, None

def get_headers():

    cst, xst = get_session()

    return {
        "X-CAP-API-KEY": API_KEY,
        "CST": cst or "",
        "X-SECURITY-TOKEN": xst or "",
        "Content-Type": "application/json"
    }

# ══════════════════════════════════════
# POSITION SIZE
# ══════════════════════════════════════

def calculate_position_size(entry_price, stop_price):

    risk_amount = state.capital * RISK_PCT

    distance = abs(entry_price - stop_price)

    if distance == 0:
        return 0.01

    size = round(risk_amount / distance, 2)

    return max(size, 0.01)

# ══════════════════════════════════════
# STOP LOSS
# ══════════════════════════════════════

def calculate_stop(signal, price, data):

    if signal == "long":

        pivot_low = float(data.get("pivot_low", 0))

        if pivot_low > 0:
            return pivot_low * 0.998

        return price * 0.985

    else:

        pivot_high = float(data.get("pivot_high", 0))

        if pivot_high > 0:
            return pivot_high * 1.002

        return price * 1.015

# ══════════════════════════════════════
# OUVERTURE POSITION
# ══════════════════════════════════════

def open_position(direction, entry_price, stop_price, epic):

    size = calculate_position_size(
        entry_price,
        stop_price
    )

    risk_distance = abs(entry_price - stop_price)

    if direction == "long":

        tp1 = entry_price + (risk_distance * TP1_RATIO)
        tp2 = entry_price + (risk_distance * TP2_RATIO)

    else:

        tp1 = entry_price - (risk_distance * TP1_RATIO)
        tp2 = entry_price - (risk_distance * TP2_RATIO)

    log.info(
        f"""
══════════════════════════════════
OUVERTURE POSITION

EPIC        : {epic}
Direction   : {direction}
Entrée      : {entry_price}
Stop        : {stop_price}
TP1         : {tp1}
TP2         : {tp2}
Size        : {size}

══════════════════════════════════
"""
    )

    try:

        payload = {
            "epic": epic,
            "direction": "BUY" if direction == "long" else "SELL",
            "size": size,
            "guaranteedStop": False,
            "stopLevel": stop_price,
            "profitLevel": tp1
        }

        r = requests.post(
            f"{API_URL}/positions",
            headers=get_headers(),
            json=payload,
            timeout=10
        )

        if r.status_code == 200:

            state.position_open = True
            state.position_side = direction

            state.position_size = size

            state.entry_price = entry_price
            state.stop_loss = stop_price

            state.take_profit1 = tp1
            state.take_profit2 = tp2

            log.info("Position ouverte")

            return True

        log.error(f"Erreur ouverture : {r.text}")

        return False

    except Exception as e:

        log.error(f"Exception ouverture : {e}")

        return False

# ══════════════════════════════════════
# WEBHOOK
# ══════════════════════════════════════

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        data = request.get_json(force=True)

        log.info(f"Signal reçu : {json.dumps(data)}")

        can_trade, reason = state.can_trade()

        if not can_trade:

            return jsonify({
                "status": "blocked",
                "reason": reason
            }), 200

        should_enter, reason, score, message = engine.analyze(data)

        log.info(f"Décision : {message}")

        if should_enter:

            signal = data.get("signal")

            price = float(data.get("price", 0))

            epic = data.get(
                "epic",
                DEFAULT_EPIC
            )

            stop = calculate_stop(
                signal,
                price,
                data
            )

            success = open_position(
                signal,
                price,
                stop,
                epic
            )

            return jsonify({
                "status": "trade_opened" if success else "order_failed",
                "signal": signal,
                "epic": epic,
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

        log.error(f"Webhook error : {e}")

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ══════════════════════════════════════
# STATUS
# ══════════════════════════════════════

@app.route("/status", methods=["GET"])
def status():

    can_trade, reason = state.can_trade()

    return jsonify({

        "capital": round(state.capital, 2),

        "daily_pnl": round(state.daily_pnl, 2),
        "total_pnl": round(state.total_pnl, 2),

        "position_open": state.position_open,
        "position_side": state.position_side,

        "entry_price": state.entry_price,

        "stop_loss": state.stop_loss,

        "take_profit1": state.take_profit1,
        "take_profit2": state.take_profit2,

        "can_trade": can_trade,
        "reason": reason

    }), 200

# ══════════════════════════════════════
# HOME
# ══════════════════════════════════════

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "Agent actif",
        "version": "3.0"
    }), 200

# ══════════════════════════════════════
# START
# ══════════════════════════════════════

if __name__ == "__main__":

    log.info("=" * 50)
    log.info("AGENT TRADING v3")
    log.info("=" * 50)

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
