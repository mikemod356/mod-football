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
