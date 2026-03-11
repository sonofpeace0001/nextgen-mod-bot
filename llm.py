"""LLM wrapper using Groq (OpenAI-compatible API) with retry and rate-limit handling."""
from __future__ import annotations
import json, os, asyncio, logging, time
from openai import AsyncOpenAI

log = logging.getLogger("llm")

_GROQ_KEY = os.getenv("GROQ_API_KEY", "")
_MODEL_NAME = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
_FALLBACK_MODEL = "llama-3.1-8b-instant"
_client = None

# Simple rate limit tracking
_last_rate_limit = 0
_RATE_LIMIT_COOLDOWN = 30  # seconds to wait after a rate limit hit

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


async def _call(messages, max_tokens=300, temperature=0.7, model=None):
    """Core LLM call with retry, fallback model, and rate-limit cooldown."""
    global _last_rate_limit
    client = _get_client()
    if client is None:
        return None

    # If we recently hit a rate limit, wait before trying
    elapsed = time.time() - _last_rate_limit
    if elapsed < _RATE_LIMIT_COOLDOWN:
        await asyncio.sleep(_RATE_LIMIT_COOLDOWN - elapsed)

    use_model = model or _MODEL_NAME
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=use_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err = str(e).lower()
            if "rate_limit" in err or "429" in err:
                _last_rate_limit = time.time()
                # Try fallback model on first rate limit
                if use_model != _FALLBACK_MODEL:
                    log.warning(f"Rate limited on {use_model}, trying {_FALLBACK_MODEL}")
                    use_model = _FALLBACK_MODEL
                    continue
                # Wait and retry
                wait = min(5 * (attempt + 1), 15)
                log.warning(f"Rate limited, waiting {wait}s (attempt {attempt + 1}/3)")
                await asyncio.sleep(wait)
            else:
                log.error(f"Groq error: {e}")
                return None
    log.error("Groq: all retries exhausted")
    return None


async def generate(user_message, context="", max_tokens=300):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    content = ""
    if context:
        content += f"Context:\n{context}\n\n"
    content += user_message
    messages.append({"role": "user", "content": content})
    result = await _call(messages, max_tokens=max_tokens, temperature=0.7)
    if result is None:
        return "(Briefly unavailable, try again in a moment.)"
    return result


async def chat_reply(message_text, channel_context="", recent_messages=None, max_tokens=200):
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
    result = await _call(messages, max_tokens=max_tokens, temperature=0.85)
    return result or ""


async def classify_violation(text):
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
    result = await _call(messages, max_tokens=60, temperature=0)
    if result is None:
        return {"violation": False, "category": "none", "severity": 0}
    try:
        raw = result
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"Groq classify parse error: {e}")
        return {"violation": False, "category": "none", "severity": 0}
