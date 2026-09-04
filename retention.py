"""Auto-kick inactive members, with a warning first.

Once a day, checks every non-immune, non-founder, non-bot member:
  - AUTO_KICK_WARNING_DAYS inactive (default 7)  -> ping them once in
    RETENTION_WARNING_CHANNEL_ID warning that they'll be kicked if they stay quiet.
  - AUTO_KICK_INACTIVE_DAYS inactive (default 14) -> kick them.
Immune-role holders and the founder are always exempt (see moderation._is_immune /
config.FOUNDER_ID). The warning is sent at most once per inactivity cycle: sending a
message resets it (see database.touch_activity), so a member who comes back after being
warned gets a fresh warning next time, not silence straight to a kick.

Safety: activity is tracked going forward from the moment this feature is deployed. On
first startup, seed_and_start() gives every member the bot doesn't already have a record
for a fresh "seen now" timestamp -- so nobody is warned or kicked for silence that
happened before the bot could possibly have been tracking them. New joiners get the same
fresh clock via bot.py's on_member_join. A member with genuinely no timestamp (shouldn't
happen after seeding) falls back to their join date, never to "kick immediately".
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
            f"warns after {config.AUTO_KICK_WARNING_DAYS}d inactive, "
            f"kicks after {config.AUTO_KICK_INACTIVE_DAYS}d inactive)."
        )


@tasks.loop(time=datetime.time(hour=config.RETENTION_CHECK_HOUR, minute=0, tzinfo=_TZ))
async def _daily_check():
    if _bot is None or not config.AUTO_KICK_ENABLED:
        return
    now = datetime.datetime.utcnow()
    kick_cutoff = now - datetime.timedelta(days=config.AUTO_KICK_INACTIVE_DAYS)
    warn_cutoff = now - datetime.timedelta(days=config.AUTO_KICK_WARNING_DAYS)
    for guild in _bot.guilds:
        try:
            activity = db.get_all_activity(guild.id)
        except Exception as e:
            log.error(f"Failed to read activity for guild {guild.id}: {e}")
            continue

        to_warn = []
        for member in list(guild.members):
            if _is_exempt(member):
                continue
            last_seen_raw, warned_at_raw = activity.get(member.id, (None, None))
            last_seen = _parse_last_seen(last_seen_raw, member)

            if last_seen <= kick_cutoff:
                await _kick_for_inactivity(guild, member)
                await asyncio.sleep(1)  # be gentle with the API on larger sweeps
                continue

            if last_seen <= warn_cutoff and not warned_at_raw:
                to_warn.append(member)
                db.mark_warned(guild.id, member.id)

        if to_warn:
            await _send_warnings(guild, to_warn)


def _parse_last_seen(raw, member) -> datetime.datetime:
    if raw:
        try:
            return datetime.datetime.fromisoformat(raw)
        except ValueError:
            pass
    # No usable record (shouldn't happen after seeding): fall back to join date so
    # nobody is warned or kicked without at least one full grace period.
    if member.joined_at:
        return member.joined_at.replace(tzinfo=None)
    return datetime.datetime.utcnow()


async def _send_warnings(guild, members):
    """Ping each newly-crossed-the-threshold member (except immune roles, already
    filtered by the caller) in the warning channel, telling them the consequence."""
    ch = guild.get_channel(config.RETENTION_WARNING_CHANNEL_ID)
    if not ch:
        log.warning(
            f"{len(members)} member(s) crossed the inactivity warning threshold in "
            f"'{guild.name}' but RETENTION_WARNING_CHANNEL_ID is not set or the channel "
            f"was not found; no warning sent."
        )
        return
    days_left = max(config.AUTO_KICK_INACTIVE_DAYS - config.AUTO_KICK_WARNING_DAYS, 1)
    plural = "s" if days_left != 1 else ""
    allowed = discord.AllowedMentions(everyone=False, roles=False, users=True)
    CHUNK = 20  # keep each ping message a reasonable size
    for i in range(0, len(members), CHUNK):
        chunk = members[i:i + CHUNK]
        mentions = " ".join(m.mention for m in chunk)
        text = (
            f"{mentions}\n"
            f"you haven't been active in NEXTGEN in a while. stay quiet for {days_left} more "
            f"day{plural} and you'll be removed for inactivity. drop a message anywhere to stay."
        )
        try:
            await ch.send(text, allowed_mentions=allowed)
            log.info(f"Sent inactivity warning to {len(chunk)} member(s) in '{guild.name}'.")
        except Exception as e:
            log.error(f"Failed to send inactivity warning: {e}")


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
