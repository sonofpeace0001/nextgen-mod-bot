"""Welcome new members and guide them around the server."""
from __future__ import annotations
import discord, config, llm

CHANNEL_GUIDE = """Server channel overview:
- #rules: Community rules. Read before anything else.
- #announcements: Official server news.
- #introductions: Tell us who you are.
- #general: Main chat.
- #help: Ask questions.
- #resources: Guides and links.
- #off-topic: Anything else."""

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
