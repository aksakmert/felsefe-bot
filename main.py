import os
import json
import asyncio
import base64
import requests as req
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

app = Flask(__name__)

TOKEN      = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID    = os.environ.get("CHAT_ID")
RENDER_URL = os.environ.get("RENDER_URL", "https://btc-sinyal-bot-dzvh.onrender.com")

trades         = []
pending_trades = {}
trade_counter  = [0]

def save_trades():
    try:
        with open("trades.json", "w") as f:
            json.dump(trades, f)
    except:
        pass

def load_trades():
    global trades
    try:
        with open("trades.json", "r") as f:
            trades = json.load(f)
            if trades:
                trade_counter[0] = max(t["id"] for t in trades)
    except:
        trades = []

def fetch_news_data():
    """Haber verisini retry ile çek"""
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    for i in range(3):
        try:
            r = req.get(url, timeout=10)
            if r.status_code == 200:
                return r.json()
        except:
            pass
        time.sleep(2)
    return None

def check_news(symbol):
    try:
        currencies = []
        pairs = ["EUR","USD","GBP","JPY","CHF","AUD","NZD","CAD"]
        for p in pairs:
            if p in symbol.upper():
                currencies.append(p)
        now    = datetime.utcnow()
        events = fetch_news_data()
        if not events:
            return []
        upcoming = []
        for event in events:
            if event.get("impact") not in ["High", "Medium"]:
                continue
            currency = event.get("country", "")
            if not any(c == currency for c in currencies):
                continue
            try:
                event_time = datetime.strptime(event["date"], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
            except:
                continue
            diff = abs((event_time - now).total_seconds()) / 3600
            if diff <= 4:
                impact = event.get("impact")
                if impact == "High":
                    icon = "🔴"
                else:
                    icon = "🟠"
                upcoming.append(f"{icon} {event.get('title','?')} ({currency}) - {event_time.strftime('%H:%M')} UTC")
        return upcoming
    except:
        return []
        upcoming = []
        for event in events:
            if event.get("impact") not in ["High", "Medium"]:
                continue
            currency = event.get("country", "")
            if not any(c == currency for c in currencies):
                continue
            try:
                event_time = datetime.strptime(event["date"], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
            except:
                continue
            diff = abs((event_time - now).total_seconds()) / 3600
            if diff <= 4:
                impact = event.get("impact")
                if impact == "High":
                    icon = "🔴"
                else:
                    icon = "🟡"
                upcoming.append(f"{icon} {event.get('title','?')} ({currency}) - {event_time.strftime('%H:%M')} UTC")
        return upcoming
    except:
        return []

# Haber çevirisi için basit sözlük
NEWS_TRANSLATIONS = {
    "speaks": "Konuşuyor",
    "speech": "Konuşması",
    "press conference": "Basın Toplantısı",
    "rate decision": "Faiz Kararı",
    "interest rate": "Faiz Oranı",
    "inflation": "Enflasyon",
    "cpi": "TÜFE",
    "gdp": "GSYİH",
    "unemployment": "İşsizlik",
    "nonfarm payrolls": "Tarım Dışı İstihdam",
    "retail sales": "Perakende Satışlar",
    "trade balance": "Ticaret Dengesi",
    "manufacturing": "İmalat",
    "services": "Hizmetler",
    "pmi": "PMI",
    "fomc": "FOMC",
    "meeting": "Toplantısı",
    "minutes": "Tutanakları",
    "statement": "Açıklaması",
    "member": "Üyesi",
    "governor": "Başkanı",
    "president": "Başkanı",
    "chair": "Başkanı",
}

def translate_news(title):
    result = title
    for en, tr in NEWS_TRANSLATIONS.items():
        result = result.replace(en.title(), tr).replace(en, tr)
    return result

def format_news_full(impact, event_time_str, country, title):
    tr_title = translate_news(title)
    if impact == "High":
        icon = "🔴"
    elif impact == "Medium":
        icon = "🟠"
    else:
        icon = "🟡"
    return f"{icon} {event_time_str} | {country} | {tr_title}"

# Sembol izin listesi
SEMBOL_IZIN = {
    "BTCUSD":  ["long", "short"],
    "ETHUSD":  ["long", "short"],
    "EURUSD":  ["long", "short"],
    "XAUUSD":  ["long"],
    "XAGUSD":  ["long"],
    "USDCHF":  ["short"],
}

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return jsonify({"error": "no data"}), 400
    trade_counter[0] += 1
    trade_id = trade_counter[0]
    entry  = float(data.get("entry", 0))
    sl     = float(data.get("sl", 0))
    tp     = float(data.get("tp", 0))
    symbol = data.get("symbol", "EURUSD")
    yon    = data.get("yon", "short").lower()

    # Sembol izin kontrolü
    izinler = SEMBOL_IZIN.get(symbol.upper(), ["short"])
    if yon not in izinler:
        print(f"⛔ {symbol} için {yon} sinyali reddedildi!")
        return jsonify({"ok": False, "reason": f"{symbol} için {yon} izni yok"}), 403

    risk   = round(abs(entry - sl), 4)
    reward = round(abs(tp - entry), 4)
    rr     = round(reward / risk, 1) if risk > 0 else 0
    trade = {
        "id":     trade_id,
        "symbol": symbol,
        "yon":    yon,
        "entry":  entry,
        "sl":     sl,
        "tp":     tp,
        "risk":   risk,
        "rr":     rr,
        "status": "pending",
        "time":   datetime.now().isoformat()
    }
    pending_trades[trade_id] = trade
    news = check_news(symbol)
    pending_trades[trade_id]["news"] = news
    return jsonify({"ok": True, "trade_id": trade_id})

async def send_signal(trade_id, symbol, entry, sl, tp, rr, news=[], yon="short"):
    bot = Bot(token=TOKEN)
    has_red    = any("🔴" in n for n in news)
    has_orange = any("🟠" in n for n in news)
    if has_red:
        news_icon = "💥"
        news_msg  = "Yakında büyük bir patlama olacak!"
    elif has_orange:
        news_icon = "💣"
        news_msg  = "Yakında sarsıcı bir patlama olacak!"
    else:
        news_icon = "🍀"
        news_msg  = "Yakınlarda büyük bir olay yok!"

    yon_icon = "📉 SHORT" if yon == "short" else "📈 LONG"

    msg = (
        f"🩸 *DÖNÜŞÜM SİNYALİ* 🩸 | #{trade_id}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚛️ Element: `{symbol}`\n"
        f"Yön: {yon_icon}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎯 Entry: `{entry}`\n"
        f"🔴 Stop Loss: `{sl}`\n"
        f"✅ Take Profit: `{tp}`\n"
        f"⚖️ RR: `{rr}R`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{news_icon} {news_msg}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏳ Dönüşüm bekleniyor... ⌛️"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 DÖNÜŞTÜR", callback_data=f"approve_{trade_id}"),
        InlineKeyboardButton("❌ REDDET", callback_data=f"reject_{trade_id}")
    ]])
    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown", reply_markup=keyboard)

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    data = request.json
    asyncio.run(process_update(data))
    return jsonify({"ok": True})

