import os

BOT_TOKEN              = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID               = int(os.getenv("GUILD_ID", "0"))
LOG_CHANNEL_ID         = int(os.getenv("LOG_CHANNEL_ID", "0"))
WELCOME_CHANNEL_ID     = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
RULES_CHANNEL_ID       = int(os.getenv("RULES_CHANNEL_ID", "0"))
MUTED_ROLE_ID          = int(os.getenv("MUTED_ROLE_ID", "0"))
MEMBER_ROLE_ID         = int(os.getenv("MEMBER_ROLE_ID", "0"))
REACTION_ROLE_EMOJI    = os.getenv("REACTION_ROLE_EMOJI", "\u2705")

# FOUNDER: absolute authority over the bot
FOUNDER_ID             = int(os.getenv("FOUNDER_ID", "1410765594952990801"))

# Escalation role for tickets beyond bot capability
ESCALATION_ROLE_ID     = int(os.getenv("ESCALATION_ROLE_ID", "1465341764125589524"))

# Immune roles
IMMUNE_ROLE_IDS = set()
_immune_raw = os.getenv("IMMUNE_ROLE_IDS", "1434195823960264805,1410807017685123122,1465341764125589524")
for _rid in _immune_raw.split(","):
    _rid = _rid.strip()
    if _rid.isdigit():
        IMMUNE_ROLE_IDS.add(int(_rid))

# Channels the bot must NEVER reply in (loaded from env + database at runtime)
IGNORED_CHANNEL_IDS = set()
_ignored_raw = os.getenv("IGNORED_CHANNEL_IDS", "1479380437196603533")
for _cid in _ignored_raw.split(","):
    _cid = _cid.strip()
    if _cid.isdigit():
        IGNORED_CHANNEL_IDS.add(int(_cid))

# Announcements channel: treated like an ignored channel so the bot never sends
# conversational / tutor / chat / prompt messages there.
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("ANNOUNCEMENT_CHANNEL_ID", "0"))
if ANNOUNCEMENT_CHANNEL_ID:
    IGNORED_CHANNEL_IDS.add(ANNOUNCEMENT_CHANNEL_ID)

# Private staff channel that welcome-DM replies are forwarded to (Task 1).
STAFF_CHANNEL_ID = int(os.getenv("STAFF_CHANNEL_ID", "0"))

# Daily prompt scheduler (Task 2): public channel + fixed local time.
PROMPT_CHANNEL_ID = int(os.getenv("PROMPT_CHANNEL_ID", "0"))
# Clamped to a valid hour: prompts.py builds a datetime.time(hour=PROMPT_HOUR) at import
# time, so an out-of-range value here would crash the ENTIRE bot on startup, not just
# the scheduler. min(23, max(0, ...)) keeps a bad env var from taking the whole bot down.
try:
    PROMPT_HOUR = min(23, max(0, int(os.getenv("PROMPT_HOUR", "18"))))
except ValueError:
    PROMPT_HOUR = 18
PROMPT_TZ = os.getenv("PROMPT_TZ", "UTC")

# When true, the bot does NOT auto-reply in ticket channels (light moderation still runs).
DISABLE_TICKET_REPLIES = os.getenv("DISABLE_TICKET_REPLIES", "true").lower() == "true"

# CREAO is the first-recommended platform everywhere. Link is fixed and enforced in code.
CREAO_LINK = os.getenv("CREAO_LINK", "https://creao.ai/@Sonofpeace")

# NEXTGEN Academy website: the structured course now lives here, not in the bot.
ACADEMY_LINK = os.getenv("ACADEMY_LINK", "https://nextgenai-web.vercel.app/")

# Retention: each non-immune, non-founder member must send AUTO_KICK_REQUIRED_MESSAGES
# messages within a rolling AUTO_KICK_INACTIVE_DAYS-day cycle, or they're kicked when that
# cycle ends. New members and existing members get a full grace period before this can
# ever apply to them (see retention.py).
AUTO_KICK_ENABLED            = os.getenv("AUTO_KICK_ENABLED", "true").lower() == "true"
AUTO_KICK_INACTIVE_DAYS      = int(os.getenv("AUTO_KICK_INACTIVE_DAYS", "14"))
AUTO_KICK_REQUIRED_MESSAGES  = int(os.getenv("AUTO_KICK_REQUIRED_MESSAGES", "10"))
# Same crash risk as PROMPT_HOUR above: clamp so a bad env var can't take the whole bot down.
try:
    RETENTION_CHECK_HOUR = min(23, max(0, int(os.getenv("RETENTION_CHECK_HOUR", "3"))))
