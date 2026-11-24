import os
import re
import json
import time
import calendar
import hashlib
import logging
import sqlite3
import asyncio
import urllib.request
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ============================================================
# MOD FOOTBALL BOT
# User chooses a league, then all popular teams or one team.
# New relevant stories are checked and sent every 10 minutes.
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "football_bot.db")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

CHECK_EVERY_MINUTES = 10
MAX_ARTICLE_WORDS = 1800
MAX_TELEGRAM_TEXT = 3800
MAX_STORIES_PER_USER_PER_CHECK = 3
MAX_FEED_AGE_HOURS = 48

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("mod-football-bot")

# These are popular-club menus, not a live league table.
# Review these lists after each season's promotions/relegations.
LEAGUES = {
    "premier": "Premier League",
    "laliga": "La Liga",
    "seriea": "Serie A",
    "bundesliga": "Bundesliga",
    "ligue1": "Ligue 1",
}

TEAMS = {
    "premier": [
        ("Arsenal", ["arsenal"]),
        ("Liverpool", ["liverpool"]),
        ("Manchester City", ["manchester city", "man city"]),
        ("Manchester United", ["manchester united", "man united", "man utd"]),
        ("Chelsea", ["chelsea"]),
        ("Tottenham Hotspur", ["tottenham", "spurs"]),
        ("Newcastle United", ["newcastle", "newcastle united"]),
        ("Aston Villa", ["aston villa"]),
        ("West Ham United", ["west ham"]),
        ("Everton", ["everton"]),
        ("Nottingham Forest", ["nottingham forest"]),
        ("Brighton", ["brighton", "brighton and hove albion"]),
        ("Crystal Palace", ["crystal palace"]),
        ("Wolverhampton Wanderers", ["wolves", "wolverhampton"]),
        ("Brentford", ["brentford"]),
    ],
    "laliga": [
        ("Real Madrid", ["real madrid"]),
        ("Barcelona", ["barcelona", "fc barcelona"]),
        ("Atletico Madrid", ["atletico madrid", "atlético madrid"]),
        ("Sevilla", ["sevilla", "sevilla fc"]),
        ("Valencia", ["valencia", "valencia cf"]),
        ("Athletic Club", ["athletic club", "athletic bilbao"]),
        ("Real Sociedad", ["real sociedad"]),
        ("Villarreal", ["villarreal"]),
        ("Real Betis", ["real betis", "betis"]),
        ("Girona", ["girona"]),
        ("Getafe", ["getafe"]),
        ("Celta Vigo", ["celta", "celta vigo"]),
        ("Osasuna", ["osasuna"]),
        ("Mallorca", ["mallorca"]),
        ("Espanyol", ["espanyol"]),
    ],
    "seriea": [
        ("Juventus", ["juventus", "juve"]),
        ("Inter Milan", ["inter milan", "inter"]),
        ("AC Milan", ["ac milan", "milan"]),
        ("Napoli", ["napoli"]),
        ("AS Roma", ["as roma", "roma"]),
        ("Lazio", ["lazio"]),
        ("Atalanta", ["atalanta"]),
        ("Fiorentina", ["fiorentina"]),
        ("Bologna", ["bologna"]),
        ("Torino", ["torino"]),
        ("Udinese", ["udinese"]),
        ("Genoa", ["genoa"]),
        ("Parma", ["parma"]),
        ("Como", ["como"]),
        ("Cagliari", ["cagliari"]),
    ],
    "bundesliga": [
        ("Bayern Munich", ["bayern munich", "bayern"]),
        ("Borussia Dortmund", ["borussia dortmund", "dortmund"]),
        ("Bayer Leverkusen", ["bayer leverkusen", "leverkusen"]),
        ("RB Leipzig", ["rb leipzig", "leipzig"]),
        ("Eintracht Frankfurt", ["eintracht frankfurt", "frankfurt"]),
        ("VfB Stuttgart", ["vfb stuttgart", "stuttgart"]),
        ("Borussia Monchengladbach", ["borussia monchengladbach", "monchengladbach"]),
        ("Wolfsburg", ["wolfsburg"]),
        ("Werder Bremen", ["werder bremen", "bremen"]),
        ("Freiburg", ["freiburg"]),
        ("Hoffenheim", ["hoffenheim"]),
        ("Mainz", ["mainz"]),
        ("Union Berlin", ["union berlin"]),
        ("Augsburg", ["augsburg"]),
        ("Hamburger SV", ["hamburger sv", "hamburg"]),
    ],
    "ligue1": [
        ("Paris Saint-Germain", ["paris saint-germain", "paris saint germain", "psg"]),
        ("Marseille", ["marseille", "olympique marseille"]),
        ("Lyon", ["olympique lyonnais", "lyon"]),
        ("Monaco", ["as monaco", "monaco"]),
        ("Lille", ["lille", "losc"]),
        ("Nice", ["ogc nice", "nice"]),
        ("Rennes", ["rennes"]),
        ("Lens", ["rc lens", "lens"]),
        ("Strasbourg", ["strasbourg"]),
        ("Nantes", ["nantes"]),
        ("Toulouse", ["toulouse"]),
        ("Montpellier", ["montpellier"]),
        ("Reims", ["reims"]),
        ("Saint-Etienne", ["saint-etienne", "saint etienne"]),
        ("Auxerre", ["auxerre"]),
    ],
}

