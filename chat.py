"""Conversational chat: @mention replies + delayed unanswered message pickup."""
from __future__ import annotations
import asyncio, logging, time, re
from collections import defaultdict
import discord, config, llm

log = logging.getLogger("chat")
_pending_replies = {}
_channel_cooldowns = defaultdict(float)
_COOLDOWN_SECONDS = 45
_NEEDS_REPLY = re.compile(
    r"(\?|anyone|somebody|help|how do|how can|what is|where do|where can|"
    r"can someone|does anyone|hey|hello|hi |yo |sup|good morning|good night|"
    r"thanks|thank you|welcome|new here|just joined)", re.IGNORECASE)

async def handle_mention(bot, message):
    if not config.CHAT_ENABLED: return
    recent = await _get_history(message.channel, before=message, limit=10)
    recent.append({"author": message.author.display_name, "content": message.content})
    clean = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip() or "hey"
    reply = await llm.chat_reply(clean, _channel_ctx(message), recent)
    if reply:
        try:
            await message.reply(reply, mention_author=False)
            _channel_cooldowns[message.channel.id] = time.time()
        except: pass

async def schedule_delayed_reply(bot, message):
    if not config.CHAT_ENABLED: return
    if time.time() - _channel_cooldowns[message.channel.id] < _COOLDOWN_SECONDS: return
    if not _NEEDS_REPLY.search(message.content): return
    if len(message.content.strip()) < 8: return
    task = asyncio.create_task(_delayed(bot, message))
    _pending_replies[message.id] = task

async def cancel_for_channel(channel_id):
    to_rm = [mid for mid, t in _pending_replies.items() if not t.done()]
    for mid in to_rm:
        t = _pending_replies.pop(mid, None)
        if t and not t.done(): t.cancel()

async def _delayed(bot, message):
    try: await asyncio.sleep(config.CHAT_REPLY_DELAY)
    except asyncio.CancelledError: return
    _pending_replies.pop(message.id, None)
    if time.time() - _channel_cooldowns[message.channel.id] < _COOLDOWN_SECONDS: return
    try:
        async for msg in message.channel.history(after=message, limit=5):
            if not msg.author.bot: return
    except: return
    recent = await _get_history(message.channel, before=message, limit=8)
    recent.append({"author": message.author.display_name, "content": message.content})
    reply = await llm.chat_reply(message.content, _channel_ctx(message), recent)
    if reply:
        try:
            await message.reply(reply, mention_author=False)
            _channel_cooldowns[message.channel.id] = time.time()
        except: pass

async def _get_history(channel, before=None, limit=10):
    msgs = []
    try:
        async for m in channel.history(limit=limit, before=before):
            msgs.append({"author": m.author.display_name, "content": m.content[:300] or "(embed)"})
    except: pass
    msgs.reverse()
    return msgs

def _channel_ctx(message):
    parts = [f"Channel: #{message.channel.name}"]
    if hasattr(message.channel, "topic") and message.channel.topic:
        parts.append(f"Topic: {message.channel.topic}")
    parts.append(f"Server: {message.guild.name}")
    return "\n".join(parts)