except ValueError:
    RETENTION_CHECK_HOUR = 3

# Inactivity WARNING (before the kick): once a member is AUTO_KICK_WARNING_DAYS into their
# cycle and still short of AUTO_KICK_REQUIRED_MESSAGES, they get pinged (once per cycle) in
# RETENTION_WARNING_CHANNEL_ID showing their progress and the consequence. Immune roles/
# founder/bots exempt, same as the kick itself. Leave RETENTION_WARNING_CHANNEL_ID unset to
# disable just the warning ping (the kick itself still runs).
RETENTION_WARNING_CHANNEL_ID = int(os.getenv("RETENTION_WARNING_CHANNEL_ID", "1536365838276235306"))
AUTO_KICK_WARNING_DAYS = int(os.getenv("AUTO_KICK_WARNING_DAYS", "7"))

# Daily social-media reminder: public channel + fixed local time (reuses PROMPT_TZ).
# Channel defaults to PROMPT_CHANNEL_ID if not set separately.
SOCIAL_REMINDER_ENABLED = os.getenv("SOCIAL_REMINDER_ENABLED", "true").lower() == "true"
SOCIAL_REMINDER_CHANNEL_ID = int(os.getenv("SOCIAL_REMINDER_CHANNEL_ID", "0")) or PROMPT_CHANNEL_ID
try:
    SOCIAL_REMINDER_HOUR = min(23, max(0, int(os.getenv("SOCIAL_REMINDER_HOUR", "12"))))
except ValueError:
    SOCIAL_REMINDER_HOUR = 12
SOCIAL_LINKS = os.getenv("SOCIAL_LINKS", "https://x.com/G_NEXTGEN")

