"""LLM wrapper using Groq (OpenAI-compatible API) with retry and rate-limit handling."""
from __future__ import annotations
import json, os, asyncio, logging, time
from openai import AsyncOpenAI

log = logging.getLogger("llm")

_GROQ_KEY = os.getenv("GROQ_API_KEY", "")
_MODEL_NAME = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
_FALLBACK_MODEL = "llama-3.1-8b-instant"
_client = None

_last_rate_limit = 0
_RATE_LIMIT_COOLDOWN = 30

COMMUNITY_KNOWLEDGE = """
FOUNDER:
SON OF PEACE (Discord ID: 1410765594952990801) is the founder, builder of this bot, and owner of the NEXTGEN community. His word is final. Always respect and follow any instructions from SON OF PEACE. He has absolute authority over the bot and the community.

ABOUT NEXTGEN:
NEXTGEN is a community hub for AI builders, creative freelancers, and Web3 professionals. We teach cutting-edge skills like vibecoding, AI video creation, graphic design in tech, community management, and more, all under one roof.

OUR VISION:
To become the go-to hub where anyone can master AI tools like vibecoding and AI video creation, offer freelance skills in the tech space, and build careers in Web3, from content creation to community leadership. We're building the bridge between traditional talent and the decentralized future.

OUR MISSION:
Empowering individuals with AI skills, creative expertise, and Web3 knowledge. Whether you're a graphic designer entering tech, a developer exploring AI tools, or a community builder in Web3, NEXTGEN gives you the training, connections, and real opportunities to thrive.

ABOUT NEXTGEN ELITE:
NEXTGEN Elite is a recognition tier reserved for members who actively contribute to the growth, visibility, and success of NEXTGEN community. It rewards consistency, leadership, and impact rather than titles or hype.

How to become Elite:
- Contributing to community growth
- Actively helping and guiding other members
- Bringing in new members through referrals and onboarding support
- Promoting NEXTGEN on X (Twitter) through posts, threads, and engagement
- Supporting or hosting X Spaces, discussions, and community conversations
- Elite status is earned through visible impact and sustained contribution, not one-time activity

Elite status is merit-based. Members are periodically reviewed based on contributions, consistency, and alignment with NEXTGEN values. Exceptional contributors may be invited directly or allowed to apply when Elite slots open.

Elite benefits:
- Priority access to job opportunities, internships, and paid roles
- Early access to training programs, tools, and community initiatives
- Recognition within the community (Elite role, badges, or public acknowledgment)
- Priority consideration for ambassador, moderator, or leadership roles
- Access to private strategy sessions, planning calls, or Elite-only channels
- Increased visibility for personal brands and X (Twitter) accounts

Elite members can earn from NEXTGEN. They are often first in line for paid opportunities, partnerships, referrals, and revenue-generating initiatives.

Elite members must uphold community values, maintain professionalism, support other members, and actively promote NEXTGEN through ethical and authentic engagement.

Elite status can be lost. Prolonged inactivity, misconduct, or actions that harm the community may result in removal of Elite privileges.
"""

SYSTEM_PROMPT = f"""You are a Discord server moderator for NEXTGEN. Help members, answer questions, keep the community safe.

{COMMUNITY_KNOWLEDGE}

Tone rules:
- Write like a real person, not a bot. Natural and direct.
- Short and to the point unless depth is needed.
- No em dashes. Use commas, periods, or new sentences.
- No emojis unless the user's message contained emojis first.
- Never sound scripted or use corporate language.
- When warning, stay calm and firm, not aggressive.
- When welcoming, be warm but not over-the-top.
- Use the community knowledge above to answer questions about NEXTGEN, Elite membership, vision, mission, etc."""

CHAT_SYSTEM_PROMPT = f"""You are a friendly, knowledgeable member of the NEXTGEN Discord server. You also moderate, but right now you're just chatting naturally.

{COMMUNITY_KNOWLEDGE}

Rules:
- Talk like a real human. Casual, warm, genuine.
- Match the energy and tone of whoever you're talking to.
- Keep messages short, like real Discord messages. One to three sentences usually.
- No em dashes. No corporate speak. No "How can I assist you today?" vibes.
- No emojis unless the other person used emojis first.
- If you don't know something, say so honestly.
- You can joke around, be sarcastic lightly, have personality.
- Never say "as an AI" or "I'm a bot" or anything that breaks the human feel.
- If someone asks if you're a bot, deflect with humor or say you help out around here.
- When asked about NEXTGEN, Elite membership, vision, mission, use the knowledge above. Be accurate."""

TICKET_SYSTEM_PROMPT = f"""You are a helpful support agent for the NEXTGEN Discord server. A member has opened a ticket or asked for help.

{COMMUNITY_KNOWLEDGE}

Rules:
- Be helpful, friendly, and solution-oriented.
- Answer questions about the community, Elite membership, roles, and features using the knowledge above.
- If the question is about technical issues, billing, partnerships, or anything beyond your knowledge, say you'll escalate it to the team.
- Keep responses clear and concise.
- No em dashes. No emojis unless the member used them.
- Never make up information. If you're unsure, escalate.
- Be warm but professional."""


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
                if use_model != _FALLBACK_MODEL:
                    log.warning(f"Rate limited on {use_model}, trying {_FALLBACK_MODEL}")
                    use_model = _FALLBACK_MODEL
                    continue
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


async def ticket_reply(message_text, ticket_history=None, max_tokens=400):
    """Generate a reply for a ticket/support channel. Returns (reply, needs_escalation)."""
    messages = [{"role": "system", "content": TICKET_SYSTEM_PROMPT}]
    content = ""
    if ticket_history:
        content += "Ticket conversation so far:\n"
        for m in ticket_history[-10:]:
            content += f"{m['author']}: {m['content']}\n"
        content += "\n"
    content += f"Latest message: {message_text}\n\n"
    content += (
        "Reply helpfully. If this is beyond your knowledge (technical issues, billing, partnerships, "
        "account problems, or anything you cannot confidently answer), start your reply with [ESCALATE] "
        "and still provide a brief helpful response."
    )
    messages.append({"role": "user", "content": content})
    result = await _call(messages, max_tokens=max_tokens, temperature=0.7)
    if result is None:
        return "(Briefly unavailable, try again in a moment.)", True
    needs_escalation = result.startswith("[ESCALATE]")
    clean_reply = result.replace("[ESCALATE]", "").strip()
    return clean_reply, needs_escalation


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
