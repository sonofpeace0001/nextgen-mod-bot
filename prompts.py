"""Daily prompt scheduler (Task 2).

Posts ONE prompt per day into a public channel (config.PROMPT_CHANNEL_ID) at a fixed
local time (config.PROMPT_HOUR in config.PROMPT_TZ). Rotates through the bank in order;
the current index is stored in the DB so it survives restarts and does not repeat until
the bank is exhausted. Public only -- never DMs, never @everyone.
"""
from __future__ import annotations
import datetime, logging
from discord.ext import tasks
import config, database as db

log = logging.getLogger("prompts")

PROMPT_BANK = [
    "what's the first thing you'd want AI to do for you if it could? one line.",
    "what's one AI tool you keep hearing about but haven't tried yet?",
    "what's the most confusing AI word you keep seeing? drop it, we'll demystify it together.",
    "what do you spend too much time on that you wish you could speed up?",
    "phones out: what's one app on your phone right now that already uses AI?",
    "if you could design one graphic today, what would it be for?",
    "drop one word that describes what you want from NEXTGEN this month.",
    "what's stopping you from starting? be honest, no judgment here.",
    "tiny challenge: open an AI image tool, make one graphic, drop it below. rough drafts welcome.",
    "what did you make yesterday? show it, even if it's rough. especially if it's rough.",
    "what's one thing you learned this week that surprised you?",
    "if you taught one person one AI thing today, what would it be?",
    "what would make this community more useful for you? tell me, i'm listening.",
    "share one win from this week, big or tiny. all count.",
]

_INDEX_KEY = "prompt_index"
_LAST_DATE_KEY = "prompt_last_date"

_bot = None


def _tzinfo():
    """Resolve PROMPT_TZ, falling back to WAT (UTC+1) if zoneinfo/tzdata is missing."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.PROMPT_TZ)
    except Exception as e:
        log.warning(f"Could not load tz '{config.PROMPT_TZ}' ({e}); using fixed UTC+1.")
        return datetime.timezone(datetime.timedelta(hours=1))


_TZ = _tzinfo()


@tasks.loop(time=datetime.time(hour=config.PROMPT_HOUR, minute=0, tzinfo=_TZ))
async def _daily_prompt():
    if _bot is None:
        return
    today = datetime.datetime.now(_TZ).date().isoformat()
    if db.kv_get(_LAST_DATE_KEY) == today:
        return  # already posted today (e.g. restart after the scheduled time)

    ch = _bot.get_channel(config.PROMPT_CHANNEL_ID)
    if not ch:
        log.warning("PROMPT_CHANNEL_ID not set or channel not found; skipping daily prompt.")
        return

    idx = int(db.kv_get(_INDEX_KEY, 0) or 0) % len(PROMPT_BANK)
    prompt = PROMPT_BANK[idx]
    try:
        await ch.send(prompt)  # public only, no @everyone, never a DM
        db.kv_set(_INDEX_KEY, (idx + 1) % len(PROMPT_BANK))
        db.kv_set(_LAST_DATE_KEY, today)
        log.info(f"Posted daily prompt #{idx} to channel {config.PROMPT_CHANNEL_ID}.")
    except Exception as e:
        log.error(f"Failed to post daily prompt: {e}")


@_daily_prompt.before_loop
async def _before():
    if _bot is not None:
        await _bot.wait_until_ready()


def start(bot):
    """Start the scheduler. No-op if PROMPT_CHANNEL_ID is unset or already running."""
    global _bot
    _bot = bot
    if not config.PROMPT_CHANNEL_ID:
        log.info("PROMPT_CHANNEL_ID not set; daily prompt scheduler disabled.")
        return
    if not _daily_prompt.is_running():
        _daily_prompt.start()
        log.info(f"Daily prompt scheduler started ({config.PROMPT_HOUR}:00 {config.PROMPT_TZ}).")
