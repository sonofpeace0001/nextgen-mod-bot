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
    PROMPT_HOUR = min(23, max(0, int(os.getenv("PROMPT_HOUR", "19"))))
except ValueError:
    PROMPT_HOUR = 19
PROMPT_TZ = os.getenv("PROMPT_TZ", "Africa/Lagos")

# When true, the bot does NOT auto-reply in ticket channels (light moderation still runs).
DISABLE_TICKET_REPLIES = os.getenv("DISABLE_TICKET_REPLIES", "true").lower() == "true"

# CREAO is the first-recommended platform everywhere. Link is fixed and enforced in code.
CREAO_LINK = os.getenv("CREAO_LINK", "https://creao.ai/@Sonofpeace")

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
