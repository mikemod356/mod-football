
import os
import re
import json
import html
import hashlib
import logging
import sqlite3
import asyncio
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# =========================================================
# CONFIGURATION
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

DATABASE_PATH = os.getenv("DATABASE_PATH", "football_bot.db")

CHECK_INTERVAL_MINUTES = 15
MAX_NEW_STORIES_PER_CHECK = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# FOOTBALL NEWS SOURCES
# =========================================================

RSS_FEEDS = [
    "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "https://www.skysports.com/rss/12040",
    "https://www.theguardian.com/football/rss",
    "https://www.espn.com/espn/rss/soccer/news",
]


# =========================================================
# DATABASE
# =========================================================

def get_connection():
    folder = os.path.dirname(os.path.abspath(DATABASE_PATH))
    os.makedirs(folder, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            user_id INTEGER PRIMARY KEY,
            joined_at TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS published_news (
            story_id TEXT PRIMARY KEY,
            title TEXT,
            published_at TEXT
        )
    """)

    connection.commit()
    return connection


def add_subscriber(user_id):
    with get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO subscribers (user_id, joined_at) VALUES (?, ?)",
            (user_id, datetime.now(timezone.utc).isoformat()),
        )


def get_subscribers():
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT user_id FROM subscribers"
        ).fetchall()

    return [row[0] for row in rows]


def is_published(story_id):
    with get_connection() as connection:
        result = connection.execute(
            "SELECT 1 FROM published_news WHERE story_id = ?",
            (story_id,),
        ).fetchone()

    return result is not None


def save_published(story_id, title):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO published_news
            (story_id, title, published_at)
            VALUES (?, ?, ?)
            """,
            (
                story_id,
                title,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


# =========================================================
# TEXT AND IMAGE HELPERS
# =========================================================

def clean_html(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def get_story_id(title):
    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        title.lower(),
    ).strip()

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def get_image_from_entry(entry):
    # RSS media content
    for media in entry.get("media_content", []):
        image_url = media.get("url")
        if image_url:
            return image_url

    # RSS thumbnail
    for media in entry.get("media_thumbnail", []):
        image_url = media.get("url")
        if image_url:
            return image_url

    # RSS enclosure
    for enclosure in entry.get("enclosures", []):
        image_url = enclosure.get("href") or enclosure.get("url")
        if image_url and "image" in enclosure.get("type", ""):
            return image_url

    # Image links
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure":
            image_url = link.get("href")
            if image_url and (
                "image" in link.get("type", "")
                or re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", image_url, re.I)
            ):
                return image_url

    return None


def get_page_image(article_url):
    if not article_url:
        return None

    try:
        request = urllib.request.Request(
            article_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            page = response.read(300000).decode(
                "utf-8",
                errors="ignore",
            )

        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, page, re.I)
            if match:
                image_url = html.unescape(match.group(1))
                if image_url.startswith("http"):
                    return image_url

    except Exception as error:
        logger.warning("Could not retrieve article image: %s", error)

    return None


# =========================================================
# FETCH ONLINE FOOTBALL NEWS
# =========================================================

def fetch_news(limit=30):
    stories = []
    seen_titles = set()

    for feed_url in RSS_FEEDS:
        try:
            request = urllib.request.Request(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0 (FootballBot/2.0)"},
            )

            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()

            feed = feedparser.parse(raw)

            for entry in feed.entries[:15]:
                title = clean_html(entry.get("title", ""))
                summary = clean_html(
                    entry.get("summary", "")
                    or entry.get("description", "")
                )
                link = entry.get("link", "")

                if not title or not link:
                    continue

                story_id = get_story_id(title)

                if story_id in seen_titles:
                    continue

                seen_titles.add(story_id)

                published = entry.get("published", "")

                image_url = get_image_from_entry(entry)

                stories.append({
                    "id": story_id,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image": image_url,
                    "published": published,
                    "source": feed.feed.get("title", "Football News"),
                })

        except Exception as error:
            logger.warning(
                "Could not read RSS feed %s: %s",
                feed_url,
                error,
            )

    # Prefer newer stories when publication dates are available.
    def sort_date(story):
        try:
            return parsedate_to_datetime(
                story["published"]
            ).timestamp()
        except Exception:
            return 0

    stories.sort(key=sort_date, reverse=True)

    return stories[:limit]


# =========================================================
# DEEPSEEK ARTICLE GENERATOR
# =========================================================

def generate_article(story):
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is missing from Railway Variables."
        )

    prompt = f"""
Write an original football news article based on the information below.

ARTICLE REQUIREMENTS:
- Write between 500 and 700 words.
- Use simple, natural, professional English.
- Give the article an engaging headline.
- Explain the important details clearly.
- Include relevant football background where it helps readers understand the story.
- Organize the article into readable paragraphs with useful subheadings.
- Do not invent scores, dates, injuries, transfers, quotes, statistics or statements.
- Do not present rumours as confirmed facts.
- If information is not available, do not make it up.
- Do not claim to have interviewed anyone.
- Keep the article focused on the supplied news.
- Do not include a fake source or fake quotation.

SOURCE HEADLINE:
{story["title"]}

SOURCE SUMMARY:
{story["summary"]}

ORIGINAL SOURCE:
{story["link"]}

SOURCE NAME:
{story["source"]}

Write the complete article now.
"""

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional football journalist. "
                    "Report accurately and never invent facts."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.5,
        "max_tokens": 2200,
        "stream": False,
    }

    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))

    article = result["choices"][0]["message"]["content"].strip()

    word_count = len(article.split())

    # Ask DeepSeek to expand an article if it is too short.
    if word_count < 500:
        expand_payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a careful football journalist. "
                        "Never invent facts."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Expand the following article to between 500 and "
                        "700 words. Preserve all facts, do not invent "
                        "details, and keep the original meaning:\n\n"
                        + article
                    ),
                },
            ],
            "temperature": 0.4,
            "max_tokens": 2200,
            "stream": False,
        }

        request = urllib.request.Request(
            DEEPSEEK_URL,
            data=json.dumps(expand_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))

        expanded = result["choices"][0]["message"]["content"].strip()

        if len(expanded.split()) > word_count:
            article = expanded

    return article


