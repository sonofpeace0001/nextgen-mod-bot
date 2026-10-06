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
- Slash commands: /warn /mute /unmute /purge /warnings /clearwarnings /modlog /slowmode /lookup /note /reactionrole /report /guide /announce /ignore /unignore /ignoredchannels, plus the points commands: /xp /buildleaderboard /reachleaderboard /referrals /invitedby /xpost /officialpost /award /xpadjust /xpexclude /xpinclude /referralclear /challenge /cycle
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
- **Builder XP**: learning and building (task proof, projects, tutorials, prompt results, events, helping others).

**Time is UTC everywhere.** A day runs 00:00:00 to 23:59:59 UTC and daily caps reset at 00:00 UTC. A week runs Monday 00:00:00 UTC to Sunday 23:59:59 UTC. Cycle number is `floor((UTC date - CYCLE_START_DATE) / CYCLE_LENGTH_DAYS)`, so a cycle rolls over at 00:00:00 UTC. All timestamps are stored in UTC, and a ledger row is stamped with the time the proof was posted (not when a reviewer got to it), so a Sunday 23:59 proof approved on Monday still counts for the week and cycle it was posted in. `POINTS_TZ` exists only so a non-UTC value can be flagged at startup; it is never used for any calculation.

### Channels
| Channel (env var) | Used for | Lane |
|---|---|---|
| `REACH_CHANNEL_ID` | `/xpost` cards, official links from roles above Elite, members' own X posts as proof | Reach |
| `BUILD_CHANNEL_ID` | Task proof submissions (any post) | Builder |
| `TUTORIAL_CHANNEL_ID`, `PROMPT_RESULT_CHANNEL_ID` | Tutorial and prompt result proof | Builder |
| `LEADERBOARD_CHANNEL_ID` | The two live leaderboards, and the only place the leaderboard commands work for everyone without a role above Elite | none |
| `LOG_CHANNEL_ID` | Review: Reach proof cards, engagement claims, spam alerts (override with `STAFF_REVIEW_CHANNEL_ID`) | none |

**There is no Academy channel any more.** The Academy lesson code (Day N posts, referral "first lesson" payout, learn-together, Academy streak) is still in the engine but nothing switches it on, so those bonuses can no longer be earned. A channel left unset is simply not a proof channel. The chat, tutor and prompt helper never reply in any proof channel. If a proof channel is also in the ignored, announcement or ticket lists the bot logs a warning at startup and stays silent there.

### How proof and review work
1. **Builder channels** (Build, Tutorial, Prompt result) take whatever a member posts: a link, a screenshot, or just text such as "task completed". A text-only post shorter than 8 characters is treated as chatter and ignored. **Reach** needs an `x.com` or `twitter.com` link containing `/status/`.
2. Duplicate links (query strings ignored) and duplicate images (SHA-256, up to 8 MB) are rejected across all members. One exception: in Reach a member may resubmit their own already approved post link to claim a likes milestone, and the engine still pays each category once per link.
3. **Review buttons appear automatically, no command needed.** For the channels in `REVIEW_IN_CHANNEL_KEYS` (default: build, tutorial, prompt_result) the bot replies right under the member's post with "Received" and a category menu plus **Approve**, **Decline** (optional reason) and **Zero points (spam)** buttons. When a moderator decides, that same message is edited to show the outcome ("Approved. +20 builder XP for build demo."). Anything not in that list (Reach proof) sends its review card to `STAFF_REVIEW_CHANNEL_ID` instead (falls back to `STAFF_CHANNEL_ID`, then `LOG_CHANNEL_ID`). Flags such as a new account or a match with earlier rejected proof are sent to the staff channel, never shown publicly.
4. **Only moderators with a role above Elite can use the buttons.** That means anyone whose highest role sits above the `ELITE_ROLE_ID` role in the server's role list, plus the founder, Administrators and any role in `STAFF_ROLE_IDS`. Elites cannot approve. `REVIEWER_ROLE_IDS` adds extra reviewer roles. Nobody can review their own submission or one from a member they invited. Everyone else who presses a button gets a private "only moderators with a role above Elite" message.
5. **Zero points (spam)** writes a 0-point row, adds a strike, and alerts staff at 3 strikes.
6. Buttons are persistent and re-registered at startup, so cards keep working after a restart. Deleting a pending proof withdraws it and removes its card; an approved award stays unless staff remove it with `/xpadjust`.

### Official post cards (Like, Retweet, Comment)
When someone with a role above Elite drops an X link (`.../status/...`) in the Reach channel, the bot posts a card in its place and deletes the original (needs Manage Messages; if it cannot, the link stays). The link is kept in the card's message text, so Discord still shows the post's own preview. The card pings `XP_PING_ROLE_ID` and has three buttons, each worth Reach XP (edit in `points_config.py`): **Like +2, Retweet +3, Comment +5**. `/xpost` always posts the same card in the Reach channel, wherever it is run.

