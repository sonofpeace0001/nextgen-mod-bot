"""Ticket detection and auto-reply with escalation support."""
from __future__ import annotations
import logging, time
from collections import defaultdict
import discord, config, llm

log = logging.getLogger("tickets")

_ticket_cooldowns = defaultdict(float)
_TICKET_COOLDOWN = 10  # seconds between bot replies in a ticket
_escalated_channels = set()  # track channels already escalated to avoid spam


def is_ticket_channel(channel) -> bool:
    """Check if a channel is a ticket/support channel by name."""
    if not hasattr(channel, "name"):
        return False
    name = channel.name.lower()
    for kw in config.TICKET_KEYWORDS:
        if kw.strip().lower() in name:
            return True
    return False


async def handle_ticket_message(bot, message):
    """Handle a message in a ticket channel. Returns True if handled."""
    if not is_ticket_channel(message.channel):
        return False

    if message.author.bot:
        return False

    # Cooldown check
    now = time.time()
    if now - _ticket_cooldowns[message.channel.id] < _TICKET_COOLDOWN:
        return False

    # Skip very short messages
    if len(message.content.strip()) < 5:
        return False

    log.info(f"Ticket message from {message.author} in #{message.channel.name}: {message.content[:100]}")

    # Get ticket conversation history
    history = await _get_ticket_history(message.channel, before=message, limit=15)
    history.append({"author": message.author.display_name, "content": message.content})

    # Generate reply with retries
    async with message.channel.typing():
        reply, needs_escalation = await llm.ticket_reply(
            message.content, ticket_history=history
        )

    # If LLM failed completely, do NOT escalate. Just acknowledge and wait.
    if not reply or reply == "(Briefly unavailable, try again in a moment.)":
        try:
            await message.reply(
                "Got your message. Let me look into this and get back to you shortly.",
                mention_author=False
            )
            _ticket_cooldowns[message.channel.id] = time.time()
        except Exception as e:
            log.error(f"Failed to send ticket acknowledgment: {e}")
        return True

    # Send the helpful reply first, always
    try:
        await message.reply(reply, mention_author=False)
        _ticket_cooldowns[message.channel.id] = time.time()
    except Exception as e:
        log.error(f"Failed to send ticket reply: {e}")
        return False

    # Only escalate if LLM explicitly said to, and we haven't already escalated this channel
    if needs_escalation and config.ESCALATION_ROLE_ID and message.channel.id not in _escalated_channels:
        try:
            await message.channel.send(
                f"I've done what I can on this one. Tagging <@&{config.ESCALATION_ROLE_ID}> "
                f"to take a closer look and help you further."
            )
            _escalated_channels.add(message.channel.id)
            log.info(f"Escalated ticket in #{message.channel.name} to role {config.ESCALATION_ROLE_ID}")
        except Exception as e:
            log.error(f"Failed to escalate ticket: {e}")

    return True


async def _get_ticket_history(channel, before=None, limit=15):
    """Get recent messages from a ticket channel."""
    msgs = []
    try:
        async for m in channel.history(limit=limit, before=before):
            msgs.append({
                "author": m.author.display_name,
                "content": m.content[:500] or "(embed/attachment)"
            })
    except:
        pass
    msgs.reverse()
    return msgs