async def process_update(data):
    bot = Bot(token=TOKEN)
    update = Update.de_json(data, bot)

    if update.message:
        if str(update.message.chat_id) != str(CHAT_ID):
            await bot.send_message(chat_id=update.message.chat_id, text="⛔ Yetkisiz erişim.")
            return
        text = update.message.text.strip().lower() if update.message.text else ""
        if text in ["/gunluk", "gunluk", "/haftalik", "haftalik", "/aylik", "aylik",
                    "/bakiye", "bakiye", "/pozisyon", "pozisyon",
                    "/kapat", "kapat", "/durdur", "durdur", "/baslat", "baslat",
                    "/haber", "haber", "/news", "news"]:
            cmd = text.replace("/", "").lower()
            if cmd not in pending_commands:
                pending_commands.append(cmd)
            # 1) Kullanıcının yazdığı mesajı sil
            try:
                await bot.delete_message(chat_id=CHAT_ID, message_id=update.message.message_id)
            except:
                pass
            # 2) "işleniyor" mesajını gönder ve hemen sil
            processing_msg = await bot.send_message(chat_id=CHAT_ID, text=f"⚙️ `{cmd}` işleniyor...")
            await asyncio.sleep(1)
            try:
                await bot.delete_message(chat_id=CHAT_ID, message_id=processing_msg.message_id)
            except:
                pass
        return

    if update.callback_query:
        if str(update.callback_query.from_user.id) != str(CHAT_ID):
            return

    query    = update.callback_query
    await query.answer()
    parts    = query.data.split("_")
    action   = parts[0]
    trade_id = int(parts[1])
    trade    = pending_trades.get(trade_id)

    if not trade:
        await query.edit_message_caption("❌ İşlem bulunamadı.")
        return

    if action == "approve":
        trade["status"] = "active"
        trades.append(trade)
        save_trades()
        del pending_trades[trade_id]
        msg = (
            f"🔄 *DÖNÜŞÜM BAŞLADI!* | #{trade_id}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⚛️ Element: `{trade['symbol']}`\n"
            f"📉 Yön: SHORT\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 Entry: `{trade['entry']}`\n"
            f"🔴 SL: `{trade['sl']}`\n"
            f"✅ TP: `{trade['tp']}`\n"
            f"⚖️ RR: `{trade['rr']}R`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⚡️ Dönüşüm aktif!"
        )
        try:
            sent = await query.edit_message_caption(msg, parse_mode="Markdown")
        except:
            sent = await query.edit_message_text(msg, parse_mode="Markdown")
        # Mesaj ID'sini sakla
        trade["message_id"] = query.message.message_id
        save_trades()

    elif action == "reject":
        del pending_trades[trade_id]
        msg = f"❌ *#{trade_id} Dönüşüm Reddedildi*"
        try:
            await query.edit_message_caption(msg, parse_mode="Markdown")
        except:
            await query.edit_message_text(msg, parse_mode="Markdown")

