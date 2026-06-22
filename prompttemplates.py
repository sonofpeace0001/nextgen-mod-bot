"""Prompt-drafting data (Task 4, upgraded) -- data only.

Two maps per output category:
  INTAKE    -> the few questions the bot asks before drafting.
  BLUEPRINT -> the section-by-section structure a senior prompt engineer follows when
               drafting the final, ready-to-paste prompt. The LLM expands these into a
               full premium prompt using the member's answers (see llm.draft_prompt).

Keep this data-only so it's easy to edit. Categories match curriculum.detect_category.
"""
from __future__ import annotations

INTAKE = {
    "graphic design": (
        "nice. a few quick things and i'll draft the prompt:\n"
        "- what's the project or brand name?\n"
        "- what's the message or tweet it's promoting?\n"
        "- brand colours or a logo? (drop the colours, or say monochrome)\n"
        "- the vibe: premium, minimal, bold, or playful?\n"
        "- which platform/size? (x post 1600x900, ig post 1080x1080, story 1080x1920, yt thumb 1280x720)"
    ),
    "image generation": (
        "cool. a few quick things and i'll draft the prompt:\n"
        "- what should the image show?\n"
        "- what's the mood or style?\n"
        "- any brand colours, or a subject/logo to centre it on?\n"
        "- where's it going and what size? (x 1600x900, ig 1080x1080, story 1080x1920)"
    ),
    "tweet": (
        "got it. quick questions:\n"
        "- what's the tweet about?\n"
        "- who's it for?\n"
        "- what's the goal: clicks, replies, or awareness?\n"
        "- what voice: yours, punchy, or calm?"
    ),
    "thread": (
        "got it. quick questions:\n"
        "- what's the thread about?\n"
        "- who's it for?\n"
        "- what's the one big takeaway?\n"
        "- roughly how many points (5-9)?"
    ),
    "video": (
        "nice. quick questions:\n"
        "- what's the video about?\n"
        "- which platform and length? (e.g. a 30s reel)\n"
        "- what mood or style?\n"
        "- any brand colours or a logo?"
    ),
    "landing page": (
        "on it. quick questions:\n"
        "- what's the page selling or signing people up for?\n"
        "- who's it for?\n"
        "- what's the one action you want them to take?\n"
        "- brand colours and tone?"
    ),
    "website": (
        "on it. quick questions:\n"
        "- what's the site for?\n"
        "- which main pages do you need?\n"
        "- who's it for?\n"
        "- brand colours and tone?"
    ),
    "app": (
        "let's do it. quick questions:\n"
        "- what should the app do, in one line?\n"
        "- who's it for?\n"
        "- the 3 must-have features?\n"
        "- any brand or style preference?"
    ),
}

_DEFAULT_INTAKE = (
    "happy to draft that. tell me:\n"
    "- what exactly are you trying to make?\n"
    "- who's it for?\n"
    "- any style, brand, or details i should bake in?"
)

# ── Blueprints ────────────────────────────────────────────────────

_GRAPHIC_BLUEPRINT = (
    "Role: world-class brand designer and art director. Reference style: Apple keynote, "
    "OpenAI launch visuals, Linear, Arc, Stripe Sessions, Notion AI. The goal is a graphic "
    "that looks like it came from a billion-dollar technology company.\n"
    "Write a complete, paste-ready image-generation prompt with these labelled sections:\n"
    "1. INPUTS: logo (note 'upload logo'), project name, the message/tweet.\n"
    "2. BRAND ANALYSIS: derive Primary, Accent and Background colours from the logo; if the "
    "logo is monochrome, Primary=#FFFFFF, Accent derived from the topic, Background=#050505. "
    "Never introduce unrelated colours.\n"
    "3. COPY GENERATION: a 4-7 word HEADLINE; exactly ONE gradient word; a SUBHEADING (max 14 "
    "words); a short CTA. Give 2-3 examples for each.\n"
    "4. HERO OBJECT: derived from the topic (e.g. AI agent -> floating chrome speech bubble, "
    "analytics -> glass dashboard shard), in premium materials only: polished chrome, brushed "
    "titanium, frosted glass, machined aluminium. No plastic, mascots, or cartoons.\n"
    "5. LAYOUT: canvas + aspect ratio for the chosen platform; logo top-left (no glow/shadow/"
    "container); headline + subheading + CTA pill on the left; hero object centre-right; 2-3 "
    "glassmorphic dashboard panels far right with abstract UI and no readable text.\n"
    "6. BACKGROUND: cinematic and premium - deep gradient, volumetric spotlight, subtle "
    "particles, depth haze, realistic reflections, soft light trails.\n"
    "7. VISUAL QUALITY: 8K, photorealistic, global illumination, ray-traced reflections, crisp "
    "typography, luxury software aesthetic.\n"
    "8. TEXT RULES: only the logo, headline, gradient word, subheading and CTA appear - nothing "
    "else.\n"
    "9. NEGATIVE PROMPT: no cyberpunk overload, no stock imagery, no rainbow gradients, no text "
    "shadows, no logo glow, no clutter, no mascots, no emojis, no plastic, no duplicated elements.\n"
    "10. FINAL STEPS: analyse the logo, analyse the message, derive the strongest hook, pick the "
    "most relevant hero object, then compose like a top-tier product launch."
)