A member does it on X first, then presses the button for each thing they did:
1. The first time, a short form asks for their X username (and an optional link to their reply). It is remembered, so later claims are one click. `/xphandle` changes it.
2. They see "Verification in progress" (private), and a claim card with their X profile link, the post and Approve / Decline buttons goes to the log channel (the review channel).
3. A moderator above Elite checks X and approves or declines. On approval the Reach XP is added to the ledger, the leaderboard updates by itself, and the member gets a DM. Declined claims can be claimed again.
One live claim per member, post and action. These share the 30-points-per-UTC-day cap on official engagement. The buttons are restored automatically after a restart.

### Leaderboards: one channel, two commands
There is one leaderboard channel (`LEADERBOARD_CHANNEL_ID`). In it the bot keeps **two live messages**, Build (Builder XP) and Engagement (Reach XP), each showing the top 10 this cycle and the top 5 all time. They are edited in place a few seconds after points change, again at 00:00 UTC for the new cycle, and re-posted if deleted.

There are two commands, `/buildleaderboard` and `/reachleaderboard` (each with a this-cycle / all-time choice, paginated). **They only work in the leaderboard channel.** Anywhere else, a member gets a private "Leaderboard commands only work in #channel" and nothing is shown. The only exception is anyone with a role above Elite, who can use them anywhere. (`LEADERBOARD_COMMAND_CHANNEL_ID` can name a different channel for the commands; it defaults to the leaderboard channel.) `/xp` is a private check of your own numbers and works anywhere.

Elites can earn and are shown, tagged "(Elite)" next to their name. The founder, people with a role above Elite, excluded members and people who left are not shown.

### The ledger
`xp_ledger` is the source of truth and leaderboards are `SUM` queries over it. Every award has a unique `award_key` (for example `{submission_id}:{category}`), so a double click, a retry or a restart can never pay twice. Daily caps (official engagement 30 points per UTC day; 3 counted own posts per UTC day), once-per-link rules, weekly streak bonuses (5 distinct UTC days: own posts +30 Reach, Academy +20 Builder), referrals, learn together and the weekly challenge are all in `xp_engine.py`. No LLM is used to score or review.

### All-time XP and cycle XP
Every member has two numbers per lane. **All-time XP** is everything ever earned, including the old imported points and the one-off correction, and is what the leaderboards list under "All-time XP". **Cycle XP** is what was earned in the current 14-day cycle from proof, claims, bonuses and staff awards only; the legacy import and the correction are excluded, so **everyone starts at 0**, and it goes back to 0 at each cycle rollover (00:00 UTC). Cycle XP is what decides Elite.

### Automatic Elite role
- The moment a member's cycle XP reaches **both** `ELITE_MIN_REACH` and `ELITE_MIN_BUILDER` (100 and 100 by default), the bot gives them the Elite role and says so in the log channel. It checks a few seconds after every approval and hourly, so there is no waiting for the end of the cycle and no slot limit (`ELITE_SLOTS_PER_CYCLE` now only shapes `/cycle review`).
- When a cycle closes, every Elite who did not reach both minimums in that cycle loses the role (checked once per finished cycle, announced in the log channel). Reaching them again later in a cycle gives it straight back. Nothing is removed during the first cycle (`CYCLE_START_DATE`), since there is no earlier cycle to judge.
- The founder and anyone above the Elite role are never changed. The bot needs **Manage Roles** and its own role must sit above the Elite role; otherwise it logs a warning and retries at the next check.

### Cycle cap (for now)
Cycle XP is capped at `POINTS_CAP` (default **100**) **per lane per cycle**, so a member can earn at most 100 Reach and 100 Builder XP in a cycle, which is exactly the Elite bar. An award that would take someone past it is trimmed ("trimmed to the 100 point cycle cap"), and at the cap it pays 0 ("cycle cap reached"); this covers reviewed proof, claims, bonuses, referral payouts and `/award`. Staff corrections with `/xpadjust` and the one-off import are not capped, and all-time XP is not capped on its own: it keeps growing across cycles. Set `POINTS_CAP=0` to remove the cap.

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
| `/buildleaderboard period` | anyone, in the leaderboard channel | Builder XP top earners; this cycle or all time; paginated |
| `/reachleaderboard period` | anyone, in the leaderboard channel | Reach XP (engaging on posts) top earners; this cycle or all time; paginated |
| `/referrals` | anyone | Your referrals and their stages (private) |
| `/invitedby member` | anyone | Record who invited you if it was not tracked |
| `/xphandle username` | anyone | Set or change the X username used on engagement claims |
| `/challenge status` | anyone | The current challenge and time left |
| `/xpost link note` | above Elite | Post an X link as an engagement card in the Reach channel (Like, Retweet, Comment buttons) |
| `/officialpost link` | staff | Open an official post window (only used to flag Reach proof posted in time) |
| `/challenge create title description` | staff | Start the weekly challenge |
| `/award member category reason` | staff | Staff awards (events, manual) |
| `/xpadjust member lane points reason` | staff | Correction, written to the ledger with a reason |
| `/xpexclude member` / `/xpinclude member` | staff | Take a member out of, or back into, points and leaderboards |
| `/referralclear member` | staff | Release a held (young account) referral |
| `/cycle review` | staff | Posts this cycle's progress toward Elite to the staff channel only |