@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    async def _set():
        bot = Bot(token=TOKEN)
        url = f"{RENDER_URL}/telegram"
        await bot.set_webhook(url)
        return url
    result = asyncio.run(_set())
    return jsonify({"ok": True, "webhook": result})

@app.route("/notify_photo", methods=["POST"])
def notify_photo():
    data       = request.json
    img_data   = data.get("photo", "")
    caption    = data.get("caption", "")
    trade_id   = data.get("trade_id")
    if img_data:
        img_bytes = base64.b64decode(img_data)
        if trade_id:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 DÖNÜŞTÜR", callback_data=f"approve_{trade_id}"),
                InlineKeyboardButton("❌ REDDET", callback_data=f"reject_{trade_id}")
            ]])
            asyncio.run(Bot(token=TOKEN).send_photo(
                chat_id=CHAT_ID, photo=img_bytes, caption=caption,
                parse_mode="Markdown", reply_markup=keyboard
            ))
        else:
            asyncio.run(Bot(token=TOKEN).send_photo(
                chat_id=CHAT_ID, photo=img_bytes, caption=caption, parse_mode="Markdown"
            ))
    return jsonify({"ok": True})

@app.route("/notify", methods=["POST"])
def notify():
    data         = request.json
    msg          = data.get("message", "")
    delete_after = data.get("delete_after", 0)
    if msg:
        async def send_and_delete():
            bot  = Bot(token=TOKEN)
            sent = await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            if delete_after > 0:
                await asyncio.sleep(delete_after)
                try:
                    await bot.delete_message(chat_id=CHAT_ID, message_id=sent.message_id)
                except:
                    pass
        import threading
        def run():
            asyncio.run(send_and_delete())
        threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/clear", methods=["GET"])
def clear():
    global trades, pending_trades
    trades = []
    pending_trades = {}
    trade_counter[0] = 0
    save_trades()
    return jsonify({"ok": True, "message": "Tüm işlemler temizlendi!"})

@app.route("/news/<symbol>", methods=["GET"])
def get_news(symbol):
    trade = next((t for t in pending_trades.values() if t["symbol"] == symbol), None)
    if trade:
        return jsonify({"news": trade.get("news", [])})
    return jsonify({"news": check_news(symbol)})

@app.route("/notify_signal", methods=["POST"])
def notify_signal():
    data = request.json
    trade_id = data.get("trade_id")
    trade = pending_trades.get(trade_id)
    if not trade:
        return jsonify({"error": "not found"}), 404
    news = trade.get("news", [])
    asyncio.run(send_signal(trade_id, trade["symbol"], trade["entry"], trade["sl"], trade["tp"], trade["rr"], news))
    return jsonify({"ok": True})

pending_commands = []

@app.route("/commands", methods=["GET"])
def get_commands():
    return jsonify({"commands": pending_commands})

@app.route("/clear_commands", methods=["POST"])
def clear_commands():
    pending_commands.clear()
    return jsonify({"ok": True})

@app.route("/pending", methods=["GET"])
def pending():
    return jsonify({"trades": list(pending_trades.values())})

@app.route("/approved", methods=["GET"])
def approved():
    active = [t for t in trades if t.get("status") == "active"]
    return jsonify({"trades": active})

@app.route("/executed", methods=["POST"])
def executed():
    data = request.json
    trade_id = data.get("trade_id")
    trade = next((t for t in trades if t["id"] == trade_id), None)
    if trade:
        trade["status"] = "executed"
        save_trades()
    return jsonify({"ok": True})

