"""LLM wrapper using Groq (OpenAI-compatible API)."""
from __future__ import annotations
import json, os, asyncio, logging
from openai import AsyncOpenAI

log = logging.getLogger("llm")

_GROQ_KEY = os.getenv("GROQ_API_KEY", "")
_MODEL_NAME = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
_client = None

SYSTEM_PROMPT = """You are a Discord server moderator. Help members, answer questions, keep the community safe.
Tone rules:
- Write like a real person, not a bot. Natural and direct.
- Short and to the point unless depth is needed.
- No em dashes. Use commas, periods, or new sentences.
- No emojis unless the user's message contained emojis first.
- Never sound scripted or use corporate language.
- When warning, stay calm and firm, not aggressive.
- When welcoming, be warm but not over-the-top."""

CHAT_SYSTEM_PROMPT = """You are a friendly, knowledgeable member of a Discord server. You also moderate, but right now you're just chatting naturally.
Rules:
- Talk like a real human. Casual, warm, genuine.
- Match the energy and tone of whoever you're talking to.
- Keep messages short, like real Discord messages. One to three sentences usually.
- No em dashes. No corporate speak. No "How can I assist you today?" vibes.
- No emojis unless the other person used emojis first.
- If you don't know something, say so honestly.
- You can joke around, be sarcastic lightly, have personality.
- Never say "as an AI" or "I'm a bot" or anything that breaks the human feel.
- If someone asks if you're a bot, deflect with humor or say you help out around here."""


def _get_client():
    global _client
    if _client is None:
        if not _GROQ_KEY:
            return None
        _client = AsyncOpenAI(
            api_key=_GROQ_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


async def generate(user_message, context="", max_tokens=300):
    client = _get_client()
    if client is None:
        return "(LLM not configured. Set GROQ_API_KEY.)"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    content = ""
    if context:
        content += f"Context:\n{context}\n\n"
    content += user_message
    messages.append({"role": "user", "content": content})
    try:
        resp = await client.chat.completions.create(
            model=_MODEL_NAME,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"Groq error: {e}")
        return "(LLM error)"


async def chat_reply(message_text, channel_context="", recent_messages=None, max_tokens=200):
    client = _get_client()
    if client is None:
        return ""
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    content = ""
    if channel_context:
        content += f"Server info:\n{channel_context}\n\n"
    if recent_messages:
        content += "Recent messages:\n"
        for m in recent_messages[-8:]:
            content += f"{m['author']}: {m['content']}\n"
        content += "\nReply naturally to the latest message. Short and human.\n"
    else:
        content += f"Someone said: {message_text}\n\nReply naturally. Short and human.\n"
    messages.append({"role": "user", "content": content})
    try:
        resp = await client.chat.completions.create(
            model=_MODEL_NAME,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.85,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"Groq chat error: {e}")
        return ""


async def classify_violation(text):
    client = _get_client()
    if client is None:
        return {"violation": False, "category": "none", "severity": 0}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            "Classify this Discord message for rule violations. "
            "Reply ONLY with valid JSON:\n"
            '{"violation": true, "category": "harassment", "severity": 1}\n'
            "Categories: harassment, spam, phishing, hate_speech, nsfw, none.\n"
            "Severity: 1=mild warning, 2=mute-worthy, 3=immediate ban.\n"
            "If no violation: "
            '{"violation": false, "category": "none", "severity": 0}\n\n'
            f"Message: {text}"
        )},
    ]
    try:
        resp = await client.chat.completions.create(
            model=_MODEL_NAME,
            messages=messages,
            max_tokens=60,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"Groq classify error: {e}")
        return {"violation": False, "category": "none", "severity": 0}
