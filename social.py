"""Daily social-media reminder.

Posts one reminder per day into a public channel, nudging members to check whether
they've seen today's NEXTGEN post on socials. Public only, never DMs, never @everyone.
Rotates through a small bank of phrasings (same pattern as prompts.py) so it doesn't
read as a copy-pasted bot line every day.
"""
from __future__ import annotations
import datetime, logging
from discord.ext import tasks
import config, database as db

log = logging.getLogger("social")

REMINDER_BANK = [
    "quick one: have you seen today's NEXTGEN post on socials yet?",
    "today's NEXTGEN post is up on socials, go check it out and show some love.",
    "don't miss today's post from NEXTGEN, it's live on socials now.",
    "have you caught today's NEXTGEN update on socials? worth a look.",
]

_INDEX_KEY = "social_index"
_LAST_DATE_KEY = "social_last_date"

_bot = None


def _tzinfo():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.PROMPT_TZ)
    except Exception as e:
        log.warning(f"Could not load tz '{config.PROMPT_TZ}' ({e}); using fixed UTC+1.")
        return datetime.timezone(datetime.timedelta(hours=1))


_TZ = _tzinfo()


def _links_line() -> str:
    links = [l.strip() for l in config.SOCIAL_LINKS.split(",") if l.strip()]
    return " ".join(links)


@tasks.loop(time=datetime.time(hour=config.SOCIAL_REMINDER_HOUR, minute=0, tzinfo=_TZ))
async def _daily_reminder():
    if _bot is None or not config.SOCIAL_REMINDER_ENABLED:
        return
    today = datetime.datetime.now(_TZ).date().isoformat()
    if db.kv_get(_LAST_DATE_KEY) == today:
        return  # already posted today (e.g. restart after the scheduled time)

    ch = _bot.get_channel(config.SOCIAL_REMINDER_CHANNEL_ID)
    if not ch:
        log.warning("SOCIAL_REMINDER_CHANNEL_ID not set or channel not found; skipping.")
        return

    idx = int(db.kv_get(_INDEX_KEY, 0) or 0) % len(REMINDER_BANK)
    text = REMINDER_BANK[idx]
    links = _links_line()
    if links:
        text += f" {links}"
    try:
        await ch.send(text)  # public only, no @everyone, never a DM
        db.kv_set(_INDEX_KEY, (idx + 1) % len(REMINDER_BANK))
        db.kv_set(_LAST_DATE_KEY, today)
        log.info(f"Posted daily social reminder #{idx} to channel {config.SOCIAL_REMINDER_CHANNEL_ID}.")
    except Exception as e:
        log.error(f"Failed to post social reminder: {e}")


@_daily_reminder.before_loop
async def _before():
    if _bot is not None:
        await _bot.wait_until_ready()


def start(bot):
    """Start the scheduler. No-op if disabled or no channel is configured."""
    global _bot
    _bot = bot
    if not config.SOCIAL_REMINDER_ENABLED:
        log.info("SOCIAL_REMINDER_ENABLED is false; social reminder disabled.")
        return
    if not config.SOCIAL_REMINDER_CHANNEL_ID:
        log.info("SOCIAL_REMINDER_CHANNEL_ID not set; social reminder disabled.")
        return
    if not _daily_reminder.is_running():
        _daily_reminder.start()
        log.info(f"Social reminder started ({config.SOCIAL_REMINDER_HOUR}:00 {config.PROMPT_TZ}).")
