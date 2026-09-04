"""Auto-kick members who don't meet the activity quota, with a warning first.

Each non-immune, non-founder, non-bot member has a rolling "cycle": they need to send
AUTO_KICK_REQUIRED_MESSAGES messages (default 10) within AUTO_KICK_INACTIVE_DAYS days
(default 14) of their cycle starting, or they're kicked when the cycle ends. Once a day:

  - AUTO_KICK_WARNING_DAYS into the cycle (default 7, the halfway point) and still short
    of the quota -> ping them once in RETENTION_WARNING_CHANNEL_ID showing their progress
    and the consequence.
  - AUTO_KICK_INACTIVE_DAYS into the cycle: quota met -> the cycle resets fresh (0/quota,
    warning cleared). Quota missed -> kicked.

Immune-role holders and the founder are always exempt (see moderation._is_immune /
config.FOUNDER_ID). The warning fires at most once per cycle: it's cleared the moment the
cycle resets, so a member who falls short again next time gets a fresh warning, never
silence straight to a kick.

Safety: activity is tracked going forward from the moment this feature is deployed. On
first startup, seed_and_start() gives every member the bot doesn't already have a record
for a fresh, empty cycle starting now -- so nobody is judged on messages sent before the
bot could possibly have been counting them. New joiners get the same fresh cycle via
bot.py's on_member_join. A member with genuinely no cycle_start (shouldn't happen after
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
    """Give every currently-untracked member a fresh, empty cycle (so this feature never
    retroactively punishes members for messages sent before it could count them), then
    start the daily check. Safe to call every startup: members already tracked with a
    real cycle_start are left untouched. A row that exists but has no cycle_start yet
    (e.g. migrated from an older schema) is also backfilled to a fresh cycle here --
    left alone, _parse_cycle_start would fall back to their join date, which for a
    long-standing member could already be past the day threshold and trigger an
    immediate kick on the very first check."""
    global _bot
    _bot = bot
    if not config.AUTO_KICK_ENABLED:
        log.info("AUTO_KICK_ENABLED is false; retention check disabled.")
        return
    for guild in bot.guilds:
        try:
            known = db.get_all_activity(guild.id)
            new_ids = []
            needs_reset = []  # tracked, but no cycle_start (pre-quota-feature row) -- rare
            for member in guild.members:
                if member.bot:
                    continue
                row = known.get(member.id)
                if row is None:
                    new_ids.append(member.id)
                elif row[0] is None:
                    needs_reset.append(member.id)
            # One round trip for the (usually much larger) new-member batch, instead of one
            # network call per member -- with a remote Postgres connection, N sequential
            # round trips on a server of any real size can block the event loop long enough
            # to trip Discord's heartbeat timeout at startup.
            if new_ids:
                db.seed_cycles_bulk(guild.id, new_ids)
            for uid in needs_reset:
                db.reset_cycle(guild.id, uid)
            seeded = len(new_ids) + len(needs_reset)
            if seeded:
                log.info(f"Seeded a fresh cycle for {seeded} member(s) in '{guild.name}'.")
        except Exception as e:
            log.error(f"Failed to seed activity for guild {guild.id}: {e}")
    if not _daily_check.is_running():
        _daily_check.start()
        log.info(
            f"Retention check started ({config.RETENTION_CHECK_HOUR}:00 {config.PROMPT_TZ}): "
            f"need {config.AUTO_KICK_REQUIRED_MESSAGES} messages per {config.AUTO_KICK_INACTIVE_DAYS}d, "
            f"warns at {config.AUTO_KICK_WARNING_DAYS}d if short."
        )


@tasks.loop(time=datetime.time(hour=config.RETENTION_CHECK_HOUR, minute=0, tzinfo=_TZ))
async def _daily_check():
    if _bot is None or not config.AUTO_KICK_ENABLED:
        return
    now = datetime.datetime.utcnow()
    quota = config.AUTO_KICK_REQUIRED_MESSAGES
    for guild in _bot.guilds:
        try:
            activity = db.get_all_activity(guild.id)
        except Exception as e:
            log.error(f"Failed to read activity for guild {guild.id}: {e}")
            continue

        to_warn = []  # (member, msg_count) pairs
        for member in list(guild.members):
            if _is_exempt(member):
                continue
            cycle_start_raw, msg_count, warned_at_raw = activity.get(member.id, (None, 0, None))
            cycle_start = _parse_cycle_start(cycle_start_raw, member)
            msg_count = msg_count or 0
            days_elapsed = (now - cycle_start).total_seconds() / 86400

            if days_elapsed >= config.AUTO_KICK_INACTIVE_DAYS:
                if msg_count >= quota:
                    db.reset_cycle(guild.id, member.id)  # met the quota -> fresh cycle
                else:
                    await _kick_for_inactivity(guild, member, msg_count)
                    await asyncio.sleep(1)  # be gentle with the API on larger sweeps
                continue

            if (days_elapsed >= config.AUTO_KICK_WARNING_DAYS
                    and msg_count < quota
                    and not warned_at_raw):
                to_warn.append((member, msg_count))
                db.mark_warned(guild.id, member.id)

        if to_warn:
            await _send_warnings(guild, to_warn)


def _parse_cycle_start(raw, member) -> datetime.datetime:
    if raw:
        try:
            return datetime.datetime.fromisoformat(raw)
        except ValueError:
            pass
    # No usable record (shouldn't happen after seeding): fall back to join date so
    # nobody is warned or kicked without at least one full cycle's grace period.
    if member.joined_at:
        return member.joined_at.replace(tzinfo=None)
    return datetime.datetime.utcnow()


async def _send_warnings(guild, entries):
    """Ping each member short of quota at the halfway mark (exempt roles already filtered
    by the caller), showing their progress and the consequence."""
    ch = guild.get_channel(config.RETENTION_WARNING_CHANNEL_ID)
    if not ch:
        log.warning(
            f"{len(entries)} member(s) crossed the inactivity warning threshold in "
            f"'{guild.name}' but RETENTION_WARNING_CHANNEL_ID is not set or the channel "
            f"was not found; no warning sent."
        )
        return
    quota = config.AUTO_KICK_REQUIRED_MESSAGES
    days_left = max(config.AUTO_KICK_INACTIVE_DAYS - config.AUTO_KICK_WARNING_DAYS, 1)
    plural = "s" if days_left != 1 else ""
    allowed = discord.AllowedMentions(everyone=False, roles=False, users=True)
    CHUNK = 15  # each line carries a progress count, keep messages a reasonable size
    for i in range(0, len(entries), CHUNK):
        chunk = entries[i:i + CHUNK]
        lines = [f"{m.mention} -- {c}/{quota} messages" for m, c in chunk]
        text = (
            "heads up, you're behind on activity:\n" + "\n".join(lines) + "\n\n"
            f"you need {quota} messages every {config.AUTO_KICK_INACTIVE_DAYS} days to stay in "
            f"NEXTGEN. you've got {days_left} more day{plural} to catch up before you're removed."
        )
        try:
            await ch.send(text, allowed_mentions=allowed)
            log.info(f"Sent inactivity warning to {len(chunk)} member(s) in '{guild.name}'.")
        except Exception as e:
            log.error(f"Failed to send inactivity warning: {e}")


async def _kick_for_inactivity(guild, member, msg_count):
    reason = f"Only {msg_count}/{config.AUTO_KICK_REQUIRED_MESSAGES} messages in {config.AUTO_KICK_INACTIVE_DAYS} days"
    try:
        t = await llm.generate(
            f"Tell this member they were removed from NEXTGEN for not sending "
            f"{config.AUTO_KICK_REQUIRED_MESSAGES} messages within {config.AUTO_KICK_INACTIVE_DAYS} days "
            f"(they sent {msg_count}). Be kind, not harsh. Let them know they're welcome to rejoin "
            f"any time and jump back in."
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