# Public RSS feeds from established football/sports publishers.
RSS_FEEDS = [
    "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "https://www.theguardian.com/football/rss",
    "https://www.skysports.com/rss/12040",
    "https://www.espn.com/espn/rss/soccer/news",
]

WELCOME = (
    "⚽ MOD FOOTBALL BOT IS HERE!\n\n"
    "Do you want to know about the top football tournaments and the best teams in the world?\n\n"
    "Get all the best from Europe's top leagues:\n"
    "📰 Football news\n"
    "📊 Team statistics\n"
    "🔄 Transfer updates\n"
    "👥 Squads\n"
    "📅 Match schedules and results\n"
    "🏆 League standings\n"
    "⚽ Your personal fan menu\n\n"
    "Select the league you want to follow:"
)


def db_connection():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    folder = os.path.dirname(DATABASE_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY,
                league TEXT,
                mode TEXT,
                team TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_stories (
                user_id INTEGER NOT NULL,
                story_id TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (user_id, story_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stories (
                story_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT,
                summary TEXT,
                image_url TEXT,
                article TEXT,
                published_at TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def save_user(user_id, league=None, mode=None, team=None):
    with db_connection() as conn:
        conn.execute("""
            INSERT INTO subscribers (user_id, league, mode, team, active, updated_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                league=COALESCE(excluded.league, subscribers.league),
                mode=COALESCE(excluded.mode, subscribers.mode),
                team=COALESCE(excluded.team, subscribers.team),
                active=1,
                updated_at=excluded.updated_at
        """, (user_id, league, mode, team, datetime.now(timezone.utc).isoformat()))
        conn.commit()


def set_user_preferences(user_id, league, mode, team):
    with db_connection() as conn:
        conn.execute("""
            INSERT INTO subscribers (user_id, league, mode, team, active, updated_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                league=excluded.league,
                mode=excluded.mode,
                team=excluded.team,
                active=1,
                updated_at=excluded.updated_at
        """, (
            user_id, league, mode, team,
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()


def get_user(user_id):
    with db_connection() as conn:
        return conn.execute(
            "SELECT * FROM subscribers WHERE user_id=? AND active=1",
            (user_id,),
        ).fetchone()


def get_subscribers():
    with db_connection() as conn:
        return conn.execute(
            "SELECT * FROM subscribers WHERE active=1 AND league IS NOT NULL AND mode IS NOT NULL"
        ).fetchall()


def was_sent(user_id, story_id):
    with db_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_stories WHERE user_id=? AND story_id=?",
            (user_id, story_id),
        ).fetchone()
        return row is not None


def mark_sent(user_id, story_id):
    with db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_stories (user_id, story_id, sent_at) VALUES (?, ?, ?)",
            (user_id, story_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def save_story(story):
    with db_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO stories
            (story_id, title, url, source, summary, image_url, article, published_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            story["story_id"], story["title"], story["url"], story["source"],
            story["summary"], story["image_url"], None, story["published_at"],
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()


def get_saved_article(story_id):
    with db_connection() as conn:
        row = conn.execute(
            "SELECT article FROM stories WHERE story_id=?", (story_id,)
        ).fetchone()
        return row["article"] if row else None


def save_article(story_id, article):
    with db_connection() as conn:
        conn.execute(
            "UPDATE stories SET article=? WHERE story_id=?", (article, story_id)
        )
        conn.commit()


def clean_text(value):
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", str(value))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def source_name(url):
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
        return host or "Football news source"
    except Exception:
        return "Football news source"


class OGImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.image = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        data = dict(attrs)
        prop = (data.get("property") or data.get("name") or "").lower()
        if prop in ("og:image", "twitter:image") and data.get("content"):
            self.image = data["content"]


def fetch_page_image(url):
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MODFootballBot/1.0)"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            html = response.read(500_000).decode("utf-8", errors="ignore")
        parser = OGImageParser()
        parser.feed(html)
        return parser.image
    except Exception:
        return None


def entry_image(entry):
    candidates = []
    for media in entry.get("media_content", []) or []:
        if media.get("url"):
            candidates.append(media["url"])
    for media in entry.get("media_thumbnail", []) or []:
        if media.get("url"):
            candidates.append(media["url"])
    for enclosure in entry.get("enclosures", []) or []:
        if enclosure.get("href"):
            candidates.append(enclosure["href"])
        elif enclosure.get("url"):
            candidates.append(enclosure["url"])
    for candidate in candidates:
        if candidate.startswith("http"):
            return candidate
    return None


def parse_published(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def make_story_id(url, title):
    raw = (url or title).strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def fetch_rss_entries():
    all_entries = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:40]:
                title = clean_text(entry.get("title"))
                link = entry.get("link", "")
                if not title or not link.startswith("http"):
                    continue
                summary = clean_text(entry.get("summary") or entry.get("description") or "")
                published = parse_published(entry)
                if datetime.now(timezone.utc) - published > timedelta(hours=MAX_FEED_AGE_HOURS):
                    continue
                all_entries.append({
                    "story_id": make_story_id(link, title),
                    "title": title,
                    "url": link,
                    "source": source_name(link),
                    "summary": summary[:5000],
                    "image_url": entry_image(entry),
                    "published_at": published.isoformat(),
                })
        except Exception as exc:
            logger.warning("Could not read RSS feed %s: %s", feed_url, exc)

    unique = {}
    for story in all_entries:
        unique[story["story_id"]] = story
    return list(unique.values())


def normalize(value):
    return re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower()).strip()


def find_matching_clubs(league_key, text):
    haystack = f" {normalize(text)} "
    matches = []
    for team_name, aliases in TEAMS.get(league_key, []):
        for alias in aliases:
            alias_norm = normalize(alias)
            if alias_norm and f" {alias_norm} " in haystack:
                matches.append(team_name)
                break
    return matches


def story_matches_user(story, user):
    league = user["league"]
    mode = user["mode"]
    text = f'{story["title"]} {story["summary"]}'
    matched = find_matching_clubs(league, text)
    if mode == "all":
        return bool(matched)
    if mode == "team":
        team = user["team"] or ""
        return team in matched
    return False


def generate_article(story):
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY is missing.")
        return None

    prompt = f"""
You are the football news writer for MOD Football Bot.
Write a clear, engaging and accurate football news article using ONLY the information supplied below.
Do not invent quotes, scores, dates, injuries, transfer fees, contract details, statistics, line-ups or other facts.
If the source information is limited, write a shorter useful report rather than guessing.
Do not present rumours as confirmed facts. Clearly say when a report is a rumour or an unconfirmed claim.
Use simple English, a strong headline, short paragraphs and useful subheadings where appropriate.
Aim for about 350-650 words when the source contains enough information.
The article MUST NOT exceed {MAX_ARTICLE_WORDS} words.
Do not include a fake source link. End with a short "Source: {story['source']}" line.

HEADLINE: {story['title']}
SOURCE: {story['source']}
SOURCE SUMMARY: {story['summary']}
ORIGINAL ARTICLE URL: {story['url']}
"""
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "You are a careful football news editor. Never make up facts."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2200,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        article = result["choices"][0]["message"]["content"].strip()
        words = article.split()
        if len(words) > MAX_ARTICLE_WORDS:
            article = " ".join(words[:MAX_ARTICLE_WORDS])
            article += "\n\n[Article shortened to fit the bot's word limit.]"
        return article
    except Exception as exc:
        logger.exception("DeepSeek article generation failed: %s", exc)
        return None


async def get_article(story):
    existing = get_saved_article(story["story_id"])
    if existing:
        return existing
    article = await asyncio.to_thread(generate_article, story)
    if article:
        save_article(story["story_id"], article)
    return article


def split_message(text, limit=MAX_TELEGRAM_TEXT):
    parts = []
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


async def send_story(bot, user_id, story, article):
    image_url = story.get("image_url")
    if not image_url:
        image_url = await asyncio.to_thread(fetch_page_image, story["url"])

    headline = f"⚽ {story['title']}\n\n📰 Source: {story['source']}"
    read_more = InlineKeyboardMarkup([[
        InlineKeyboardButton("READ MORE ↗", url=story["url"])
    ]])

    if image_url:
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=image_url,
                caption=headline[:1000],
                reply_markup=read_more,
            )
        except Exception as exc:
            logger.info("Image could not be sent; sending text instead: %s", exc)
            await bot.send_message(chat_id=user_id, text=headline)

    parts = split_message(article)
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            await bot.send_message(
                chat_id=user_id,
                text=part,
                reply_markup=read_more,
                disable_web_page_preview=True,
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=part,
                disable_web_page_preview=True,
            )


async def check_and_send_news(bot):
    logger.info("Starting 10-minute football news check.")
    subscribers = get_subscribers()
    if not subscribers:
        logger.info("No users have completed league/news selection yet.")
        return

    stories = await asyncio.to_thread(fetch_rss_entries)
    if not stories:
        logger.info("No fresh stories found in the RSS feeds.")
        return

    # Store each source story once. The same story can be relevant to many users.
    for story in stories:
        save_story(story)

    for user in subscribers:
        sent_count = 0
        for story in stories:
            if sent_count >= MAX_STORIES_PER_USER_PER_CHECK:
                break
            if was_sent(user["user_id"], story["story_id"]):
                continue
            if not story_matches_user(story, user):
                continue

            article = await get_article(story)
            if not article:
                continue

            try:
                await bot.send_chat_action(user["user_id"], ChatAction.TYPING)
                await send_story(bot, user["user_id"], story, article)
                mark_sent(user["user_id"], story["story_id"])
                sent_count += 1
                await asyncio.sleep(1.2)
            except Exception as exc:
                error_text = str(exc).lower()
                logger.warning("Could not send news to %s: %s", user["user_id"], exc)
                if "bot was blocked" in error_text or "chat not found" in error_text:
                    with db_connection() as conn:
                        conn.execute(
                            "UPDATE subscribers SET active=0 WHERE user_id=?",
                            (user["user_id"],),
                        )
                        conn.commit()
                # Do not mark failed deliveries as sent; the bot can retry next check.


def league_keyboard():
    rows = []
    for key, name in LEAGUES.items():
        rows.append([InlineKeyboardButton(name, callback_data=f"league:{key}")])
    return InlineKeyboardMarkup(rows)


def mode_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📰 Read All Football News", callback_data="mode:all")],
        [InlineKeyboardButton("⚽ Pick a Team", callback_data="mode:team")],
        [InlineKeyboardButton("⬅ Change League", callback_data="menu:leagues")],
    ])


def team_keyboard(league):
    rows = []
    teams = TEAMS.get(league, [])
    for index in range(0, len(teams), 2):
        row = []
        for team_name, _aliases in teams[index:index + 2]:
            row.append(InlineKeyboardButton(
                team_name,
                callback_data=f"team:{team_name}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="menu:modes")])
    return InlineKeyboardMarkup(rows)


def fan_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📰 Change News Preference", callback_data="menu:leagues")],
        [InlineKeyboardButton("🔄 Check for News Now", callback_data="news:now")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)
    context.user_data.clear()
    await update.message.reply_text(WELCOME, reply_markup=league_keyboard())


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "⚽ MOD Football Fan Menu\n\nChoose the league you want to follow:",
        reply_markup=league_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Use /start to choose a league and news preference.\n"
        "Use /menu to change your league or team.\n\n"
        "The bot checks for new relevant stories every 10 minutes."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""

    if data == "menu:leagues":
        context.user_data.clear()
        await query.edit_message_text(
            "Select the league you want to follow:",
            reply_markup=league_keyboard(),
        )
        return

    if data == "menu:modes":
        league = context.user_data.get("league")
        if not league:
            await query.edit_message_text(
                "Please select your league first:",
                reply_markup=league_keyboard(),
            )
            return
        await query.edit_message_text(
            f"You selected {LEAGUES[league]}.\n\nHow would you like to receive football news?",
            reply_markup=mode_keyboard(),
        )
        return

    if data.startswith("league:"):
        league = data.split(":", 1)[1]
        if league not in LEAGUES:
            return
        context.user_data["league"] = league
        set_user_preferences(user_id, league=league, mode=None, team=None)
        await query.edit_message_text(
            f"🏆 You selected {LEAGUES[league]}.\n\n"
            "How would you like to receive your football news?",
            reply_markup=mode_keyboard(),
        )
        return

    if data == "mode:all":
        league = context.user_data.get("league")
        if not league:
            user = get_user(user_id)
            league = user["league"] if user else None
        if not league:
            await query.edit_message_text(
                "Select your league first:",
                reply_markup=league_keyboard(),
            )
            return
        context.user_data["league"] = league
        set_user_preferences(user_id, league=league, mode="all", team=None)
        await query.edit_message_text(
            f"✅ Your preference is saved!\n\n"
            f"You will receive new news about the 15 popular clubs in {LEAGUES[league]}.\n"
            "I will check for fresh news every 10 minutes.\n\n"
            "Use /menu any time to change your selection.",
            reply_markup=fan_menu_keyboard(),
        )
        return

    if data == "mode:team":
        league = context.user_data.get("league")
        if not league:
            user = get_user(user_id)
            league = user["league"] if user else None
        if not league:
            await query.edit_message_text(
                "Select your league first:",
                reply_markup=league_keyboard(),
            )
            return
        context.user_data["league"] = league
        await query.edit_message_text(
            f"⚽ Choose the team you want to follow in {LEAGUES[league]}:",
            reply_markup=team_keyboard(league),
        )
        return

    if data.startswith("team:"):
        team_name = data.split(":", 1)[1]
        league = context.user_data.get("league")
        if not league:
            user = get_user(user_id)
            league = user["league"] if user else None
        if not league or team_name not in [team[0] for team in TEAMS.get(league, [])]:
            await query.edit_message_text(
                "Please select your league again:",
                reply_markup=league_keyboard(),
            )
            return
        set_user_preferences(user_id, league=league, mode="team", team=team_name)
        await query.edit_message_text(
            f"✅ You are now following {team_name} news in {LEAGUES[league]}.\n\n"
            "I will check for fresh news every 10 minutes and send new updates only.\n\n"
            "Use /menu any time to change your selection.",
            reply_markup=fan_menu_keyboard(),
        )
        return

    if data == "news:now":
        await query.edit_message_text(
            "🔎 I am checking the latest football news. "
            "If a new story matches your selection, I will send it shortly.\n\n"
            "Automatic checks continue every 10 minutes.",
            reply_markup=fan_menu_keyboard(),
        )
        # Run a scan for all subscribers; this user's preferences are applied during matching.
        asyncio.create_task(check_and_send_news(context.bot))
        return


async def post_init(application: Application):
    init_database()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        check_and_send_news,
        "interval",
        minutes=CHECK_EVERY_MINUTES,
        args=[application.bot],
        id="football_news_check",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
    )
    scheduler.start()
    application.bot_data["news_scheduler"] = scheduler
    logger.info("MOD Football Bot started. News check interval: %s minutes.", CHECK_EVERY_MINUTES)


async def post_shutdown(application: Application):
    scheduler = application.bot_data.get("news_scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing from Railway Variables.")
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY is missing. The bot can find RSS stories but cannot write articles.")

    init_database()
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Starting MOD Football Bot polling.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