@app.route("/result", methods=["POST"])
def result():
    data        = request.json
    trade_id    = data.get("trade_id")
    result_type = data.get("result")
    real_profit = data.get("profit", None)  # MT5'ten gelen gerçek kar/zarar
    trade = next((t for t in trades if t["id"] == trade_id), None)
    if not trade:
        return jsonify({"error": "trade not found"}), 404

    yon      = trade.get("yon", "short")
    yon_icon = "📉 SHORT" if yon == "short" else "📈 LONG"

    if result_type == "tp":
        profit = real_profit if real_profit is not None else 0
        trade["status"] = "tp"
        trade["result"] = profit
        msg = (
            f"🏆 *DÖNÜŞÜM TAMAMLANDI!* | #{trade_id}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⚛️ Element: `{trade['symbol']}`\n"
            f"Yön: {yon_icon}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 Entry: `{trade['entry']}`\n"
            f"🔴 SL: `{trade['sl']}`\n"
            f"✅ TP: `{trade['tp']}`\n"
            f"⚖️ RR: `{trade['rr']}R`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Kâr: `+{round(profit, 2)}$`\n"
            f"🚀 Harika dönüşüm!"
        )
    else:
        profit = real_profit if real_profit is not None else 0
        trade["status"] = "sl"
        trade["result"] = profit
        msg = (
            f"🛑 *DÖNÜŞÜM DURDU!* | #{trade_id}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⚛️ Element: `{trade['symbol']}`\n"
            f"Yön: {yon_icon}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 Entry: `{trade['entry']}`\n"
            f"🔴 SL: `{trade['sl']}`\n"
            f"✅ TP: `{trade['tp']}`\n"
            f"⚖️ RR: `{trade['rr']}R`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💥 Zarar: `{round(profit, 2)}$`\n"
            f"📊 Disiplin korunuyor."
        )

    save_trades()

    message_id = trade.get("message_id")
    async def edit():
        bot = Bot(token=TOKEN)
        if message_id:
            try:
                await bot.edit_message_caption(
                    chat_id=CHAT_ID, message_id=message_id,
                    caption=msg, parse_mode="Markdown"
                )
            except:
                try:
                    await bot.edit_message_text(
                        chat_id=CHAT_ID, message_id=message_id,
                        text=msg, parse_mode="Markdown"
                    )
                except:
                    await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
    asyncio.run(edit())
    return jsonify({"ok": True})
def kz_emoji(value):
    if value > 0:
        return "💚"
    elif value < 0:
        return "🔴"
    else:
        return "⚪"

def get_report_daily(trade_list):
    total = len(trade_list)
    if total == 0:
        return "📊 *GÜN SONU RAPORU*\n\nBugün işlem yok."
    wins    = [t for t in trade_list if t.get("status") == "tp"]
    losses  = [t for t in trade_list if t.get("status") == "sl"]
    closed  = len(wins) + len(losses)
    net_d   = round(len(wins) * 100 - len(losses) * 50, 2)
    net_rr  = round(len(wins) * 2 - len(losses), 1)
    winrate = round(len(wins) / closed * 100, 1) if closed > 0 else 0
    e = kz_emoji(net_d)
    return (
        f"📊 *GÜN SONU RAPORU*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 Toplam: {total} | ✅ {len(wins)} | ❌ {len(losses)} | ⏳ {total-closed}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{e} Net: `{'+' if net_d>=0 else ''}{net_d}$` / `{'+' if net_rr>=0 else ''}{net_rr}R`\n"
        f"📈 Winrate: `%{winrate}`\n"
        f"🚀 Disiplin > Duygu"
    )

