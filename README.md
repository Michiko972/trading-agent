# Agent de Trading Automatique
## Stratégie : MACD + DMI + Pivot Points + EMA 200
## Broker : Capital.com (démo)

---

## DÉPLOIEMENT SUR RAILWAY

### Étape 1 — Préparer les fichiers
Télécharge les 4 fichiers :
- agent.py
- requirements.txt
- railway.toml
- strategy_tradingview.pine (pour TradingView uniquement)

### Étape 2 — Créer le projet Railway
1. Va sur railway.app
2. Clique "New Project"
3. Choisis "Deploy from GitHub" ou "Empty Project"
4. Upload les fichiers agent.py, requirements.txt, railway.toml

### Étape 3 — Variables d'environnement (TES CLÉS API)
Dans Railway → Settings → Variables, ajoute :
- CAPITAL_API_KEY = ta_clé_api_capital
- CAPITAL_API_PASSWORD = ton_mot_de_passe_api

⚠️ Ne partage JAMAIS ces valeurs

### Étape 4 — Déployer
Clique "Deploy" — Railway installe tout automatiquement.
Tu récupères une URL du type : https://ton-agent.railway.app

### Étape 5 — TradingView
1. Ouvre strategy_tradingview.pine dans l'éditeur Pine
2. Remplace "VOTRE_URL_RAILWAY/webhook" par ton URL Railway
3. Ajoute le script sur BTC/USD M1 ou M2
4. Crée une alerte → "Any alert() function call"
5. Webhook URL = https://ton-agent.railway.app/webhook

---

## VÉRIFICATION

Teste que l'agent tourne :
- Va sur https://ton-agent.railway.app/status
- Tu vois l'état du compte en temps réel

---

## ENDPOINTS

| URL | Méthode | Description |
|-----|---------|-------------|
| /webhook | POST | Reçoit les alertes TradingView |
| /status | GET | État du compte et position |
| /close | POST | Fermer la position manuellement |

---

## RÈGLES DE GESTION (proportionnelles TopStep 50K)

| Règle | Valeur |
|-------|--------|
| Capital démo | 1.000€ |
| Risque par trade | 1% = 10€ |
| Stop journalier | 2% = 20€ |
| Drawdown max | 4% = 40€ |
| Objectif total | 6% = 60€ |
| Consistance | Meilleur jour < 50% du total |
| TP1 | Risk × 1.5 |
| TP2 | Risk × 3.0 |
