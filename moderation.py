"""Core auto-mod: spam detection, phishing, airdrop scam detection, LLM classification."""
from __future__ import annotations
import asyncio, datetime, re, logging
from collections import defaultdict, deque
import discord, config, database as db, llm

log = logging.getLogger("moderation")
_PHISHING_PATTERNS = re.compile(r"(discord[\.\-]?gift|free[\.\-]?nitro|steam[\.\-]?gift|bit\.ly|tinyurl|gg/[a-z0-9]{6,}|\.ru/|\.xyz/|\.tk/)", re.IGNORECASE)

# Airdrop / wallet scam patterns: auto-ban
_AIRDROP_SCAM_PATTERNS = re.compile(
    r"(airdrop\s*(is\s*)?live|claim\s*your\s*airdrop|free\s*airdrop|airdrop\s*alert"
    r"|send\s*(your\s*)?(wallet|seed\s*phrase)|dm\s*(me\s*)?(your\s*)?wallet"
    r"|connect\s*your\s*wallet|wallet\s*via\s*dm|sell\s*(your\s*)?wallet"
    r"|drop\s*your\s*wallet|paste\s*your\s*(wallet|seed)|claim\s*free\s*token"
    r"|airdrop\s*drop|token\s*airdrop\s*live)",
    re.IGNORECASE
)

_message_timestamps = defaultdict(deque)


def _is_immune(member) -> bool:
    """Check if member has an immune role."""
    if not hasattr(member, "roles"):
        return False
    for role in member.roles:
        if role.id in config.IMMUNE_ROLE_IDS:
            return True
    return False


def _is_founder(user) -> bool:
    """Check if user is the founder (SON OF PEACE)."""
    return user.id == config.FOUNDER_ID


def _is_ignored_channel(channel_id) -> bool:
    """Check if bot should ignore this channel entirely."""
    return channel_id in config.IGNORED_CHANNEL_IDS


def _is_spam(user_id):
    now = datetime.datetime.utcnow().timestamp()
    dq = _message_timestamps[user_id]; dq.append(now)
    while dq and dq[0] < now - config.SPAM_WINDOW_SECONDS: dq.popleft()
    return len(dq) >= config.SPAM_MESSAGE_COUNT


async def handle_message(bot, message):
    if message.author.bot or db.is_spam_exempt(message.author.id):
        return
    if _is_immune(message.author):
        return
    if _is_ignored_channel(message.channel.id):
        return

    # Airdrop/wallet scam: ALWAYS ban, even before other checks
    if _AIRDROP_SCAM_PATTERNS.search(message.content):
        await _handle_airdrop_scam(bot, message)
        return

    if _PHISHING_PATTERNS.search(message.content):
        await _handle_phishing(bot, message); return
    if _is_spam(message.author.id):
        await _handle_spam(bot, message); return
    if len(message.content) > 15:
        result = await llm.classify_violation(message.content)
        if result.get("violation"):
            await _handle_violation(bot, message, result)


async def handle_message_light(bot, message):
    """Light moderation for ticket channels: only airdrop scam, phishing, and spam.
    No LLM classify to avoid double-replying in tickets."""
    if message.author.bot or db.is_spam_exempt(message.author.id):
        return
    if _is_immune(message.author):
        return
    if _AIRDROP_SCAM_PATTERNS.search(message.content):
        await _handle_airdrop_scam(bot, message)
        return
    if _PHISHING_PATTERNS.search(message.content):
        await _handle_phishing(bot, message)
        return
    if _is_spam(message.author.id):
        await _handle_spam(bot, message)


async def _handle_airdrop_scam(bot, message):
    """Airdrop/wallet scam: delete message and ban immediately."""
    m, g = message.author, message.guild
    try: await message.delete()
    except: pass
    reason = "Airdrop/wallet scam detected"
    db.add_warning(g.id, m.id, "AutoMod", reason)
    db.log_action(g.id, "BAN(airdrop scam)", m.id, "AutoMod", reason)
    # DM them before ban
    try:
        await m.send(
            "You have been banned from NEXTGEN for posting airdrop or wallet scam content. "
            "This is a zero-tolerance policy. If you believe this was a mistake, contact a moderator."
        )
    except: pass
    # Ban
    try:
        await g.ban(m, reason=reason, delete_message_days=1)
        log.info(f"BANNED {m} for airdrop scam")
    except Exception as e:
        log.error(f"Failed to ban {m} for airdrop scam: {e}")
    await _post_log(bot, g, "BAN (AIRDROP SCAM)", m, reason)


async def _handle_phishing(bot, message):
    try: await message.delete()
    except: pass
    m, g = message.author, message.guild
    total = db.add_warning(g.id, m.id, "AutoMod", "Phishing link")
    db.log_action(g.id, "DELETE+WARN(phishing)", m.id, "AutoMod", "Phishing link")
    try:
        r = await llm.generate("Tell this member their message was removed for a suspected phishing link. Be firm but fair. Let them know repeated violations will lead to a timeout.")
        await m.send(r)
    except: pass
    if total >= 3:
        await _escalate(bot, g, m, "Repeated phishing links", total)
    else:
        await _post_log(bot, g, "PHISHING DETECTED", m, "Phishing link", total)