# =========================================================
# TELEGRAM MESSAGE DELIVERY
# =========================================================

def split_message(text, limit=3800):
    paragraphs = text.split("\n")
    chunks = []
    current = ""

    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= limit:
            current += paragraph + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
                current = ""

            while len(paragraph) > limit:
                chunks.append(paragraph[:limit])
                paragraph = paragraph[limit:]

            current = paragraph + "\n\n"

    if current.strip():
        chunks.append(current.strip())

    return chunks


async def send_article(bot, user_id, story, article, image_url):
    headline = story["title"]
    source_link = story["link"]

    caption = (
        f"⚽ {headline}\n\n"
        f"📰 Source: {source_link}"
    )

    photo_sent = False

    if image_url:
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=image_url,
                caption=caption[:1024],
            )
            photo_sent = True

        except Exception as error:
            logger.warning(
                "Could not send image for %s: %s",
                headline,
                error,
            )

    if not photo_sent:
        await bot.send_message(
            chat_id=user_id,
            text=caption,
            disable_web_page_preview=False,
        )

    for part in split_message(article):
        await bot.send_message(
            chat_id=user_id,
            text=part,
            disable_web_page_preview=True,
        )

    await bot.send_message(
        chat_id=user_id,
        text=f"🔗 Read the original report: {source_link}",
        disable_web_page_preview=True,
    )


# =========================================================
# NEWS CHECK AND AUTOMATIC BROADCAST
# =========================================================

async def check_and_send_news(bot):
    subscribers = get_subscribers()

    if not subscribers:
        logger.info("No subscribers yet. Waiting for users to start the bot.")
        return

    logger.info("Checking online football news...")

    stories = await asyncio.to_thread(fetch_news, 40)

    new_stories = [
        story for story in stories
        if not is_published(story["id"])
    ]

    if not new_stories:
        logger.info("No new football stories found.")
        return

    new_stories = new_stories[:MAX_NEW_STORIES_PER_CHECK]

    for story in new_stories:
        try:
            logger.info("Generating article: %s", story["title"])

            article = await asyncio.to_thread(
                generate_article,
                story,
            )

            image_url = story["image"]

            if not image_url:
                image_url = await asyncio.to_thread(
                    get_page_image,
                    story["link"],
                )

            successful_sends = 0

            for user_id in subscribers:
                try:
                    await send_article(
                        bot,
                        user_id,
                        story,
                        article,
                        image_url,
                    )
                    successful_sends += 1

                except Exception as error:
                    logger.warning(
                        "Could not send news to user %s: %s",
                        user_id,
                        error,
                    )

                    # Remove users who have blocked the bot.
                    if "bot was blocked" in str(error).lower():
                        with get_connection() as connection:
                            connection.execute(
                                "DELETE FROM subscribers WHERE user_id = ?",
                                (user_id,),
                            )

            if successful_sends > 0:
                save_published(story["id"], story["title"])

            logger.info(
                "Finished story: %s. Sent to %s users.",
                story["title"],
                successful_sends,
            )

        except Exception:
            logger.exception(
                "Could not process story: %s",
                story["title"],
            )


# =========================================================
# /START COMMAND
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    add_subscriber(user_id)

    welcome = (
        "⚽ Welcome to Mod Football Bot!\n\n"
        "Get football news from major clubs and competitions "
        "around the world.\n\n"
        "📰 Fresh football news\n"
        "🔄 Transfer updates\n"
        "🏆 Major football competitions\n"
        "✍️ Detailed AI-written football articles\n"
        "📸 Relevant football images where available\n\n"
        "You are now subscribed to automatic football news. "
        "We check for new stories every 15 minutes.\n\n"
        "Use the button below to view the latest headlines."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📰 Latest football headlines",
                callback_data="news|latest",
            )
        ]
    ])

    await update.message.reply_text(
        welcome,
        reply_markup=keyboard,
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def on_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data == "news|latest":
        await query.edit_message_text(
            "🔎 Getting the latest football headlines..."
        )

        stories = await asyncio.to_thread(fetch_news, 10)

        if not stories:
            await query.message.reply_text(
                "Sorry, I could not retrieve football news right now. "
                "Please try again later."
            )
            return

        message = "⚽ LATEST FOOTBALL HEADLINES\n\n"

        for index, story in enumerate(stories, 1):
            message += (
                f"{index}. {story['title']}\n"
                f"{story['link']}\n\n"
            )

        for part in split_message(message):
            await query.message.reply_text(
                part,
                disable_web_page_preview=True,
            )


# =========================================================
# SCHEDULER
# =========================================================

async def post_init(application):
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        check_and_send_news,
        trigger="interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[application.bot],
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )

    scheduler.start()
    application.bot_data["scheduler"] = scheduler

    logger.info(
        "Football news scheduler started. Checking every %s minutes.",
        CHECK_INTERVAL_MINUTES,
    )


async def post_shutdown(application):
    scheduler = application.bot_data.get("scheduler")

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is missing. Add it to Railway Variables."
        )

    if not DEEPSEEK_API_KEY:
        logger.warning(
            "DEEPSEEK_API_KEY is missing. Add it to Railway Variables "
            "before automatic article generation can work."
        )

    get_connection().close()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))

    logger.info("Mod Football Bot is starting...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