def get_report_weekly(trade_list):
    if not trade_list:
        return "📊 *HAFTALIK RAPOR*\n\nBu hafta işlem yok."
    now        = datetime.now()
    week_start = now - timedelta(days=now.weekday())
    msg        = "📊 *HAFTALIK RAPOR*\n━━━━━━━━━━━━━━━\n"
    total_wins = 0
    total_loss = 0
    total_net  = 0.0

    for day_offset in range(7):
        day    = (week_start + timedelta(days=day_offset)).date()
        if day > now.date():
            break
        day_trades = [t for t in trade_list if datetime.fromisoformat(t["time"]).date() == day]
        if not day_trades:
            continue
        wins   = [t for t in day_trades if t.get("status") == "tp"]
        losses = [t for t in day_trades if t.get("status") == "sl"]
        closed = len(wins) + len(losses)
        net    = round(len(wins) * 100 - len(losses) * 50, 2)
        wr     = round(len(wins) / closed * 100, 1) if closed > 0 else 0
        e      = kz_emoji(net)
        total_wins += len(wins)
        total_loss += len(losses)
        total_net  += net
        gun_adi = ["Pzt","Sal","Çar","Per","Cum","Cmt","Paz"][day.weekday()]
        msg += f"{e} *{gun_adi} {day.strftime('%d.%m')}* → `{'+' if net>=0 else ''}{net}$` | WR: `%{wr}`\n"

    total_closed = total_wins + total_loss
    total_wr     = round(total_wins / total_closed * 100, 1) if total_closed > 0 else 0
    total_rr     = round(total_wins * 2 - total_loss, 1)
    e            = kz_emoji(total_net)
    msg += (
        f"━━━━━━━━━━━━━━━\n"
        f"*HAFTALIK ÖZET*\n"
        f"✅ {total_wins} Kazanan | ❌ {total_loss} Kaybeden\n"
        f"{e} Net: `{'+' if total_net>=0 else ''}{round(total_net,2)}$` / `{'+' if total_rr>=0 else ''}{total_rr}R`\n"
        f"📈 Winrate: `%{total_wr}`\n"
        f"🚀 Disiplin > Duygu"
    )
    return msg

def get_report_monthly(trade_list):
    if not trade_list:
        return "📊 *AYLIK RAPOR*\n\nBu ay işlem yok."
    now   = datetime.now()
    msg   = f"📊 *AYLIK RAPOR - {now.strftime('%B %Y')}*\n━━━━━━━━━━━━━━━\n"
    total_wins = 0
    total_loss = 0
    total_net  = 0.0

    # Haftaları bul
    first_day = now.replace(day=1)
    week_num  = 1
    current   = first_day
    while current.month == now.month:
        week_end = current + timedelta(days=6)
        if week_end.month != now.month:
            week_end = now.replace(day=1) + timedelta(days=32)
            week_end = week_end.replace(day=1) - timedelta(days=1)
        week_trades = [t for t in trade_list if
                       current.date() <= datetime.fromisoformat(t["time"]).date() <= week_end.date()]
        if week_trades:
            wins   = [t for t in week_trades if t.get("status") == "tp"]
            losses = [t for t in week_trades if t.get("status") == "sl"]
            closed = len(wins) + len(losses)
            net    = round(len(wins) * 100 - len(losses) * 50, 2)
            wr     = round(len(wins) / closed * 100, 1) if closed > 0 else 0
            e      = kz_emoji(net)
            total_wins += len(wins)
            total_loss += len(losses)
            total_net  += net
            msg += f"{e} *{week_num}. Hafta* → `{'+' if net>=0 else ''}{net}$` | WR: `%{wr}`\n"
        week_num += 1
        current = current + timedelta(days=7)
        if current.month != now.month:
            break

    total_closed = total_wins + total_loss
    total_wr     = round(total_wins / total_closed * 100, 1) if total_closed > 0 else 0
    total_rr     = round(total_wins * 2 - total_loss, 1)
    e            = kz_emoji(total_net)
    msg += (
        f"━━━━━━━━━━━━━━━\n"
        f"*AYLIK ÖZET*\n"
        f"✅ {total_wins} Kazanan | ❌ {total_loss} Kaybeden\n"
        f"{e} Net: `{'+' if total_net>=0 else ''}{round(total_net,2)}$` / `{'+' if total_rr>=0 else ''}{total_rr}R`\n"
        f"📈 Winrate: `%{total_wr}`\n"
        f"🚀 Disiplin > Duygu"
    )
    return msg

