"""
Agent de Trading Automatique
Stratégie : MACD + DMI + Pivot Points + EMA 200
Broker : Capital.com (démo)
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ══════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════
API_KEY      = os.environ.get("CAPITAL_API_KEY", "")
API_PASSWORD = os.environ.get("CAPITAL_API_PASSWORD", "")
API_URL      = "https://demo-api-capital.backend-capital.com/api/v1"
DEMO_MODE    = True  # Toujours True pour le test

# Paramètres du compte démo (proportionnels au TopStep 50K)
CAPITAL_DEMO     = 1000.0   # Capital démo en euros
RISK_PCT         = 0.01     # 1% de risque par trade
DAILY_LOSS_LIMIT = 0.02     # 2% = 20€ max de perte journalière
MAX_DRAWDOWN_PCT = 0.04     # 4% = 40€ drawdown maximum
PROFIT_TARGET    = 0.06     # 6% = 60€ objectif total
TP1_RATIO        = 1.5      # Take profit 1 = risk × 1.5
TP2_RATIO        = 3.0      # Take profit 2 = risk × 3.0
CONSISTENCY_MAX  = 0.50     # Meilleur jour < 50% du profit total

# Instrument BTC sur Capital.com
EPIC = "BTCUSD"

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("agent.log")
    ]
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  ÉTAT DU COMPTE
# ══════════════════════════════════════════════
class AccountState:
    def __init__(self):
        self.capital          = CAPITAL_DEMO
        self.peak_equity      = CAPITAL_DEMO
        self.daily_pnl        = 0.0
        self.total_pnl        = 0.0
        self.best_day_pnl     = 0.0
        self.trades_today     = 0
        self.position_open    = False
        self.position_side    = None   # "long" ou "short"
        self.position_size    = 0.0
        self.entry_price      = 0.0
        self.stop_loss        = 0.0
        self.take_profit1     = 0.0
        self.take_profit2     = 0.0
        self.session_token    = None
        self.last_day         = datetime.now(timezone.utc).date()

    def reset_daily(self):
        today = datetime.now(timezone.utc).date()
        if today != self.last_day:
            log.info(f"Nouveau jour — Reset PnL journalier (était {self.daily_pnl:.2f}€)")
            if self.daily_pnl > self.best_day_pnl:
                self.best_day_pnl = self.daily_pnl
            self.daily_pnl   = 0.0
            self.trades_today = 0
            self.last_day    = today

    def can_trade(self):
        self.reset_daily()

        # 1. Perte journalière dépassée
        if self.daily_pnl <= -(CAPITAL_DEMO * DAILY_LOSS_LIMIT):
            log.warning(f"STOP — Limite journalière atteinte ({self.daily_pnl:.2f}€)")
            return False, "daily_loss_limit"

        # 2. Drawdown maximum dépassé
        drawdown = self.peak_equity - self.capital
        if drawdown >= CAPITAL_DEMO * MAX_DRAWDOWN_PCT:
            log.warning(f"STOP — Drawdown maximum atteint ({drawdown:.2f}€)")
            return False, "max_drawdown"

        # 3. Objectif atteint
        if self.total_pnl >= CAPITAL_DEMO * PROFIT_TARGET:
            log.info(f"OBJECTIF ATTEINT — Profit total : {self.total_pnl:.2f}€")
            return False, "profit_target_reached"

        # 4. Position déjà ouverte
        if self.position_open:
            return False, "position_already_open"

        # 5. Règle de consistance TopStep
        if self.total_pnl > 0:
            consistency_ratio = self.best_day_pnl / self.total_pnl if self.total_pnl > 0 else 0
            if consistency_ratio >= CONSISTENCY_MAX:
                log.warning(f"PRUDENCE — Consistance à {consistency_ratio*100:.0f}% (max 50%)")

        return True, "ok"

    def update_after_trade(self, pnl):
        self.daily_pnl  += pnl
        self.total_pnl  += pnl
        self.capital    += pnl
        if self.capital > self.peak_equity:
            self.peak_equity = self.capital
        self.trades_today += 1
        log.info(f"Trade clôturé | PnL : {pnl:+.2f}€ | Journalier : {self.daily_pnl:+.2f}€ | Total : {self.total_pnl:+.2f}€")


state = AccountState()

# ══════════════════════════════════════════════
#  API CAPITAL.COM
# ══════════════════════════════════════════════
def get_session_token():
    """Obtenir un token de session Capital.com"""
    try:
        response = requests.post(
            f"{API_URL}/session",
            headers={"X-CAP-API-KEY": API_KEY},
            json={"identifier": API_KEY, "password": API_PASSWORD},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            token = response.headers.get("CST")
            x_token = response.headers.get("X-SECURITY-TOKEN")
            log.info("Connexion Capital.com réussie")
            return token, x_token
        else:
            log.error(f"Erreur connexion Capital.com : {response.text}")
            return None, None
    except Exception as e:
        log.error(f"Erreur connexion : {e}")
        return None, None


def get_headers():
    """Headers pour les requêtes API"""
    cst, x_token = get_session_token()
    return {
        "X-CAP-API-KEY": API_KEY,
        "CST": cst or "",
        "X-SECURITY-TOKEN": x_token or "",
        "Content-Type": "application/json"
    }


def get_current_price(epic):
    """Récupérer le prix actuel"""
    try:
        response = requests.get(
            f"{API_URL}/markets/{epic}",
            headers=get_headers(),
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            bid = data["snapshot"]["bid"]
            ask = data["snapshot"]["offer"]
            return (bid + ask) / 2, bid, ask
        return None, None, None
    except Exception as e:
        log.error(f"Erreur prix : {e}")
        return None, None, None


def calculate_position_size(entry_price, stop_price):
    """
    Calculer la taille de position selon le risque
    Risque = 1% du capital = 10€
    Taille = Risque / (distance en prix × valeur du pip)
    """
    risk_amount = state.capital * RISK_PCT
    price_distance = abs(entry_price - stop_price)
    if price_distance == 0:
        return 0.0001  # taille minimum
    size = risk_amount / price_distance
    size = max(0.0001, round(size, 4))
    log.info(f"Taille position calculée : {size} BTC (risque : {risk_amount:.2f}€)")
    return size


def open_position(direction, entry_price, stop_price, pivot_target):
    """Ouvrir une position sur Capital.com démo"""
    size = calculate_position_size(entry_price, stop_price)
    risk = abs(entry_price - stop_price) * size

    if direction == "long":
        tp1 = entry_price + (entry_price - stop_price) * TP1_RATIO
        tp2 = entry_price + (entry_price - stop_price) * TP2_RATIO
    else:
        tp1 = entry_price - (stop_price - entry_price) * TP1_RATIO
        tp2 = entry_price - (stop_price - entry_price) * TP2_RATIO

    log.info(f"""
    ══════════════════════════════════
    OUVERTURE POSITION {direction.upper()}
    Prix entrée : {entry_price:.2f}
    Stop Loss   : {stop_price:.2f}
    TP1         : {tp1:.2f} (ratio 1:{TP1_RATIO})
    TP2         : {tp2:.2f} (ratio 1:{TP2_RATIO})
    Taille      : {size} BTC
    Risque      : {risk:.2f}€
    ══════════════════════════════════
    """)

    try:
        payload = {
            "epic": EPIC,
            "direction": "BUY" if direction == "long" else "SELL",
            "size": size,
            "guaranteedStop": False,
            "stopLevel": stop_price,
            "profitLevel": tp1  # TP1 comme premier objectif
        }

        response = requests.post(
            f"{API_URL}/positions",
            headers=get_headers(),
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            state.position_open = True
            state.position_side = direction
            state.position_size = size
            state.entry_price   = entry_price
            state.stop_loss     = stop_price
            state.take_profit1  = tp1
            state.take_profit2  = tp2
            log.info(f"Position ouverte avec succès — Deal ID : {data.get('dealId', 'N/A')}")
            return True
        else:
            log.error(f"Erreur ouverture position : {response.text}")
            return False

    except Exception as e:
        log.error(f"Erreur ouverture : {e}")
        return False


def close_position():
    """Fermer la position ouverte"""
    try:
        response = requests.get(
            f"{API_URL}/positions",
            headers=get_headers(),
            timeout=10
        )
        if response.status_code == 200:
            positions = response.json().get("positions", [])
            for pos in positions:
                if pos["market"]["epic"] == EPIC:
                    deal_id = pos["position"]["dealId"]
                    close_resp = requests.delete(
                        f"{API_URL}/positions/{deal_id}",
                        headers=get_headers(),
                        timeout=10
                    )
                    if close_resp.status_code == 200:
                        pnl = pos["position"]["upl"]
                        state.update_after_trade(pnl)
                        state.position_open = False
                        log.info(f"Position fermée — PnL : {pnl:.2f}€")
                        return True
    except Exception as e:
        log.error(f"Erreur fermeture : {e}")
    return False


# ══════════════════════════════════════════════
#  MOTEUR DE DÉCISION
# ══════════════════════════════════════════════
def analyze_signal(data):
    """
    Analyser le signal reçu de TradingView
    et décider si on entre en position
    """
    signal_type  = data.get("signal")       # "long" ou "short"
    macd_cross   = data.get("macd_cross")   # True/False
    di_plus      = data.get("di_plus", 0)   # valeur DI+
    di_minus     = data.get("di_minus", 0)  # valeur DI-
    adx          = data.get("adx", 0)       # valeur ADX
    adx_prev     = data.get("adx_prev", 0)  # ADX bougie précédente
    price        = data.get("price", 0)     # prix actuel
    ema200       = data.get("ema200", 0)    # valeur EMA 200
    pivot_low    = data.get("pivot_low", 0) # dernier pivot low
    pivot_high   = data.get("pivot_high", 0)# dernier pivot high

    log.info(f"""
    Signal reçu : {signal_type}
    MACD croisement : {macd_cross}
    DI+ : {di_plus:.2f} | DI- : {di_minus:.2f}
    ADX : {adx:.2f} (prev : {adx_prev:.2f})
    Prix : {price:.2f} | EMA200 : {ema200:.2f}
    Pivot Low : {pivot_low:.2f} | Pivot High : {pivot_high:.2f}
    """)

    conditions = {}

    if signal_type == "long":
        conditions["macd_cross"]    = macd_cross == True
        conditions["di_favorable"]  = di_plus > di_minus
        conditions["adx_ok"]        = adx >= adx_prev or adx > 20
        conditions["above_ema"]     = price > ema200
        conditions["near_support"]  = pivot_low > 0 and abs(price - pivot_low) / price < 0.005

    elif signal_type == "short":
        conditions["macd_cross"]    = macd_cross == True
        conditions["di_favorable"]  = di_minus > di_plus
        conditions["adx_ok"]        = adx >= adx_prev or adx > 20
        conditions["below_ema"]     = price < ema200
        conditions["near_resist"]   = pivot_high > 0 and abs(price - pivot_high) / price < 0.005

    # Résultat
    passed    = sum(conditions.values())
    total     = len(conditions)
    score     = f"{passed}/{total}"

    log.info(f"Conditions : {conditions}")
    log.info(f"Score : {score}")

    if passed == total:
        log.info(f"✅ TOUTES LES CONDITIONS RÉUNIES — Entrée {signal_type.upper()}")
        return True, signal_type, conditions
    elif passed >= total - 1:
        log.info(f"⚠️ {score} conditions — Entrée refusée, quasi-signal")
        return False, signal_type, conditions
    else:
        log.info(f"❌ {score} conditions — Pas d'entrée")
        return False, signal_type, conditions


# ══════════════════════════════════════════════
#  SERVEUR WEBHOOK (reçoit les alertes TradingView)
# ══════════════════════════════════════════════
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    """Endpoint qui reçoit les alertes de TradingView"""
    try:
        data = request.get_json()
        log.info(f"Alerte reçue : {json.dumps(data, indent=2)}")

        # Vérifier si on peut trader
        can, reason = state.can_trade()
        if not can:
            log.warning(f"Trading bloqué : {reason}")
            return jsonify({"status": "blocked", "reason": reason}), 200

        # Analyser le signal
        should_enter, direction, conditions = analyze_signal(data)

        if should_enter:
            price     = data.get("price", 0)
            stop      = data.get("stop_loss", 0)
            pivot_tgt = data.get("pivot_target", 0)

            if price and stop:
                success = open_position(direction, price, stop, pivot_tgt)
                if success:
                    return jsonify({
                        "status": "trade_opened",
                        "direction": direction,
                        "price": price,
                        "stop_loss": stop,
                        "conditions": conditions
                    }), 200
            else:
                log.error("Prix ou stop loss manquant dans l'alerte")

        return jsonify({
            "status": "no_trade",
            "conditions": conditions
        }), 200

    except Exception as e:
        log.error(f"Erreur webhook : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    """Dashboard de l'état du compte"""
    state.reset_daily()
    return jsonify({
        "capital"        : round(state.capital, 2),
        "total_pnl"      : round(state.total_pnl, 2),
        "daily_pnl"      : round(state.daily_pnl, 2),
        "best_day"       : round(state.best_day_pnl, 2),
        "drawdown"       : round(state.peak_equity - state.capital, 2),
        "trades_today"   : state.trades_today,
        "position_open"  : state.position_open,
        "position_side"  : state.position_side,
        "entry_price"    : state.entry_price,
        "stop_loss"      : state.stop_loss,
        "take_profit1"   : state.take_profit1,
        "take_profit2"   : state.take_profit2,
        "objectif"       : f"{round(state.total_pnl / (CAPITAL_DEMO * PROFIT_TARGET) * 100, 1)}%",
        "can_trade"      : state.can_trade()[0]
    }), 200


@app.route("/close", methods=["POST"])
def close():
    """Fermer manuellement la position"""
    if state.position_open:
        success = close_position()
        return jsonify({"status": "closed" if success else "error"}), 200
    return jsonify({"status": "no_position"}), 200


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Agent trading actif", "mode": "DEMO"}), 200


# ══════════════════════════════════════════════
#  DÉMARRAGE
# ══════════════════════════════════════════════
if __name__ == "__main__":
    log.info("=" * 50)
    log.info("Agent de Trading Démarré — MODE DÉMO")
    log.info(f"Capital : {CAPITAL_DEMO}€")
    log.info(f"Objectif : {CAPITAL_DEMO * PROFIT_TARGET}€ ({PROFIT_TARGET*100}%)")
    log.info(f"Stop journalier : {CAPITAL_DEMO * DAILY_LOSS_LIMIT}€")
    log.info(f"Drawdown max : {CAPITAL_DEMO * MAX_DRAWDOWN_PCT}€")
    log.info("=" * 50)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
