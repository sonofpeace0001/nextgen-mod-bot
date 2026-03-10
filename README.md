# NEXTGEN MOD - Discord Moderation Agent

Autonomous Discord moderation bot with AI-powered chat, auto-mod, ban appeals, reaction roles, and full slash command suite.

## Features
- Conversational chat (responds when @mentioned, picks up unanswered messages after 30s)
- Auto-moderation (spam, phishing, LLM-based violation detection)
- Escalation: Warnings -> Mute -> Ban
- Ban appeals via DM
- Member reports with mod action buttons
- Reaction roles
- Mod notes on user profiles
- Slash commands: /warn /mute /unmute /ban /tempban /purge /warnings /clearwarnings /modlog /slowmode /lookup /note /reactionrole /report /guide

## Deploy on Railway
1. Fork or connect this repo on railway.app
2. Add variables: DISCORD_BOT_TOKEN, GEMINI_API_KEY, GUILD_ID, LOG_CHANNEL_ID, WELCOME_CHANNEL_ID
3. Deploy. Done.

## Environment Variables
| Variable | Required | Default |
|----------|----------|---------|
| DISCORD_BOT_TOKEN | Yes | - |
| GEMINI_API_KEY | Yes | - |
| GUILD_ID | Yes | - |
| LOG_CHANNEL_ID | Yes | - |
| WELCOME_CHANNEL_ID | No | 0 |
| CHAT_ENABLED | No | true |
| CHAT_REPLY_DELAY | No | 30 |
| WARN_BEFORE_MUTE | No | 3 |
| WARN_BEFORE_BAN | No | 5 |