@app.route("/report/<period>", methods=["GET"])
def report(period):
    now = datetime.now()
    if period == "daily":
        filtered = [t for t in trades if datetime.fromisoformat(t["time"]).date() == now.date()]
        msg = get_report_daily(filtered)
    elif period == "weekly":
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        filtered = [t for t in trades if datetime.fromisoformat(t["time"]) >= week_start]
        msg = get_report_weekly(filtered)
    elif period == "monthly":
        filtered = [t for t in trades if
                    datetime.fromisoformat(t["time"]).month == now.month and
                    datetime.fromisoformat(t["time"]).year == now.year]
        msg = get_report_monthly(filtered)
    else:
        return jsonify({"error": "invalid period"}), 400
    async def send_and_delete():
        bot  = Bot(token=TOKEN)
        sent = await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        await asyncio.sleep(60)
        try:
            await bot.delete_message(chat_id=CHAT_ID, message_id=sent.message_id)
        except:
            pass
    import threading
    def run():
        asyncio.run(send_and_delete())
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/haber_all", methods=["GET"])
def haber_all():
    try:
        from datetime import timezone
        from dateutil import parser as dateparser
        events = fetch_news_data()
        if not events:
            return jsonify({"error": "veri alınamadı"})
        today  = datetime.now(timezone.utc).date()
        msg    = f"📅 *{today.strftime('%d.%m.%Y')} TÜM HABERLER*\n━━━━━━━━━━━━━━━\n"
        count  = 0
        for event in events:
            try:
                event_time = dateparser.parse(event["date"]).astimezone(timezone.utc)
            except:
                continue
            if event_time.date() != today:
                continue
            impact = event.get("impact", "")
            line   = format_news_full(impact, event_time.strftime('%H:%M'), event.get('country','?'), event.get('title','?'))
            msg   += line + "\n"
            count += 1
        if count == 0:
            msg += "Bugün haber yok."
        async def send_and_delete():
            bot  = Bot(token=TOKEN)
            sent = await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            await asyncio.sleep(60)
            try:
                await bot.delete_message(chat_id=CHAT_ID, message_id=sent.message_id)
            except:
                pass
        import threading
        threading.Thread(target=lambda: asyncio.run(send_and_delete()), daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/haber_important", methods=["GET"])
def haber_important():
    try:
        from datetime import timezone
        from dateutil import parser as dateparser
        events = fetch_news_data()
        if not events:
            return jsonify({"error": "veri alınamadı"})
        today  = datetime.now(timezone.utc).date()
        msg    = f"⚠️ *{today.strftime('%d.%m.%Y')} ÖNEMLİ HABERLER*\n━━━━━━━━━━━━━━━\n"
        count  = 0
        for event in events:
            try:
                event_time = dateparser.parse(event["date"]).astimezone(timezone.utc)
            except:
                continue
            if event_time.date() != today:
                continue
            impact = event.get("impact", "")
            if impact not in ["High", "Medium"]:
                continue
            line  = format_news_full(impact, event_time.strftime('%H:%M'), event.get('country','?'), event.get('title','?'))
            msg  += line + "\n"
            count += 1
        if count == 0:
            msg += "✅ Bugün önemli haber yok."
        async def send_and_delete2():
            bot  = Bot(token=TOKEN)
            sent = await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            await asyncio.sleep(60)
            try:
                await bot.delete_message(chat_id=CHAT_ID, message_id=sent.message_id)
            except:
                pass
        import threading
        threading.Thread(target=lambda: asyncio.run(send_and_delete2()), daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)})
mt5_bakiye_cache  = {}
mt5_pozisyon_cache = {"positions": []}

@app.route("/mt5_bakiye", methods=["GET", "POST"])
def mt5_bakiye():
    global mt5_bakiye_cache
    if request.method == "POST":
        mt5_bakiye_cache = request.json
        return jsonify({"ok": True})
    return jsonify(mt5_bakiye_cache)

@app.route("/mt5_pozisyon", methods=["GET", "POST"])
def mt5_pozisyon():
    global mt5_pozisyon_cache
    if request.method == "POST":
        mt5_pozisyon_cache = request.json
        return jsonify({"ok": True})
    return jsonify(mt5_pozisyon_cache)

@app.route("/trades_all", methods=["GET"])
def trades_all():
    return jsonify({"trades": trades})

@app.route("/bakiye_data", methods=["GET"])
def bakiye_data():
    try:
        r = requests.get(f"{RENDER_URL}/mt5_bakiye", timeout=5)
        if r.status_code == 200:
            return jsonify(r.json())
    except:
        pass
    return jsonify({"balance": 0, "equity": 0, "acik_kar": 0, "margin": 0,
                    "daily_net": 0, "daily_pct": 0, "total_net": 0, "total_pct": 0})

@app.route("/pozisyon_data", methods=["GET"])
def pozisyon_data():
    try:
        r = requests.get(f"{RENDER_URL}/mt5_pozisyon", timeout=5)
        if r.status_code == 200:
            return jsonify(r.json())
    except:
        pass
    return jsonify({"positions": []})

@app.route("/", methods=["GET"])
def index():
    return "Bot çalışıyor! 🚀"

if __name__ == "__main__":
    load_trades()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