async def _handle_spam(bot, message):
    try: await message.delete()
    except: pass
    m, g = message.author, message.guild
    total = db.add_warning(g.id, m.id, "AutoMod", "Spam")
    db.log_action(g.id, "WARN(spam)", m.id, "AutoMod", "Spam")
    if total >= config.WARN_BEFORE_MUTE:
        await _timeout(bot, g, m, "Repeated spam")
    else:
        t = await llm.generate(f"Tell this member to slow down, they are sending messages too fast. Warning {total} of {config.WARN_BEFORE_MUTE}. Keep it friendly and calm.")
        try: await message.channel.send(f"{m.mention} {t}", delete_after=15)
        except: pass
        await _post_log(bot, g, "SPAM WARNING", m, "Spam", total)


async def _handle_violation(bot, message, result):
    m, g = message.author, message.guild
    cat, sev = result.get("category", "violation"), result.get("severity", 1)
    reason = f"Detected: {cat}"

    if sev >= 3:
        try: await message.delete()
        except: pass
        total = db.add_warning(g.id, m.id, "AutoMod", reason)
        db.log_action(g.id, "TIMEOUT+ESCALATE(severe)", m.id, "AutoMod", reason)
        await _timeout(bot, g, m, reason)
        await _escalate(bot, g, m, f"Severe violation: {cat}", total)
        return

    if sev == 2:
        try: await message.delete()
        except: pass
        total = db.add_warning(g.id, m.id, "AutoMod", reason)
        db.log_action(g.id, "DELETE+WARN(violation)", m.id, "AutoMod", reason)
        if total >= config.WARN_BEFORE_MUTE:
            await _timeout(bot, g, m, reason)
        else:
            t = await llm.generate(f"Tell this member their message was removed. Reason: {cat}. Warning {total} of {config.WARN_BEFORE_MUTE}. Calm and fair.")
            try: await message.channel.send(f"{m.mention} {t}", delete_after=20)
            except: pass
        await _post_log(bot, g, f"WARN ({cat.upper()})", m, reason, total)
        return

    total = db.add_warning(g.id, m.id, "AutoMod", reason)
    db.log_action(g.id, "WARN(violation)", m.id, "AutoMod", reason)
    t = await llm.generate(f"Give this member a gentle heads up about: {cat}. Warning {total}. Friendly and calm, not threatening.")
    try: await message.channel.send(f"{m.mention} {t}", delete_after=20)
    except: pass
    if total >= config.WARN_BEFORE_MUTE:
        await _timeout(bot, g, m, reason)
    await _post_log(bot, g, f"WARN ({cat.upper()})", m, reason, total)


async def _timeout(bot, guild, member, reason):
    duration = datetime.timedelta(minutes=config.TIMEOUT_DURATION_MIN)
    try:
        await member.timeout(duration, reason=reason)
        log.info(f"Timed out {member} for {config.TIMEOUT_DURATION_MIN}m: {reason}")
    except discord.Forbidden:
        log.warning(f"Cannot timeout {member}, missing permissions or higher role")
        await _role_mute(guild, member, reason)
    except Exception as e:
        log.error(f"Timeout error for {member}: {e}")
        await _role_mute(guild, member, reason)
    try:
        t = await llm.generate(
            f"Tell this member they have been timed out for {config.TIMEOUT_DURATION_MIN} minutes. "
            f"Reason: {reason}. Be calm and understanding, not harsh."
        )
        await member.send(t)
    except: pass
    db.log_action(guild.id, f"TIMEOUT ({config.TIMEOUT_DURATION_MIN}m)", member.id, "AutoMod", reason)
    await _post_log(bot, guild, "TIMEOUT", member, reason)


async def _role_mute(guild, member, reason):
    role = guild.get_role(config.MUTED_ROLE_ID) or discord.utils.get(guild.roles, name="Muted")
    if role:
        try:
            await member.add_roles(role, reason=reason)
            asyncio.create_task(_auto_unmute(member, role, config.MUTE_DURATION_MIN))
        except: pass


async def _escalate(bot, guild, member, reason, warning_count=0):
    ch = guild.get_channel(config.LOG_CHANNEL_ID)
    if not ch: return
    role_pings = " ".join(f"<@&{rid}>" for rid in config.IMMUNE_ROLE_IDS)
    e = discord.Embed(
        title="Escalation: Manual Review Needed",
        color=discord.Color.red(),
        timestamp=datetime.datetime.utcnow()
    )
    e.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
    e.add_field(name="Reason", value=reason, inline=True)
    e.add_field(name="Warnings", value=str(warning_count), inline=True)
    e.set_footer(text="AutoMod escalation. Please review and take action.")
    try: await ch.send(f"{role_pings} This member needs manual review.", embed=e)
    except: pass


async def _mute(bot, guild, member, reason):
    await _timeout(bot, guild, member, reason)


async def _auto_unmute(member, role, minutes):
    await asyncio.sleep(minutes * 60)
    try: await member.remove_roles(role, reason="Mute expired")
    except: pass


async def _post_log(bot, guild, action, member, reason, wt=0):
    ch = guild.get_channel(config.LOG_CHANNEL_ID)
    if not ch: return
    cm = {"BAN": discord.Color.red(), "TIMEOUT": discord.Color.orange(), "PHISHING": discord.Color.dark_red()}
    color = next((v for k, v in cm.items() if k in action), discord.Color.yellow())
    e = discord.Embed(title=f"Mod Action: {action}", color=color, timestamp=datetime.datetime.utcnow())
    e.add_field(name="User", value=f"{member} ({member.id})", inline=True)
    e.add_field(name="Reason", value=reason, inline=True)
    if wt: e.add_field(name="Total Warnings", value=str(wt), inline=True)
    e.set_footer(text="AutoMod")
    try: await ch.send(embed=e)
    except: pass
