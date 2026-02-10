# Futures Signal Bot v8.9.24

Profesionalus crypto futures signalų botas su automatiniu prekybos valdymu.

## 🚀 Funkcijos

- **Automatinis signalų generavimas** - 8 crypto assetai (BTC, ETH, SOL, XRP, LTC, ADA, DOT, LINK)
- **Automatinių pozicijų valdymas** - Kraken Futures integracija
- **Trailing Stop Loss** - Automatinis stop loss atnaujinimas
- **Dynamic Leverage** - Automatinis leverage pasirinkimas pagal signalo stiprumą
- **Risk Management** - Dienos/savaitės nuostolių limitai
- **Telegram pranešimai** - Signalai ir pozicijų atnaujinimai
- **Web Dashboard** - Flask-based dashboard
- **Quantitative Analysis** - Matematinė analizė (Monte Carlo, ARIMA, Mean Reversion)
- **ML Predictions** - Machine learning signalų patvirtinimas

## 📋 Reikalavimai

- Python 3.9+
- Kraken Futures API keys (jei naudojate auto-trading)
- Telegram Bot Token (pranešimams)
- Finnhub API Key (S&P 500, VIX, DXY duomenims)

## 🛠️ Diegimas

1. **Klonuokite arba nukopijuokite projekto failus**

2. **Įdiekite priklausomybes:**
```bash
pip install -r requirements.txt
```

3. **Sukonfigūruokite aplinkos kintamuosius:**
```bash
# Nukopijuokite .env.example į .env ir užpildykite:
cp .env.example .env
```

Arba sukurkite `.env` failą su:
```
TELEGRAM_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
KRAKEN_FUTURES_API_KEY=your_key_here
KRAKEN_FUTURES_SECRET=your_secret_here
FINNHUB_API_KEY=your_key_here
```

4. **Paleiskite botą:**
```bash
python futures_signals.py
```

## ⚙️ Konfigūracija

Pagrindiniai nustatymai yra faile `futures_signals.py`:

- `AUTO_TRADING_ENABLED` - Įjungti/išjungti auto-trading
- `AUTO_TRADE_MARGIN_USD` - Pradinė marža USD (default: $25)
- `AUTO_TRADE_MAX_POSITIONS` - Maksimalus pozicijų skaičius (default: 3)
- `DAILY_LOSS_LIMIT_PCT` - Dienos nuostolių limitas % (default: 2%)
- `WEEKLY_LOSS_LIMIT_PCT` - Savaitės nuostolių limitas % (default: 5%)
- `TRAILING_ENABLED` - Trailing stop loss (default: True)
- `MIN_SCORE` - Minimalus signalo score (default: 60)

## 📊 Struktūra

```
futures-signals-bot/
├── futures_signals.py          # Pagrindinis botas
├── requirements.txt            # Python priklausomybės
├── .env.example               # Aplinkos kintamųjų pavyzdys
├── README.md                  # Šis failas
├── quant_analytics.py         # Matematinė analizė (placeholder)
├── ml_signals.py              # ML modelis (placeholder)
├── sentiment_analyzer.py      # Sentimento analizė (placeholder)
├── onchain_analytics.py       # On-chain analizė (placeholder)
├── trade_exit_engine.py       # Exit leveliai (basic)
├── signal_density_engine.py   # Signal density (basic)
├── async_safety_engine.py     # Safety utilities
├── market_regime_engine.py    # Market regime (basic)
├── net_profit_engine.py       # Net profit calculator
├── entry_optimizer_5m.py      # 5m entry optimizer (basic)
├── pro_strategies.py          # Pro strategies (placeholder)
├── templates/                 # Flask templates
│   └── index.html
└── static/                    # Static files (CSS, JS)
```

## ⚠️ Svarbu

**Custom moduliai** (`quant_analytics`, `ml_signals`, `sentiment_analyzer`, `onchain_analytics`, `pro_strategies`) yra **placeholder** failai su minimalia funkcionalumu. 

Jei jūsų projekte yra pilni šių modulių failai, nukopijuokite juos į projekto root aplanką.

## 🔧 Troubleshooting

### ModuleNotFoundError

Jei gaunate `ModuleNotFoundError`:
1. Patikrinkite ar įdiegėte visas priklausomybes: `pip install -r requirements.txt`
2. Patikrinkite ar Python yra teisingame aplanke: `python --version`

### Missing Custom Modules

Jei trūksta custom modulių (pvz., `quant_analytics`), placeholder failai yra sukurti ir botas turėtų paleisti. Tačiau kai kurios funkcijos gali neveikti iki pilnų modulių implementacijos.

### API Keys

Jei botas neužsisako į Telegram arba negali prisijungti prie Kraken:
1. Patikrinkite `.env` failą
2. Patikrinkite ar API keys yra teisingi
3. Patikrinkite ar Telegram bot turi permissions siųsti žinutes

## 📝 Versijos

- **v8.9.24** - Current version
  - 5m Entry Optimizer (FUND MODE)
  - Dynamic Exit Engine v2.0
  - RR Engine v2.0 HYBRID
  - Rebound Entry Refiner v2.0

## 📞 Support

Jei turite klausimų ar problemų, patikrinkite:
1. Log failus konsolėje
2. Telegram pranešimus
3. Web dashboard: `http://localhost:5000`

## ⚖️ Disclaimer

Šis botas yra skirtas edukaciniams tikslams. Prekyba crypto futures yra rizikinga ir gali sukelti finansinius nuostolius. Naudokite atsakingai ir savo rizika.

