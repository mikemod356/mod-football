import os
import re
import logging
import urllib.request
import xml.etree.ElementTree as ET
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
BBC_RSS = "https://feeds.bbci.co.uk/sport/football/rss.xml"
READ_MORE_URL = "https://www.bbc.com/sport/football"

# ---- Top 5 leagues + teams ----
LEAGUES = {
    "premier_league": {
        "name": "🏴 Premier League",
        "teams": ["Arsenal", "Chelsea", "Liverpool", "Manchester United",
                  "Manchester City", "Tottenham"]
    },
    "la_liga": {
        "name": "🇪🇸 La Liga",
        "teams": ["Real Madrid", "Barcelona", "Atletico Madrid", "Sevilla"]
    },
    "serie_a": {
        "name": "🇮🇹 Serie A",
        "teams": ["Juventus", "AC Milan", "Inter Milan", "Napoli"]
    },
    "bundesliga": {
        "name": "🇩🇪 Bundesliga",
        "teams": ["Bayern Munich", "Borussia Dortmund", "RB Leipzig"]
    },
    "ligue_1": {
        "name": "🇫🇷 Ligue 1",
        "teams": ["PSG", "Marseille", "Lyon", "Monaco"]
    },
}

# In-memory storage: user_id -> {"league":..., "team":...}
user_prefs = {}

# =========================================================
# HELPERS
# =========================================================

def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.strip()

def truncate(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."

def fetch_news(team_filter: str = None, limit: int = 15):
    try:
        req = urllib.request.Request(
            BBC_RSS,
            headers={"User-Agent": "Mozilla/5.0 (FootballBot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
    except Exception as e:
        logging.warning(f"RSS fetch failed: {e}")
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        logging.warning(f"RSS parse failed: {e}")
        return []

    results = []
    for item in root.iter("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        link_el = item.find("link")

        title = clean_html(title_el.text if title_el is not None else "")
        summary = clean_html(desc_el.text if desc_el is not None else "")
        link = link_el.text if link_el is not None else READ_MORE_URL

        if not title:
            continue

        if team_filter:
            blob = (title + " " + summary).lower()
            if team_filter.lower() not in blob:
                continue

        results.append({"title": title, "summary": summary, "link": link})
        if len(results) >= limit:
            break

    return results

def build_read_more_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 READ MORE", url=READ_MORE_URL)]
    ])

# =========================================================
# /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "⚽ *Mod Football Bot is here!*\n\n"
        "Do you want to know about the top football tournaments and the best "
        "teams in the world? All the best from the top European leagues — "
        "news, stats, transfers, squads, match schedules & results, live "
        "standings and a fan menu.\n\n"
        "👉 *Choose the league you want to follow:*"
    )
    buttons = [
        [InlineKeyboardButton(info["name"], callback_data=f"league|{key}")]
        for key, info in LEAGUES.items()
    ]
    await update.message.reply_text(
        welcome,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

# =========================================================
# CALLBACK HANDLER
# =========================================================

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # ---- League selected ----
    if data.startswith("league|"):
        league_key = data.split("|", 1)[1]
        user_prefs.setdefault(user_id, {})["league"] = league_key
        league_name = LEAGUES[league_key]["name"]

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📰 Read all football news", callback_data="news|all")],
            [InlineKeyboardButton("🎯 Pick a team", callback_data="pick_team")]
        ])
        await query.edit_message_text(
            f"You chose *{league_name}*.\n\n"
            "What would you like to do?",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return

    # ---- Read all news ----
    if data == "news|all":
        news = fetch_news(limit=15)

        if not news:
            await query.edit_message_text(
                "😕 Sorry, no news is available right now. Try again later.",
                reply_markup=build_read_more_keyboard()
            )
            return

        header = "📰 *Top 15 Football News*\n\n"
        body = ""
        for i, n in enumerate(news, 1):
            body += f"*{i}. {n['title']}*\n{truncate(n['summary'], 150)}\n\n"

        full = truncate(header + body, 3800)
        await query.edit_message_text(
            full + "\n_…tap READ MORE for the full story._",
            reply_markup=build_read_more_keyboard(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        return

    # ---- Pick a team menu ----
    if data == "pick_team":
        prefs = user_prefs.get(user_id, {})
        league_key = prefs.get("league")
        teams = LEAGUES[league_key]["teams"]

        buttons = [
            [InlineKeyboardButton(t, callback_data=f"team|{t}")]
            for t in teams
        ]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data=f"league|{league_key}")])
        await query.edit_message_text(
            "🎯 *Choose a team:*",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        return

    # ---- Team selected ----
    if data.startswith("team|"):
        team = data.split("|", 1)[1]
        prefs = user_prefs.setdefault(user_id, {})
        prefs["team"] = team

        await query.edit_message_text(
            f"✅ You are now following *{team}*.\n"
            f"Latest news will be sent to you every *10 minutes* automatically.",
            parse_mode="Markdown"
        )

        await send_team_news(context, user_id, team)
        return

# =========================================================
# SEND TEAM NEWS
# =========================================================

async def send_team_news(context, user_id, team):
    news = fetch_news(team_filter=team, limit=5)
    if not news:
        news = fetch_news(limit=3)
        prefix = f"ℹ️ No fresh *{team}* news right now. Latest football headlines:"
    else:
        prefix = f"⚽ *{team} — Latest News*"

    text = prefix + "\n\n"
    for i, n in enumerate(news, 1):
        text += f"*{i}. {n['title']}*\n{truncate(n['summary'], 200)}\n\n"

    text = truncate(text, 3800) + "\n_…tap READ MORE for the full story._"

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=build_read_more_keyboard(),
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.warning(f"Failed to send to {user_id}: {e}")

# =========================================================
# SCHEDULED JOB — every 10 minutes
# =========================================================

async def periodic_update(context: ContextTypes.DEFAULT_TYPE):
    for user_id, prefs in list(user_prefs.items()):
        team = prefs.get("team")
        if team:
            await send_team_news(context, user_id, team)

# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN environment variable is missing!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        periodic_update,
        "interval",
        minutes=10,
        args=[app],
        max_instances=1,
    )
    scheduler.start()

    logging.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
