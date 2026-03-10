"""Core auto-mod: spam detection, phishing, LLM classification, escalation."""
from __future__ import annotations
import asyncio, datetime, re, logging
from collections import defaultdict, deque
import discord, config, database as db, llm

log = logging.getLogger("moderation")
_PHISHING_PATTERNS = re.compile(r"(discord[\.\-]?gift|free[\.\-]?nitro|steam[\.\-]?gift|bit\.ly|tinyurl|gg/[a-z0-9]{6,}|\.ru/|\.xyz/|\.tk/)", re.IGNORECASE)
_message_timestamps = defaultdict(deque)

def _is_spam(user_id):
    now = datetime.datetime.utcnow().timestamp()
    dq = _message_timestamps[user_id]; dq.append(now)
    while dq and dq[0] < now - config.SPAM_WINDOW_SECONDS: dq.popleft()
    return len(dq) >= config.SPAM_MESSAGE_COUNT

async def handle_message(bot, message):
    if message.author.bot or db.is_spam_exempt(message.author.id): return
    if _PHISHING_PATTERNS.search(message.content):
        await _handle_phishing(bot, message); return
    if _is_spam(message.author.id):
        await _handle_spam(bot, message); return
    if len(message.content) > 15:
        result = await llm.classify_violation(message.content)
        if result.get("violation"):
            await _handle_violation(bot, message, result)

async def _handle_phishing(bot, message):
    try: await message.delete()
    except: pass
    m, g = message.author, message.guild
    total = db.add_warning(g.id, m.id, "AutoMod", "Phishing link")
    db.log_action(g.id, "DELETE+WARN(phishing)", m.id, "AutoMod", "Phishing link")
    try:
        r = await llm.generate("Tell this member their message was removed for a suspected phishing link. Warn that further violations mean a ban.")
        await m.send(r)
    except: pass
    if total >= config.WARN_BEFORE_BAN or _is_obvious_bot(m): await _ban(bot, g, m, "Phishing")
    else: await _post_log(bot, g, "PHISHING DETECTED", m, "Phishing link", total)

async def _handle_spam(bot, message):
    try: await message.delete()
    except: pass
    m, g = message.author, message.guild
    total = db.add_warning(g.id, m.id, "AutoMod", "Spam")
    db.log_action(g.id, "WARN(spam)", m.id, "AutoMod", "Spam")
    if total >= config.WARN_BEFORE_BAN: await _ban(bot, g, m, "Repeated spam")
    elif total >= config.WARN_BEFORE_MUTE: await _mute(bot, g, m, "Spam")
    else:
        t = await llm.generate(f"Tell this member they are sending messages too fast. Warning {total} of {config.WARN_BEFORE_MUTE}. Brief and calm.")
        try: await message.channel.send(f"{m.mention} {t}", delete_after=15)
        except: pass
        await _post_log(bot, g, "SPAM WARNING", m, "Spam", total)

async def _handle_violation(bot, message, result):
    m, g = message.author, message.guild
    cat, sev = result.get("category","violation"), result.get("severity",1)
    reason = f"Detected: {cat}"
    if sev >= 3:
        try: await message.delete()
        except: pass
        db.add_warning(g.id, m.id, "AutoMod", reason); db.log_action(g.id, "BAN(severe)", m.id, "AutoMod", reason)
        await _ban(bot, g, m, reason); return
    if sev == 2:
        try: await message.delete()
        except: pass
        db.add_warning(g.id, m.id, "AutoMod", reason); db.log_action(g.id, "MUTE(violation)", m.id, "AutoMod", reason)
        await _mute(bot, g, m, reason); return
    total = db.add_warning(g.id, m.id, "AutoMod", reason)
    db.log_action(g.id, "WARN(violation)", m.id, "AutoMod", reason)
    t = await llm.generate(f"Warn this member: {cat}. Warning {total}. Direct and calm.")
    try: await message.channel.send(f"{m.mention} {t}", delete_after=20)
    except: pass
    if total >= config.WARN_BEFORE_BAN: await _ban(bot, g, m, "Exceeded warning limit")
    elif total >= config.WARN_BEFORE_MUTE: await _mute(bot, g, m, reason)
    await _post_log(bot, g, f"WARN ({cat.upper()})", m, reason, total)

async def _mute(bot, guild, member, reason):
    role = guild.get_role(config.MUTED_ROLE_ID) or discord.utils.get(guild.roles, name="Muted")
    if role:
        try: await member.add_roles(role, reason=reason)
        except: pass
        asyncio.create_task(_auto_unmute(member, role, config.MUTE_DURATION_MIN))
    try:
        t = await llm.generate(f"Tell this member they are muted for {config.MUTE_DURATION_MIN} minutes. Reason: {reason}.")
        await member.send(t)
    except: pass
    db.log_action(guild.id, f"MUTE ({config.MUTE_DURATION_MIN}m)", member.id, "AutoMod", reason)
    await _post_log(bot, guild, "MUTE", member, reason)

async def _auto_unmute(member, role, minutes):
    await asyncio.sleep(minutes * 60)
    try: await member.remove_roles(role, reason="Mute expired")
    except: pass

async def _ban(bot, guild, member, reason):
    try:
        t = await llm.generate(f"Tell this member they are banned. Reason: {reason}. Short, factual.")
        await member.send(t)
    except: pass
    try: await guild.ban(member, reason=reason, delete_message_days=1)
    except: pass
    db.log_action(guild.id, "BAN", member.id, "AutoMod", reason)
    await _post_log(bot, guild, "BAN", member, reason)

def _is_obvious_bot(member):
    age = (datetime.datetime.utcnow() - member.created_at.replace(tzinfo=None)).total_seconds() / 3600
    nr = sum(c.isdigit() for c in member.name) / max(len(member.name), 1)
    return age < 24 and nr > 0.4 and member.default_avatar

async def _post_log(bot, guild, action, member, reason, wt=0):
    ch = guild.get_channel(config.LOG_CHANNEL_ID)
    if not ch: return
    cm = {"BAN": discord.Color.red(), "MUTE": discord.Color.orange(), "PHISHING": discord.Color.dark_red()}
    color = next((v for k,v in cm.items() if k in action), discord.Color.yellow())
    e = discord.Embed(title=f"Mod Action: {action}", color=color, timestamp=datetime.datetime.utcnow())
    e.add_field(name="User", value=f"{member} ({member.id})", inline=True)
    e.add_field(name="Reason", value=reason, inline=True)
    if wt: e.add_field(name="Total Warnings", value=str(wt), inline=True)
    e.set_footer(text="AutoMod")
    try: await ch.send(embed=e)
    except: pass
