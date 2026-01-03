# 🚀 Setup Instrukcijos

## 1. Įdiegimas

```bash
# Navigate į projekto aplanką
cd C:\Users\dneri\Downloads\futures-signals-bot

# Sukurti virtual environment (rekomenduojama)
python -m venv venv

# Aktivinti virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Įdiegti priklausomybes
pip install -r requirements.txt
```

## 2. Konfigūracija

Sukurkite `.env` failą projekto root aplanke:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
KRAKEN_FUTURES_API_KEY=your_kraken_key
KRAKEN_FUTURES_SECRET=your_kraken_secret
FINNHUB_API_KEY=your_finnhub_key
```

### Kaip gauti API keys:

**Telegram:**
1. Eikite į [@BotFather](https://t.me/botfather)
2. Sukurkite naują botą: `/newbot`
3. Nukopijuokite token
4. Gauti Chat ID: siųskite žinutę botui, tada eikite į `https://api.telegram.org/bot<TOKEN>/getUpdates`

**Kraken Futures:**
1. Prisijunkite į [Kraken Futures](https://futures.kraken.com)
2. Eikite į API Settings
3. Sukurkite API key su trading permissions

**Finnhub:**
1. Užsiregistruokite [Finnhub](https://finnhub.io)
2. Gauti free API key iš dashboard

## 3. Patikrinimas

### Patikrinkite ar visi moduliai importuojami:

```bash
python -c "import futures_signals; print('OK')"
```

Jei gaunate klaidas:
- Patikrinkite ar įdiegėte visas priklausomybes: `pip install -r requirements.txt`
- Patikrinkite ar custom moduliai yra projekto root aplanke

## 4. Paleidimas

```bash
python futures_signals.py
```

Botas paleis:
- Signalų monitoring loop
- Flask web server (http://localhost:5000)
- Telegram notifications (jei sukonfigūruota)

## 5. Troubleshooting

### Missing Module Errors

Jei gaunate `ModuleNotFoundError`:
```bash
pip install <module_name>
```

### Custom Modules

Jei turite pilnus custom modulių failus (quant_analytics, ml_signals, etc.), nukopijuokite juos į projekto root, perrašydami placeholder failus.

### Import Errors

Jei kai kurios funkcijos trūksta:
- Placeholder failai yra sukurti su basic funkcionalumu
- Botas turėtų paleisti, bet kai kurios funkcijos neveiks iki pilnos implementacijos

### API Connection Issues

1. Patikrinkite `.env` failą
2. Patikrinkite ar API keys yra teisingi
3. Patikrinkite firewall/network settings

## 6. Web Dashboard

Paleidę botą, atidarykite naršyklėje:
```
http://localhost:5000
```

Dashboard rodo:
- Signalų statistiką
- Atviras pozicijas
- Bot statusą
- Analytics

## 7. Kitas žingsniais

- [ ] Implementuoti pilną `quant_analytics.py` modulį
- [ ] Implementuoti pilną `ml_signals.py` modulį
- [ ] Implementuoti pilną `sentiment_analyzer.py` modulį
- [ ] Implementuoti pilną `onchain_analytics.py` modulį
- [ ] Implementuoti pilną `pro_strategies.py` modulį
- [ ] Sukonfigūruoti auto-trading (jei norite)

## ⚠️ Svarbu

- Auto-trading yra **RIZIKINGAS** - naudokite su atsargumu
- Pradėkite su mažais balansais
- Patikrinkite visas konfigūracijas prieš paleisdami su realiais pinigais
- Testuokite pirmiausia su paper trading arba demo account

