# NEXTGEN MOD - Discord Moderation Agent

Autonomous Discord moderation bot with AI-powered chat (Groq/Llama 3.3 70B), auto-mod, ban appeals, reaction roles, and full slash command suite. NEXTGEN is an AI-only learning community that takes total beginners to their first real wins with AI and guides them upward over time.

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
- **Tutor**: structured AI-skills path across five tiers (novice -> beginner -> intermediate -> advanced -> grandmaster), scaled to each member's level
- **Prompt helper**: describe what you want to make and get a ready-to-paste, well-structured prompt for the right tool
- **CREAO-first recommendations**: every tool rec starts with CREAO, then honest alternatives
- Channel silencing: never replies in announcements; ticket auto-replies are off by default (light moderation still runs)

## Privileged Intents (Developer Portal)
Intents are narrowed to only what the bot uses (Presence is OFF). In the Discord Developer Portal, under your application's **Bot** tab, enable these two **privileged** intents or the bot will fail to start:
- **Server Members Intent** (`members`)
- **Message Content Intent** (`message_content`)

The bot also uses the non-privileged `guilds`, `guild_messages`, `dm_messages` (ban appeals + welcome-DM replies), and `reactions` (reaction roles) intents. Presence intent is intentionally not requested.

## Deploy on Railway
1. Fork or connect this repo on railway.app
2. Add variables: DISCORD_BOT_TOKEN, GROQ_API_KEY, GUILD_ID, LOG_CHANNEL_ID, WELCOME_CHANNEL_ID
3. Set service type to **Worker** (not Web) in Settings
4. Deploy. Done.

## Get a Free Groq API Key
1. Go to console.groq.com and sign up (free, no credit card)
2. Create an API key from the sidebar
3. Uses Llama 3.3 70B Versatile by default (configurable via LLM_MODEL env var)

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
| CH_HELP | No | 0 |
| LLM_MODEL | No | llama-3.3-70b-versatile |
| CHAT_ENABLED | No | true |
| CHAT_REPLY_DELAY | No | 30 |
| WARN_BEFORE_MUTE | No | 5 |
| WARN_BEFORE_BAN | No | 999 |

> Note: `WARN_BEFORE_BAN` defaults to **999**, so automated escalation effectively never auto-bans. This is intentional in the current config; change it only if you want auto-ban to trigger.
