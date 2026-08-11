"""Auto-kick inactive members.

Once a day, kicks any non-immune, non-founder, non-bot member who hasn't sent a message
in AUTO_KICK_INACTIVE_DAYS (default 7). Immune-role holders and the founder are always
exempt (see moderation._is_immune / config.FOUNDER_ID).

Safety: activity is tracked going forward from the moment this feature is deployed. On
first startup, seed_and_start() gives every member the bot doesn't already have a record
for a fresh "seen now" timestamp -- so nobody is kicked for silence that happened before
the bot could possibly have been tracking them. New joiners get the same fresh clock via
bot.py's on_member_join. A member with genuinely no timestamp (shouldn't happen after
seeding) falls back to their join date, never to "kick immediately".
"""
from __future__ import annotations
import asyncio, datetime, logging
import discord
from discord.ext import tasks
import config, database as db, llm, moderation

log = logging.getLogger("retention")

_bot = None


def _tzinfo():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.PROMPT_TZ)
    except Exception as e:
        log.warning(f"Could not load tz '{config.PROMPT_TZ}' ({e}); using fixed UTC+1.")
        return datetime.timezone(datetime.timedelta(hours=1))


_TZ = _tzinfo()


def _is_exempt(member) -> bool:
    if member.bot:
        return True
    if member.id == config.FOUNDER_ID:
        return True
    return moderation._is_immune(member)


async def seed_and_start(bot):
    """Give every currently-untracked member a fresh activity timestamp (so this feature
    never retroactively punishes members for pre-deploy silence), then start the daily
    check. Safe to call every startup: only members with no existing record are touched."""
    global _bot
    _bot = bot
    if not config.AUTO_KICK_ENABLED:
        log.info("AUTO_KICK_ENABLED is false; retention check disabled.")
        return
    for guild in bot.guilds:
        try:
            known = db.get_all_activity(guild.id)
            seeded = 0
            for member in guild.members:
                if member.bot:
                    continue
                if member.id not in known:
                    db.touch_activity(guild.id, member.id)
                    seeded += 1
            if seeded:
                log.info(f"Seeded activity for {seeded} previously-untracked member(s) in '{guild.name}'.")
        except Exception as e:
            log.error(f"Failed to seed activity for guild {guild.id}: {e}")
    if not _daily_check.is_running():
        _daily_check.start()
        log.info(
            f"Retention check started ({config.RETENTION_CHECK_HOUR}:00 {config.PROMPT_TZ}, "
            f"kicks after {config.AUTO_KICK_INACTIVE_DAYS}d inactive)."
        )


@tasks.loop(time=datetime.time(hour=config.RETENTION_CHECK_HOUR, minute=0, tzinfo=_TZ))
async def _daily_check():
    if _bot is None or not config.AUTO_KICK_ENABLED:
        return
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=config.AUTO_KICK_INACTIVE_DAYS)
    for guild in _bot.guilds:
        try:
            activity = db.get_all_activity(guild.id)
        except Exception as e:
            log.error(f"Failed to read activity for guild {guild.id}: {e}")
            continue
        for member in list(guild.members):
            if _is_exempt(member):
                continue
            last_seen = _parse_last_seen(activity.get(member.id), member)
            if last_seen > cutoff:
                continue
            await _kick_for_inactivity(guild, member)
            await asyncio.sleep(1)  # be gentle with the API on larger sweeps


def _parse_last_seen(raw, member) -> datetime.datetime:
    if raw:
        try:
            return datetime.datetime.fromisoformat(raw)
        except ValueError:
            pass
    # No usable record (shouldn't happen after seeding): fall back to join date so
    # nobody is kicked without at least one full grace period.
    if member.joined_at:
        return member.joined_at.replace(tzinfo=None)
    return datetime.datetime.utcnow()


async def _kick_for_inactivity(guild, member):
    reason = f"Inactive {config.AUTO_KICK_INACTIVE_DAYS}+ days"
    try:
        t = await llm.generate(
            f"Tell this member they were removed from NEXTGEN for being inactive for "
            f"{config.AUTO_KICK_INACTIVE_DAYS} days. Be kind, not harsh. Let them know they're "
            f"welcome to rejoin any time and jump back in."
        )
        await member.send(t)
    except Exception:
        pass
    try:
        await member.kick(reason=reason)
        log.info(f"Kicked {member} for inactivity ({reason})")
    except discord.Forbidden:
        log.warning(f"Cannot kick {member}: missing permissions or higher role")
        return
    except Exception as e:
        log.error(f"Kick error for {member}: {e}")
        return
    db.log_action(guild.id, "KICK(inactive)", member.id, "AutoMod", reason)
    db.remove_activity(guild.id, member.id)
    ch = guild.get_channel(config.LOG_CHANNEL_ID)
    if ch:
        e = discord.Embed(
            title="Mod Action: KICK (inactivity)",
            color=discord.Color.dark_grey(),
            timestamp=datetime.datetime.utcnow(),
        )
        e.add_field(name="User", value=f"{member} ({member.id})", inline=True)
        e.add_field(name="Reason", value=reason, inline=True)
        e.set_footer(text="AutoMod retention")
        try: await ch.send(embed=e)
        except Exception: pass


@_daily_check.before_loop
async def _before():
    if _bot is not None:
        await _bot.wait_until_ready()