`/cycle review` lists members who meet **both** `ELITE_MIN_REACH` and `ELITE_MIN_BUILDER` this cycle and do not already hold `ELITE_ROLE_ID`, ranked by combined total and limited to `ELITE_SLOTS_PER_CYCLE`, with the breakdown per lane, plus near misses (one lane met), the top referrer, the top builder and the top Reach member. The Elite role itself is handled automatically (see Automatic Elite role below); the review is a progress report. Leaderboards hide excluded members, people who left, the founder, and holders of an immune role other than Elite (staff and admins). Elites are visible, but the shortlist never includes someone who already holds the Elite role.

(The old single-points commands `/xproof`, `/addxp` and `/removexp` are retired. The old `member_xp`, `x_posts` and `xp_submissions` tables are untouched and kept for reference.)

**Old XP carried over, half Builder and half Reach; Elites start from zero.** Once, after the first start with `ELITE_ROLE_ID` set, each non-Elite member's old `member_xp` balance is split into two equal halves and copied into the ledger: one half as **Builder XP**, one as **Reach XP** (an odd point goes to Builder, so 285 becomes 143 + 142). Rows are category `legacy_import`, keys `legacy:{guild}:{user}:{lane}`, stamped at the start of the cycle that is current at that moment, so they count toward **all-time XP only**. They are left out of every cycle total, so cycle XP starts at 0 for everyone. Members who hold the Elite role are skipped and start from zero (they still appear on the leaderboards, tagged "(Elite)", as soon as they earn). It waits, and logs a warning, until `ELITE_ROLE_ID` is set and found in the server; it cannot run twice (unique keys plus a done flag), skips excluded members, and never modifies the old table. Old points never count toward Elite: only cycle XP does. To correct an individual balance, use `/xpadjust`. The split is set in `points_config.py` (`LEGACY_IMPORT`).

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
| XP_LEADERBOARD_ENABLED | No | true |
| XP_LEADERBOARD_HOUR | No | 0 (UTC; when the live boards are refreshed for the new cycle) |
| REACH_CHANNEL_ID | No | 0 (owner sets; Reach proof channel) |
| BUILD_CHANNEL_ID | No | 1529120140711432344 |
| TUTORIAL_CHANNEL_ID | No | 1520157303054139492 |
| PROMPT_RESULT_CHANNEL_ID | No | 1492637326541590790 |
| CHALLENGE_CHANNEL_ID | No | 0 (weekly challenge posts) |
| LEADERBOARD_CHANNEL_ID | No | 0 (the one leaderboard channel: live boards, and where the commands work) |
| LEADERBOARD_COMMAND_CHANNEL_ID | No | = LEADERBOARD_CHANNEL_ID (only if the commands should work in a different channel) |
| REVIEW_IN_CHANNEL_KEYS | No | build,tutorial,prompt_result (channels whose review buttons sit under the post) |
| STAFF_REVIEW_CHANNEL_ID | No | LOG_CHANNEL_ID (reviews go to the log channel unless you set this) |
| ELITE_ROLE_ID | **Set this** | 0 (needed for "above Elite" checks, the legacy import, and showing Elites on leaderboards) |
| STAFF_ROLE_IDS | No | empty (extra roles treated as above Elite) |
| REVIEWER_ROLE_IDS | No | empty (extra reviewer roles; empty means only roles above Elite) |
| POINTS_TZ | No | UTC (points always run on UTC; any other value is only warned about) |
| CYCLE_START_DATE | No | 2026-10-05 (YYYY-MM-DD, read as 00:00:00 UTC; set your first cycle date) |
| CYCLE_LENGTH_DAYS | No | 14 |
| POINTS_CAP | No | 100 (a ceiling on each member's cycle XP per lane; 0 turns it off) |
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