# ---------------------------------------------------------------------------
# Points: two lanes (Reach and Builder), proof posted in channels, checked by a human
# reviewer, counted in cycles. Rules and point values live in points_config.py.
# Everything about points runs on UTC.
# ---------------------------------------------------------------------------
def _int(name, default=0):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _id_set(name):
    out = set()
    for part in os.getenv(name, "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


# Proof channels. A channel left at 0 is simply not a proof channel. There is no Academy
# channel any more: the Academy lesson code is still in the engine but nothing switches it on.
#   reach          X links: member posts about NEXTGEN, and the official post cards (/xpost)
#   build          task proof submissions (any post: link, screenshot or text)
#   tutorial, prompt_result    link or attachment, reviewed the same way as build
REACH_CHANNEL_ID         = _int("REACH_CHANNEL_ID", 0)
BUILD_CHANNEL_ID         = _int("BUILD_CHANNEL_ID", 1529120140711432344)
TUTORIAL_CHANNEL_ID      = _int("TUTORIAL_CHANNEL_ID", 1520157303054139492)
PROMPT_RESULT_CHANNEL_ID = _int("PROMPT_RESULT_CHANNEL_ID", 1492637326541590790)
CHALLENGE_CHANNEL_ID     = _int("CHALLENGE_CHANNEL_ID", 0)

# Reviews (Reach proof cards, engagement claims, spam alerts) go to the LOG channel unless
# STAFF_REVIEW_CHANNEL_ID says otherwise.
STAFF_REVIEW_CHANNEL_ID  = _int("STAFF_REVIEW_CHANNEL_ID", 0) or LOG_CHANNEL_ID

# ONE leaderboard channel. The bot keeps two live messages in it (Build and Reach, edited in
# place), and the leaderboard commands only work in it for everyone without a role above Elite.
# LEADERBOARD_COMMAND_CHANNEL_ID can name a different channel for the commands; it defaults
# to LEADERBOARD_CHANNEL_ID. Both 0 = no live boards and no restriction.
LEADERBOARD_CHANNEL_ID         = _int("LEADERBOARD_CHANNEL_ID", 0)
LEADERBOARD_COMMAND_CHANNEL_ID = _int("LEADERBOARD_COMMAND_CHANNEL_ID", 0) or LEADERBOARD_CHANNEL_ID

# Proof channels whose review buttons (category menu, Approve, Decline) are put on the bot's
# reply right in the channel. Anything not listed here sends its review card to the review channel.
REVIEW_IN_CHANNEL_KEYS = {k.strip() for k in os.getenv(
    "REVIEW_IN_CHANNEL_KEYS", "build,tutorial,prompt_result").split(",") if k.strip()}

# channel key (see points_config.CHANNEL_LANES) -> channel id, only for channels that are set
PROOF_CHANNELS = {k: v for k, v in {
    "reach": REACH_CHANNEL_ID,
    "build": BUILD_CHANNEL_ID,
    "tutorial": TUTORIAL_CHANNEL_ID,
    "prompt_result": PROMPT_RESULT_CHANNEL_ID,
}.items() if v}
PROOF_CHANNEL_IDS = set(PROOF_CHANNELS.values())
PROOF_KEY_BY_ID = {v: k for k, v in PROOF_CHANNELS.items()}

# Roles. STAFF_ROLE_IDS empty means staff = the immune roles above (plus the founder and
# anyone with Administrator). REVIEWER_ROLE_IDS empty means staff only can review.
ELITE_ROLE_ID     = _int("ELITE_ROLE_ID", 0)
STAFF_ROLE_IDS    = _id_set("STAFF_ROLE_IDS")
REVIEWER_ROLE_IDS = _id_set("REVIEWER_ROLE_IDS")

# Time. Points code runs on UTC only; POINTS_TZ exists so a non-UTC value can be flagged at
# startup, it is never used to compute a day, week or cycle.
POINTS_TZ = os.getenv("POINTS_TZ", "UTC")
_DEFAULT_CYCLE_START = "2026-10-05"
CYCLE_START_DATE = os.getenv("CYCLE_START_DATE", _DEFAULT_CYCLE_START).strip() or _DEFAULT_CYCLE_START
CYCLE_START_DATE_IS_DEFAULT = "CYCLE_START_DATE" not in os.environ
CYCLE_LENGTH_DAYS = max(1, _int("CYCLE_LENGTH_DAYS", 14))

# Elite selection (/cycle review). A member needs BOTH minimums in the same cycle.
ELITE_MIN_REACH       = _int("ELITE_MIN_REACH", 100)
ELITE_MIN_BUILDER     = _int("ELITE_MIN_BUILDER", 100)
ELITE_SLOTS_PER_CYCLE = max(0, _int("ELITE_SLOTS_PER_CYCLE", 5))

# Reach proof timing and account checks
OFFICIAL_POST_WINDOW_MINUTES = max(1, _int("OFFICIAL_POST_WINDOW_MINUTES", 120))
MIN_ACCOUNT_AGE_DAYS         = max(0, _int("MIN_ACCOUNT_AGE_DAYS", 7))

# /xpost posts its engagement card in the Reach channel (the channel it was run in if that is
# not set) and pings XP_PING_ROLE_ID (defaults to MEMBER_ROLE_ID).
XP_PING_ROLE_ID = int(os.getenv("XP_PING_ROLE_ID", "0")) or MEMBER_ROLE_ID

# The live leaderboards are refreshed every day at this hour in UTC, so they roll over to the
# new cycle at midnight (and they update within seconds of any approval).
XP_LEADERBOARD_ENABLED = os.getenv("XP_LEADERBOARD_ENABLED", "true").lower() == "true"
XP_LEADERBOARD_HOUR = min(23, max(0, _int("XP_LEADERBOARD_HOUR", 0)))

# Ticket channel detection
TICKET_KEYWORDS        = os.getenv("TICKET_KEYWORDS", "ticket,support,help-desk").split(",")

# Timeout duration in minutes
TIMEOUT_DURATION_MIN   = int(os.getenv("TIMEOUT_DURATION_MIN", "10"))

CHANNEL_MAP = {
    "rules":        int(os.getenv("CH_RULES",        "0")),
    "introductions":int(os.getenv("CH_INTRODUCTIONS","0")),
    "general":      int(os.getenv("CH_GENERAL",       "0")),
    "announcements":int(os.getenv("CH_ANNOUNCEMENTS", "0")),
    "help":         int(os.getenv("CH_HELP",          "0")),
    "off-topic":    int(os.getenv("CH_OFFTOPIC",      "0")),
    "resources":    int(os.getenv("CH_RESOURCES",     "0")),
}

WARN_BEFORE_MUTE   = int(os.getenv("WARN_BEFORE_MUTE", "5"))
WARN_BEFORE_BAN    = int(os.getenv("WARN_BEFORE_BAN",  "999"))
MUTE_DURATION_MIN  = int(os.getenv("MUTE_DURATION_MIN","10"))
SPAM_MESSAGE_COUNT  = 7
SPAM_WINDOW_SECONDS = 8
CHAT_REPLY_DELAY   = int(os.getenv("CHAT_REPLY_DELAY", "30"))
CHAT_ENABLED       = os.getenv("CHAT_ENABLED", "true").lower() == "true"
