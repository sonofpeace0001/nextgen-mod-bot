import os

BOT_TOKEN              = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID               = int(os.getenv("GUILD_ID", "0"))
LOG_CHANNEL_ID         = int(os.getenv("LOG_CHANNEL_ID", "0"))
WELCOME_CHANNEL_ID     = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
RULES_CHANNEL_ID       = int(os.getenv("RULES_CHANNEL_ID", "0"))
MUTED_ROLE_ID          = int(os.getenv("MUTED_ROLE_ID", "0"))
MEMBER_ROLE_ID         = int(os.getenv("MEMBER_ROLE_ID", "0"))
REACTION_ROLE_EMOJI    = os.getenv("REACTION_ROLE_EMOJI", "\u2705")

# Escalation role for tickets beyond bot capability
ESCALATION_ROLE_ID     = int(os.getenv("ESCALATION_ROLE_ID", "1465341764125589524"))

# Immune roles
IMMUNE_ROLE_IDS = set()
_immune_raw = os.getenv("IMMUNE_ROLE_IDS", "1434195823960264805,1410807017685123122,1465341764125589524")
for _rid in _immune_raw.split(","):
    _rid = _rid.strip()
    if _rid.isdigit():
        IMMUNE_ROLE_IDS.add(int(_rid))

# Channels the bot must NEVER reply in (ignored completely)
IGNORED_CHANNEL_IDS = set()
_ignored_raw = os.getenv("IGNORED_CHANNEL_IDS", "1479380437196603533")
for _cid in _ignored_raw.split(","):
    _cid = _cid.strip()
    if _cid.isdigit():
        IGNORED_CHANNEL_IDS.add(int(_cid))

# Ticket channel detection
TICKET_KEYWORDS        = os.getenv("TICKET_KEYWORDS", "ticket,support,help-desk").split(",")

# Timeout duration in minutes (gentler: 10 min default instead of 30)
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

# More lenient thresholds: 5 warnings before timeout, no auto-ban
WARN_BEFORE_MUTE   = int(os.getenv("WARN_BEFORE_MUTE", "5"))
WARN_BEFORE_BAN    = int(os.getenv("WARN_BEFORE_BAN",  "999"))  # effectively disabled
MUTE_DURATION_MIN  = int(os.getenv("MUTE_DURATION_MIN","10"))
SPAM_MESSAGE_COUNT  = 7   # more forgiving spam threshold
SPAM_WINDOW_SECONDS = 8
CHAT_REPLY_DELAY   = int(os.getenv("CHAT_REPLY_DELAY", "30"))
CHAT_ENABLED       = os.getenv("CHAT_ENABLED", "true").lower() == "true"