BLUEPRINT = {
    "graphic design": _GRAPHIC_BLUEPRINT,
    "image generation": _GRAPHIC_BLUEPRINT,
    "tweet": (
        "Role: senior ghostwriter for founders. Write a paste-ready prompt that instructs an AI "
        "to write one high-performing X post. Sections:\n"
        "1. CONTEXT: topic, audience, the angle or insight, the writer's voice.\n"
        "2. GOAL: what the reader should feel or do.\n"
        "3. STRUCTURE: a scroll-stopping hook line, 1-3 tight body lines, optional one-line CTA.\n"
        "4. CONSTRAINTS: sound human, match the voice, under 280 chars unless asked, no hashtags "
        "or emojis unless asked.\n"
        "5. OUTPUT: give 3 distinct variations."
    ),
    "thread": (
        "Role: senior thread writer. Write a paste-ready prompt for an AI to write an X thread. "
        "Sections:\n"
        "1. CONTEXT: topic, audience, the big idea, voice.\n"
        "2. STRUCTURE: a hook tweet that promises a clear payoff, then 5-9 numbered tweets each "
        "making one self-contained point with a concrete example, then a final tweet that recaps "
        "and gives a soft CTA.\n"
        "3. CONSTRAINTS: each tweet under 280 chars, plain language, no fluff, no hashtags unless "
        "asked.\n"
        "4. OUTPUT: the full numbered thread."
    ),
    "video": (
        "Role: creative director for short-form video. Write a paste-ready prompt for an AI video "
        "tool. Sections:\n"
        "1. CONCEPT: topic, core message, mood.\n"
        "2. SPEC: platform + aspect ratio, target length.\n"
        "3. SCRIPT/SHOTLIST: a hook in the first 2 seconds, then scene-by-scene beats with on-"
        "screen action and a voiceover/caption line per beat, ending on a payoff or CTA.\n"
        "4. STYLE: reference look, pacing, colour, music feel.\n"
        "5. CONSTRAINTS: captions on, brand colours, no watermarks."
    ),
    "landing page": (
        "Role: conversion copywriter and product designer. Write a paste-ready prompt for an AI "
        "builder to produce a landing page. Sections:\n"
        "1. CONTEXT: what it sells or signs people up for, audience, the one promise, brand "
        "colours and tone.\n"
        "2. SECTIONS: hero (headline + subhead + primary CTA), problem, solution, key features/"
        "benefits, social-proof placeholder, pricing or offer, FAQ, final CTA.\n"
        "3. STYLE: layout, typography vibe, responsive.\n"
        "4. OUTPUT: clean structured copy plus short layout notes per section."
    ),
    "website": (
        "Role: conversion copywriter and product designer. Write a paste-ready prompt for an AI "
        "builder to produce a multi-page website. Sections:\n"
        "1. CONTEXT: what the site is for, audience, brand colours and tone.\n"
        "2. PAGES: list each page (home, about, etc.) and, per page, its goal and the sections it "
        "needs.\n"
        "3. STYLE: layout, typography vibe, responsive, consistent nav and footer.\n"
        "4. OUTPUT: structured copy plus layout notes per page."
    ),
    "app": (
        "Role: product engineer and no-code architect. Write a paste-ready prompt for an AI "
        "builder/agent to create the app. Sections:\n"
        "1. WHAT IT DOES: the core job in one line.\n"
        "2. USERS: who it's for.\n"
        "3. CORE FEATURES: the must-have actions.\n"
        "4. SCREENS/FLOWS: the main screens and what each does.\n"
        "5. DATA: what it stores.\n"
        "6. STYLE: brand and look.\n"
        "7. OUTPUT: build it as a reusable agent/app and list any inputs the user must provide."
    ),
}

_DEFAULT_BLUEPRINT = (
    "Role: senior prompt engineer. Write a clear, premium, ready-to-paste prompt for the "
    "member's requested output, with labelled sections: ROLE, CONTEXT/INPUTS, REQUIREMENTS, "
    "CONSTRAINTS, and OUTPUT FORMAT. Fill every section from the member's answers and pick "
    "sensible professional defaults for anything missing."
)


def intake(category: str | None) -> str:
    return INTAKE.get((category or "").lower(), _DEFAULT_INTAKE)


def blueprint(category: str | None) -> str:
    return BLUEPRINT.get((category or "").lower(), _DEFAULT_BLUEPRINT)
