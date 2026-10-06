# NEXTGEN MOD - Discord Moderation Agent

Autonomous Discord moderation bot with AI-powered chat (Groq/GPT-OSS 120B), auto-mod, ban appeals, reaction roles, and full slash command suite. NEXTGEN is an AI-only learning community that takes total beginners to their first real wins with AI and guides them upward over time.

## Features
- Persistent Postgres (Supabase) storage — survives redeploys and restarts (see Database below)
- Conversational chat (responds when @mentioned, picks up unanswered messages after 30s)
- Auto-moderation (spam, phishing, LLM-based violation detection)
- Escalation: Warnings -> Mute -> Ban
- Ban appeals via DM
- Member reports with mod action buttons
- Reaction roles
- Mod notes on user profiles
- Slash commands: /warn /mute /unmute /purge /warnings /clearwarnings /modlog /slowmode /lookup /note /reactionrole /report /guide /announce /ignore /unignore /ignoredchannels, plus the points commands: /xp /xpleaderboard /referrals /invitedby /xpost /officialpost /award /xpadjust /xpexclude /xpinclude /referralclear /challenge /cycle
- **/announce**: post an announcement as a Discord embed card (mod-only); defaults to the announcements channel, optional @everyone ping
- **Points (Reach XP and Builder XP)**: members post proof in dedicated channels, a human reviewer checks it and awards points, and points are counted in 14-day cycles to pick the next Elites. Scoring is deterministic, no LLM involved. See [Points](#points-reach-xp-and-builder-xp) below. Everything runs on UTC.
- **Tag-to-teach**: @mention the bot anywhere and it answers as a senior prompt engineer / AI mentor — questions, explanations, and ready-to-paste prompts
- **Welcome DM**: new members get one onboarding question; replies are forwarded to a private staff channel (falls back to the public greet if DMs are closed)
- **Daily prompt**: one community prompt posted to a public channel each day at a fixed local time, rotating through a 14-prompt bank (state persisted across restarts)
- **Tutor**: the structured course now lives on the NEXTGEN Academy website (`ACADEMY_LINK`) instead of in the bot; "where do I start" style questions get pointed there, plus a tool rec if the message actually calls for one
- **Prompt helper**: tag the bot for a prompt (graphic, tweet, thread, video, landing page, app, ...) and it acts like a senior prompt engineer — asks a few quick intake questions, then drafts a premium, ready-to-paste prompt; recommends CREAO first (try it first; alternatives only when you come back and ask)
- **CREAO-first recommendations**: every tool rec starts with CREAO, then honest alternatives; agent/automation questions get the CREAO agent-builder link, earning/monetizing questions get the AI-income-use-cases link (both contextual only, never unprompted)
- **Retention (message quota + daily warn + auto-kick)**: every member without an immune role must send `AUTO_KICK_REQUIRED_MESSAGES` messages (default 10) within a rolling `AUTO_KICK_INACTIVE_DAYS`-day cycle (default 14). From the halfway point on (`AUTO_KICK_WARNING_DAYS`, default 7), anyone still short of quota gets pinged in `RETENTION_WARNING_CHANNEL_ID` every day showing their progress ("4/10 messages") and days left -- not just once, so it doesn't go quiet on someone still catching up. When the cycle ends: quota met -> a fresh cycle starts (warnings clear too); quota missed -> kicked. Founder and immune roles are always exempt. New members and everyone already in the server get a full grace period from the moment this feature deploys — nobody is judged on messages sent before the bot could count them. DMs the member before kicking (best effort), then kicks and logs.
- **Daily social-media reminder**: one nudge per day in a public channel asking members if they've seen today's NEXTGEN post on socials
- Channel silencing: never replies in announcements; ticket auto-replies are off by default (light moderation still runs)

## Privileged Intents (Developer Portal)
Intents are narrowed to only what the bot uses (Presence is OFF). In the Discord Developer Portal, under your application's **Bot** tab, enable these two **privileged** intents or the bot will fail to start:
- **Server Members Intent** (`members`)
- **Message Content Intent** (`message_content`)

The bot also uses the non-privileged `guilds`, `guild_messages`, `dm_messages` (ban appeals + welcome-DM replies), `reactions` (reaction roles) and `invites` (referral tracking) intents. Presence intent is intentionally not requested. Summary of intents in use: **members, message_content, guilds, guild_messages, dm_messages, reactions, invites**.

Retention (auto-kick) needs the bot's server role to have the **Kick Members** permission. This is a normal guild permission granted when you invite the bot / assign its role — not a Developer Portal intent toggle.

Referral tracking reads the server's invite list, which needs the **Manage Server** permission on the bot's role. Without it the bot logs a warning and invite tracking is off; members can still use `/invitedby`. The bot also needs View Channel, Send Messages, Embed Links, Read Message History and Use Application Commands in the proof channels, the staff review channel and the challenge channel.

## Points (Reach XP and Builder XP)
Two separate lanes, each from proof posted in its own channels:
- **Reach XP**: growing NEXTGEN (official post engagement, posting about NEXTGEN on X, referrals).
- **Builder XP**: learning and building (Academy, projects, tutorials, prompt results, events, helping others).

**Time is UTC everywhere.** A day runs 00:00:00 to 23:59:59 UTC and daily caps reset at 00:00 UTC. A week runs Monday 00:00:00 UTC to Sunday 23:59:59 UTC. Cycle number is `floor((UTC date - CYCLE_START_DATE) / CYCLE_LENGTH_DAYS)`, so a cycle rolls over at 00:00:00 UTC. All timestamps are stored in UTC, and a ledger row is stamped with the time the proof was posted (not when a reviewer got to it), so a Sunday 23:59 proof approved on Monday still counts for the week and cycle it was posted in. `POINTS_TZ` exists only so a non-UTC value can be flagged at startup; it is never used for any calculation.

### Channel to lane map
| Channel (env var) | Lane |
|---|---|
| `REACH_CHANNEL_ID` | Reach |
| `ACADEMY_CHANNEL_ID`, `BUILD_CHANNEL_ID`, `TUTORIAL_CHANNEL_ID`, `PROMPT_RESULT_CHANNEL_ID` | Builder |

`REACH_CHANNEL_ID` and `ACADEMY_CHANNEL_ID` used to be one shared channel (`1492637188817682442`); set both. A channel left unset is simply not a proof channel. The chat, tutor and prompt helper never reply in any proof channel. If a proof channel is also in the ignored, announcement or ticket lists the bot logs a warning at startup.

### How proof and review work
1. A member posts proof in a proof channel. Reach needs an `x.com` or `twitter.com` link containing `/status/`; the other channels need a link or an attachment. Academy lesson posts must say `Day N`, and a post covering several days (`day 1-2`, `days 1, 2`, `day 1 and 2`) is rejected and earns nothing. A post that links to a Discord message is a helping submission (helping a new member, helping with an Academy task, teaching a skill) and does not need a day.
2. Duplicate links (query strings ignored) and duplicate images (SHA-256, up to 8 MB) are rejected across all members. One exception: in Reach a member may resubmit their own already approved post link to claim a likes milestone, and the engine still pays each category once per link.
3. The bot replies "Received", then posts a review card in `STAFF_REVIEW_CHANNEL_ID` (falls back to `STAFF_CHANNEL_ID`, then `LOG_CHANNEL_ID`). The card shows the member, lane, channel, jump link, lesson day and flags: inside an official post window, account younger than `MIN_ACCOUNT_AGE_DAYS`, or matches earlier rejected proof.
4. A reviewer picks a category from a menu that only shows categories valid for that channel (Academy lessons allow lesson, test and proof together; everything else is a single choice), then presses **Approve**, **Reject** (asks for a short reason) or **Zero points (spam)** (writes a 0-point row, adds a strike, alerts staff at 3 strikes).
5. Who can review: staff, plus anyone with a role in `REVIEWER_ROLE_IDS` (empty means staff only). Staff means `STAFF_ROLE_IDS`, or the immune roles when that is empty, plus the founder and anyone with Administrator. Nobody can review their own submission or one from a member they invited.
6. Buttons are persistent (the submission id is in every custom id) and are re-registered at startup, so cards keep working after a restart. Deleting a pending proof withdraws it and removes its card; an approved award stays unless staff remove it with `/xpadjust`.

### The ledger
`xp_ledger` is the source of truth and leaderboards are `SUM` queries over it. Every award has a unique `award_key` (for example `{submission_id}:{category}`), so a double click, a retry or a restart can never pay twice. Daily caps (official engagement 30 points per UTC day; 3 counted own posts per UTC day), once-per-link rules, weekly streak bonuses (5 distinct UTC days: own posts +30 Reach, Academy +20 Builder), referrals, learn together and the weekly challenge are all in `xp_engine.py`. No LLM is used to score or review.

### Editing the numbers
All point values, caps, bonuses and referral/challenge rules live in `points_config.py` (data only, no logic). Edit a number there and redeploy. Elite thresholds, slots, cycle length and windows are environment variables (table below).

### Referrals (Reach lane)
Invite use counts are cached at startup and on invite create/delete; when someone joins, the invite whose count went up is attributed, but only if exactly one did. `/invitedby @member` is the fallback (once, within 7 days of joining, never yourself). First attribution wins, and members already in the server are never attributed. Payouts to the inviter: joined +5, first Academy lesson within 7 days +20, active (14 days old with approved proof on 3 distinct days; checked hourly; expires at 30 days) +25, invitee becomes Elite +50. At most 5 counted referrals per inviter per cycle (by the cycle the invitee joined in); the sixth pays nothing. Accounts younger than `MIN_ACCOUNT_AGE_DAYS` are flagged and stages 2 to 4 are held until staff run `/referralclear`. When two people linked by a referral both get an approved Academy lesson with the same day number in the same UTC week, each gets +10 Builder (once per pair per week).

### Weekly challenge
`/challenge create` (staff) posts the challenge in `CHALLENGE_CHANNEL_ID` and runs it until the coming Sunday 23:59:59 UTC (if created on a Sunday, until the next one), with the deadline shown as Discord timestamps so everyone sees their own local time. Lateness is judged by when the proof was posted, not when it was approved. Both parts on time (build in the Build channel, share in the Reach channel) add +10 Builder and +10 Reach once; four challenges in a row with both parts add +40 Builder each time the streak reaches a multiple of 4.

### Commands
| Command | Who | What |
|---|---|---|
| `/xp [member]` | anyone | This cycle's Reach and Builder XP, progress to the Elite minimums, rank in each lane, all-time totals (private) |
| `/xpleaderboard lane period` | anyone | Reach, Builder or combined; this cycle or all time; paginated top 10 |
| `/referrals` | anyone | Your referrals and their stages (private) |
| `/invitedby member` | anyone | Record who invited you if it was not tracked |
| `/challenge status` | anyone | The current challenge and time left |
| `/xpost link note` | staff | Announce an X post, ping the role, and open an official post window |
| `/officialpost link` | staff | Open an official post window without announcing |
| `/challenge create title description` | staff | Start the weekly challenge |
| `/award member category reason` | staff | Staff awards (events, manual) |
| `/xpadjust member lane points reason` | staff | Correction, written to the ledger with a reason |
| `/xpexclude member` / `/xpinclude member` | staff | Take a member out of, or back into, points and leaderboards |
| `/referralclear member` | staff | Release a held (young account) referral |
| `/cycle review` | staff | Posts the Elite shortlist to the staff channel only |

`/cycle review` lists members who meet **both** `ELITE_MIN_REACH` and `ELITE_MIN_BUILDER` this cycle and do not already hold `ELITE_ROLE_ID`, ranked by combined total and limited to `ELITE_SLOTS_PER_CYCLE`, with the breakdown per lane, plus near misses (one lane met), the top referrer, the top builder and the top Reach member. It never assigns a role: selection stays a human decision. Leaderboards and the review hide excluded members, people who left, and immune-role holders (Elite and staff).

(The old single-points commands `/xproof`, `/addxp` and `/removexp` are retired. The old `member_xp`, `x_posts` and `xp_submissions` tables are untouched and kept for reference.)

**Old XP carried over as Reach XP.** On the first start after this shipped, each member's old `member_xp` balance was copied into the ledger once, as Reach XP (category `legacy_import`, award key `legacy:{guild}:{user}`), stamped at the start of the cycle that was current at that moment, so it counts toward that cycle and all-time totals. It cannot run twice (unique key plus a done flag), skips excluded members, and never modifies the old table. Imported Reach XP alone does not put anyone on the Elite shortlist, because that needs the Builder minimum too. To correct an individual balance, use `/xpadjust`.


## Database
Persistence is Postgres via Supabase, not a local file. **This matters on Railway: a local SQLite file lives on the container's disk, which Railway wipes on every deploy and restart** — warnings, mod logs, appeals, and retention progress would silently vanish every time the bot redeployed. Postgres survives that.

Tables live in a dedicated `mod_bot` schema, isolated from any other app sharing the same Supabase project (this bot's `mod_bot_service` role has zero access outside that one schema — verified: it cannot even see other schemas' tables). To set up your own:
```sql
CREATE SCHEMA IF NOT EXISTS mod_bot;
CREATE ROLE mod_bot_service WITH LOGIN PASSWORD '<strong-password>';
ALTER ROLE mod_bot_service SET search_path TO mod_bot;
GRANT USAGE, CREATE ON SCHEMA mod_bot TO mod_bot_service;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA mod_bot TO mod_bot_service;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA mod_bot TO mod_bot_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA mod_bot GRANT ALL ON TABLES TO mod_bot_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA mod_bot GRANT ALL ON SEQUENCES TO mod_bot_service;
```
Then point `SUPABASE_DB_*` (below) at that project and role. `database.py`'s `init_db()` creates the actual tables on first run (idempotent).

**Use the Supavisor pooler (session mode, port 5432), not the direct `db.<ref>.supabase.co` host.** The direct host is IPv6-only, and Railway (and several other platforms) have no IPv6 egress — connecting to it fails with `Network is unreachable`. Get the exact pooler host for your project from the Supabase dashboard (**Connect** button > **Session pooler**): it's a region+shard-specific host like `aws-1-eu-central-1.pooler.supabase.com` (the shard number isn't always `0` — ask the dashboard, don't guess). `SUPABASE_DB_USER` must include the `.<project-ref>` suffix (e.g. `mod_bot_service.hvwuozfsdckopxlbailm`) or Supavisor rejects the connection with `tenant/user not found`, even with a correct password.

## Deploy on Railway
1. Fork or connect this repo on railway.app
2. Add variables: DISCORD_BOT_TOKEN, GROQ_API_KEY, GUILD_ID, LOG_CHANNEL_ID, WELCOME_CHANNEL_ID, and the `SUPABASE_DB_*` variables (see Database and Environment Variables below)
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
| SUPABASE_DB_HOST | Yes | - |
| SUPABASE_DB_PORT | No | 5432 |
| SUPABASE_DB_NAME | No | postgres |
| SUPABASE_DB_USER | No | mod_bot_service |
| SUPABASE_DB_PASSWORD | Yes | - |
| WELCOME_CHANNEL_ID | No | 0 |
| STAFF_CHANNEL_ID | No | 0 |
| PROMPT_CHANNEL_ID | No | 0 |
| PROMPT_HOUR | No | 18 (UTC, same time of day as 19:00 in Lagos) |
| PROMPT_TZ | No | UTC |
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
| XP_PING_ROLE_ID | No | 0 (falls back to MEMBER_ROLE_ID) |
| XP_ANNOUNCE_CHANNEL_ID | No | 0 (/xpost posts here, falling back to the channel it was run in; the daily leaderboard needs it) |
| XP_LEADERBOARD_ENABLED | No | true |
| XP_LEADERBOARD_HOUR | No | 8 (UTC) |
| REACH_CHANNEL_ID | No | 0 (owner sets; Reach proof channel) |
| ACADEMY_CHANNEL_ID | No | 0 (owner sets; Academy proof channel) |
| BUILD_CHANNEL_ID | No | 1529120140711432344 |
| TUTORIAL_CHANNEL_ID | No | 1520157303054139492 |
| PROMPT_RESULT_CHANNEL_ID | No | 1492637326541590790 |
| CHALLENGE_CHANNEL_ID | No | 0 (weekly challenge posts) |
| STAFF_REVIEW_CHANNEL_ID | No | falls back to STAFF_CHANNEL_ID, then LOG_CHANNEL_ID |
| ELITE_ROLE_ID | No | 0 |
| STAFF_ROLE_IDS | No | empty (staff = the immune roles) |
| REVIEWER_ROLE_IDS | No | empty (staff only) |
| POINTS_TZ | No | UTC (points always run on UTC; any other value is only warned about) |
| CYCLE_START_DATE | No | 2026-10-05 (YYYY-MM-DD, read as 00:00:00 UTC; set your first cycle date) |
| CYCLE_LENGTH_DAYS | No | 14 |
| ELITE_MIN_REACH | No | 100 |
| ELITE_MIN_BUILDER | No | 100 |
| ELITE_SLOTS_PER_CYCLE | No | 5 |
| OFFICIAL_POST_WINDOW_MINUTES | No | 120 |
| MIN_ACCOUNT_AGE_DAYS | No | 7 |
| CH_HELP | No | 0 |
| LLM_MODEL | No | openai/gpt-oss-120b |
| CHAT_ENABLED | No | true |
| CHAT_REPLY_DELAY | No | 30 |
| WARN_BEFORE_MUTE | No | 5 |
| WARN_BEFORE_BAN | No | 999 |

> Note: `WARN_BEFORE_BAN` defaults to **999**, so automated escalation effectively never auto-bans. This is intentional in the current config; change it only if you want auto-ban to trigger.

> **Note on `AUTO_KICK_ENABLED`:** this is the most consequential toggle in the bot — it removes real members from the server automatically. It defaults to **true** to match the requested behavior, with real safety rails (grace period on join/first-deploy, a warning ping before the kick, DM before kick, immune-role and founder exemptions), but review `AUTO_KICK_INACTIVE_DAYS`/`AUTO_KICK_WARNING_DAYS` and watch the log + warning channels after your first deploy. Set `AUTO_KICK_ENABLED=false` to turn it off entirely, or just leave `RETENTION_WARNING_CHANNEL_ID` unset to skip the warning ping (the kick itself still runs).
