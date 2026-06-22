"""Welcome new members and guide them around the server."""
from __future__ import annotations
import logging
import discord, config, llm, database as db, leveling

log = logging.getLogger("welcome")

WELCOME_DM = (
    "hey, welcome to NEXTGEN.\n"
    "quick one so i point you to the right place: with AI tools right now, are you a "
    "total beginner, dabbled a bit, or pretty comfortable? just reply with one. "
    "whatever you pick, you're in the right room. i read every reply."
)

CHANNEL_GUIDE = """Server channel overview:
- #rules: Community rules. Read before anything else.
- #announcements: Official server news.
- #introductions: Tell us who you are.
- #general: Main chat.
- #help: Ask questions.
- #resources: Guides and links.
- #off-topic: Anything else."""

async def send_welcome_dm(bot, member) -> bool:
    """DM the new member one onboarding question. Returns True if the DM was sent.

    On success the member is recorded in welcomed_users (awaiting_reply=1) so their next
    DM is routed to staff, not the appeal handler. Returns False if DMs are closed, so the
    caller can fall back to the public greet."""
    try:
        await member.send(WELCOME_DM)
    except Exception as e:
        log.info(f"Welcome DM to {member} failed ({e}); falling back to public greet.")
        return False
    try:
        db.add_welcomed_user(member.id, member.guild.id)
    except Exception as e:
        log.error(f"Failed to record welcomed_user {member.id}: {e}")
    return True


async def handle_welcome_reply(bot, message) -> bool:
    """If this DM is a reply to the welcome question (and the author isn't banned),
    forward it to the staff channel, set their level, and acknowledge. Returns True if
    handled so the DM is NOT treated as an appeal. Otherwise returns False."""
    row = db.get_welcomed_user(message.author.id)
    if not row or row.get("awaiting_reply") != 1:
        return False

    guild = bot.get_guild(config.GUILD_ID)
    # Banned users can't be mid-welcome; if somehow banned, let appeals handle the DM.
    if guild:
        try:
            await guild.fetch_ban(discord.Object(id=message.author.id))
            return False  # currently banned -> fall through to appeals
        except discord.NotFound:
            pass  # not banned, good
        except Exception:
            pass  # ban lookup failed; proceed as a welcome reply

    # Set their starting level from the answer (beginner/dabbled/comfortable).
    try:
        leveling.set_level(message.author.id, leveling.level_from_answer(message.content))
    except Exception as e:
        log.error(f"Failed to set level from welcome reply: {e}")

    # Forward to staff. Prefer STAFF_CHANNEL_ID; fall back to the log channel so an
    # intro reply is never silently dropped (the bot promises "i read every reply").
    target_id = config.STAFF_CHANNEL_ID or config.LOG_CHANNEL_ID
    if guild and target_id:
        ch = guild.get_channel(target_id)
        if ch:
            e = discord.Embed(title="New member intro reply", color=discord.Color.blurple())
            e.add_field(name="Member", value=f"{message.author} ({message.author.id})", inline=False)
            e.add_field(name="Reply", value=(message.content[:1000] or "(empty)"), inline=False)
            try:
                await ch.send(embed=e)
            except Exception as ex:
                log.error(f"Failed to forward welcome reply: {ex}")
        else:
            log.warning("Welcome-reply target channel (STAFF/LOG) not found.")
    else:
        log.warning("No STAFF_CHANNEL_ID or LOG_CHANNEL_ID set; welcome reply not forwarded.")

    db.clear_awaiting_reply(message.author.id)
    try:
        await message.reply(
            "got it, thanks for that. you're in the right place. "
            "i'll point you to good next steps, and you can always ask me where to start."
        )
    except Exception:
        pass
    return True


async def greet_member(bot, member):
    ch = member.guild.get_channel(config.WELCOME_CHANNEL_ID)
    if not ch: return
    rules = member.guild.get_channel(config.RULES_CHANNEL_ID)
    rm = f" Read {rules.mention} first." if rules else ""
    prompt = f"Welcome {member.display_name} to the server. Short, warm, not over-the-top. Mention #introductions and #help.{rm} No emojis."
    msg = await llm.generate(prompt, context=CHANNEL_GUIDE, max_tokens=120)
    try: await ch.send(f"{member.mention} {msg}")
    except: pass

async def answer_question(bot, message):
    text = message.content.lower()
    triggers = ["where","how do i","what channel","where do i","can i post","where can i","where should i","what is #","rules","where to","how to","what are the","where do people"]
    if not any(t in text for t in triggers): return False
    if not (bot.user.mentioned_in(message) or message.channel.id == config.CHANNEL_MAP.get("help", 0)): return False
    ctx = CHANNEL_GUIDE
    for name, cid in config.CHANNEL_MAP.items():
        c = message.guild.get_channel(cid)
        if c: ctx = ctx.replace(f"#{name}", c.mention)
    reply = await llm.generate(f"A member asked: {message.content}\n\nAnswer helpfully, point to the right channel. Concise.", context=ctx, max_tokens=200)
    try: await message.reply(reply, mention_author=False)
    except: pass
    return True
