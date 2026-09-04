# NEXTGEN MOD - Discord Moderation Agent

Autonomous Discord moderation bot with AI-powered chat (Groq/GPT-OSS 120B), auto-mod, ban appeals, reaction roles, and full slash command suite. NEXTGEN is an AI-only learning community that takes total beginners to their first real wins with AI and guides them upward over time.

## Features
- Conversational chat (responds when @mentioned, picks up unanswered messages after 30s)
- Auto-moderation (spam, phishing, LLM-based violation detection)
- Escalation: Warnings -> Mute -> Ban
- Ban appeals via DM
- Member reports with mod action buttons
- Reaction roles
- Mod notes on user profiles
- Slash commands: /warn /mute /unmute /ban /tempban /purge /warnings /clearwarnings /modlog /slowmode /lookup /note /reactionrole /report /guide /announce
- **/announce**: post an announcement as a Discord embed card (mod-only); defaults to the announcements channel, optional @everyone ping
- **Tag-to-teach**: @mention the bot anywhere and it answers as a senior prompt engineer / AI mentor — questions, explanations, and ready-to-paste prompts
- **Welcome DM**: new members get one onboarding question; replies are forwarded to a private staff channel (falls back to the public greet if DMs are closed)
- **Daily prompt**: one community prompt posted to a public channel each day at a fixed local time, rotating through a 14-prompt bank (state persisted across restarts)
- **Tutor**: the structured course now lives on the NEXTGEN Academy website (`ACADEMY_LINK`) instead of in the bot; "where do I start" style questions get pointed there, plus a tool rec if the message actually calls for one
- **Prompt helper**: tag the bot for a prompt (graphic, tweet, thread, video, landing page, app, ...) and it acts like a senior prompt engineer — asks a few quick intake questions, then drafts a premium, ready-to-paste prompt; recommends CREAO first (try it first; alternatives only when you come back and ask)
- **CREAO-first recommendations**: every tool rec starts with CREAO, then honest alternatives; agent/automation questions get the CREAO agent-builder link, earning/monetizing questions get the AI-income-use-cases link (both contextual only, never unprompted)
- **Retention (message quota + warn + auto-kick)**: every member without an immune role must send `AUTO_KICK_REQUIRED_MESSAGES` messages (default 10) within a rolling `AUTO_KICK_INACTIVE_DAYS`-day cycle (default 14). At the halfway point (`AUTO_KICK_WARNING_DAYS`, default 7) anyone still short of quota gets pinged once in `RETENTION_WARNING_CHANNEL_ID` showing their progress ("4/10 messages") and days left. When the cycle ends: quota met -> a fresh cycle starts; quota missed -> kicked. Founder and immune roles are always exempt. New members and everyone already in the server get a full grace period from the moment this feature deploys — nobody is judged on messages sent before the bot could count them. DMs the member before kicking (best effort), then kicks and logs.
- **Daily social-media reminder**: one nudge per day in a public channel asking members if they've seen today's NEXTGEN post on socials
- Channel silencing: never replies in announcements; ticket auto-replies are off by default (light moderation still runs)

## Privileged Intents (Developer Portal)
Intents are narrowed to only what the bot uses (Presence is OFF). In the Discord Developer Portal, under your application's **Bot** tab, enable these two **privileged** intents or the bot will fail to start:
- **Server Members Intent** (`members`)
- **Message Content Intent** (`message_content`)

The bot also uses the non-privileged `guilds`, `guild_messages`, `dm_messages` (ban appeals + welcome-DM replies), and `reactions` (reaction roles) intents. Presence intent is intentionally not requested.

Retention (auto-kick) needs the bot's server role to have the **Kick Members** permission. This is a normal guild permission granted when you invite the bot / assign its role — not a Developer Portal intent toggle.

## Deploy on Railway
1. Fork or connect this repo on railway.app
2. Add variables: DISCORD_BOT_TOKEN, GROQ_API_KEY, GUILD_ID, LOG_CHANNEL_ID, WELCOME_CHANNEL_ID
3. Set service type to **Worker** (not Web) in Settings
4. Deploy. Done.

## Get a Free Groq API Key
1. Go to console.groq.com and sign up (free, no credit card)
2. Create an API key from the sidebar
3. Uses GPT-OSS 120B by default (configurable via LLM_MODEL env var). Groq periodically deprecates older models (this bot's original default, Llama 3.3 70B, was pulled from the API entirely) -- if chat replies ever go back to just saying "briefly unavailable", check [console.groq.com/docs/deprecations](https://console.groq.com/docs/deprecations) and update LLM_MODEL.

## Environment Variables
| Variable | Required | Default |
|----------|----------|---------|
| DISCORD_BOT_TOKEN | Yes | - |
| GROQ_API_KEY | Yes | - |
| GUILD_ID | Yes | - |
| LOG_CHANNEL_ID | Yes | - |
| WELCOME_CHANNEL_ID | No | 0 |
| STAFF_CHANNEL_ID | No | 0 |
| PROMPT_CHANNEL_ID | No | 0 |
| PROMPT_HOUR | No | 19 |
| PROMPT_TZ | No | Africa/Lagos |
| ANNOUNCEMENT_CHANNEL_ID | No | 0 |
| DISABLE_TICKET_REPLIES | No | true |
| CREAO_LINK | No | https://creao.ai/@Sonofpeace |
| ACADEMY_LINK | No | https://nextgenai-web.vercel.app/ |
| AUTO_KICK_ENABLED | No | true |
| AUTO_KICK_INACTIVE_DAYS | No | 14 |
| AUTO_KICK_REQUIRED_MESSAGES | No | 10 |
| AUTO_KICK_WARNING_DAYS | No | 7 |
| RETENTION_WARNING_CHANNEL_ID | No | 1536365838276235306 |
| RETENTION_CHECK_HOUR | No | 3 |
| SOCIAL_REMINDER_ENABLED | No | true |
| SOCIAL_REMINDER_CHANNEL_ID | No | 0 (falls back to PROMPT_CHANNEL_ID) |
| SOCIAL_REMINDER_HOUR | No | 12 |
| SOCIAL_LINKS | No | https://x.com/G_NEXTGEN |
| CH_HELP | No | 0 |
| LLM_MODEL | No | openai/gpt-oss-120b |
| CHAT_ENABLED | No | true |
| CHAT_REPLY_DELAY | No | 30 |
| WARN_BEFORE_MUTE | No | 5 |
| WARN_BEFORE_BAN | No | 999 |

> Note: `WARN_BEFORE_BAN` defaults to **999**, so automated escalation effectively never auto-bans. This is intentional in the current config; change it only if you want auto-ban to trigger.

> **Note on `AUTO_KICK_ENABLED`:** this is the most consequential toggle in the bot — it removes real members from the server automatically. It defaults to **true** to match the requested behavior, with real safety rails (grace period on join/first-deploy, a warning ping before the kick, DM before kick, immune-role and founder exemptions), but review `AUTO_KICK_INACTIVE_DAYS`/`AUTO_KICK_WARNING_DAYS` and watch the log + warning channels after your first deploy. Set `AUTO_KICK_ENABLED=false` to turn it off entirely, or just leave `RETENTION_WARNING_CHANNEL_ID` unset to skip the warning ping (the kick itself still runs).
