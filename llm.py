"""LLM wrapper using Google Gemini."""
from __future__ import annotations
import json, os, asyncio, logging
import google.generativeai as genai

log = logging.getLogger("llm")
_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
_MODEL_NAME = "gemini-2.0-flash"
_model = None
_chat_model = None

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

def _get_model():
    global _model
    if _model is None:
        if not _GEMINI_KEY: return None
        genai.configure(api_key=_GEMINI_KEY)
        _model = genai.GenerativeModel(_MODEL_NAME, system_instruction=SYSTEM_PROMPT)
    return _model

def _get_chat_model():
    global _chat_model
    if _chat_model is None:
        if not _GEMINI_KEY: return None
        genai.configure(api_key=_GEMINI_KEY)
        _chat_model = genai.GenerativeModel(_MODEL_NAME, system_instruction=CHAT_SYSTEM_PROMPT)
    return _chat_model

async def generate(user_message, context="", max_tokens=300):
    model = _get_model()
    if model is None: return "(LLM not configured.)"
    parts = []
    if context: parts.append(f"Context:\n{context}\n\n")
    parts.append(user_message)
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: model.generate_content(
            "".join(parts), generation_config=genai.GenerationConfig(max_output_tokens=max_tokens, temperature=0.7)))
        return resp.text.strip()
    except Exception as e:
        log.error(f"Gemini error: {e}")
        return "(LLM error)"

async def chat_reply(message_text, channel_context="", recent_messages=None, max_tokens=200):
    model = _get_chat_model()
    if model is None: return ""
    parts = []
    if channel_context: parts.append(f"Server info:\n{channel_context}\n\n")
    if recent_messages:
        parts.append("Recent messages:\n")
        for m in recent_messages[-8:]:
            parts.append(f"{m['author']}: {m['content']}\n")
        parts.append("\nReply naturally to the latest message. Short and human.\n")
    else:
        parts.append(f"Someone said: {message_text}\n\nReply naturally. Short and human.\n")
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: model.generate_content(
            "".join(parts), generation_config=genai.GenerationConfig(max_output_tokens=max_tokens, temperature=0.85)))
        return resp.text.strip()
    except Exception as e:
        log.error(f"Gemini chat error: {e}")
        return ""

async def classify_violation(text):
    model = _get_model()
    if model is None: return {"violation": False, "category": "none", "severity": 0}
    prompt = (
        "Classify this Discord message for rule violations. "
        "Reply ONLY with valid JSON:\n"
        '{"violation": true, "category": "harassment", "severity": 1}\n'
        "Categories: harassment, spam, phishing, hate_speech, nsfw, none.\n"
        "Severity: 1=mild warning, 2=mute-worthy, 3=immediate ban.\n"
        "If no violation: "
        '{"violation": false, "category": "none", "severity": 0}\n\n'
        f"Message: {text}")
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: model.generate_content(
            prompt, generation_config=genai.GenerationConfig(max_output_tokens=60, temperature=0)))
        raw = resp.text.strip()
        if raw.startswith("```"): raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"Gemini classify error: {e}")
        return {"violation": False, "category": "none", "severity": 0}
