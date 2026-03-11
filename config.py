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

# Immune roles: members with these roles cannot be warned, muted, timed out, banned, or have messages deleted
IMMUNE_ROLE_IDS = set()
_immune_raw = os.getenv("IMMUNE_ROLE_IDS", "1434195823960264805,1410807017685123122,1465341764125589524")
for _rid in _immune_raw.split(","):
    _rid = _rid.strip()
    if _rid.isdigit():
        IMMUNE_ROLE_IDS.add(int(_rid))

# Ticket channel detection: channels with these keywords in name are treated as tickets
TICKET_KEYWORDS        = os.getenv("TICKET_KEYWORDS", "ticket,support,help-desk").split(",")

# Timeout duration in minutes (used instead of muted role)
TIMEOUT_DURATION_MIN   = int(os.getenv("TIMEOUT_DURATION_MIN", "30"))

CHANNEL_MAP = {
    "rules":        int(os.getenv("CH_RULES",        "0")),
    "introductions":int(os.getenv("CH_INTRODUCTIONS","0")),
    "general":      int(os.getenv("CH_GENERAL",       "0")),
    "announcements":int(os.getenv("CH_ANNOUNCEMENTS", "0")),
    "help":         int(os.getenv("CH_HELP",          "0")),
    "off-topic":    int(os.getenv("CH_OFFTOPIC",      "0")),
    "resources":    int(os.getenv("CH_RESOURCES",     "0")),
}

WARN_BEFORE_MUTE   = int(os.getenv("WARN_BEFORE_MUTE", "3"))
WARN_BEFORE_BAN    = int(os.getenv("WARN_BEFORE_BAN",  "5"))
MUTE_DURATION_MIN  = int(os.getenv("MUTE_DURATION_MIN","30"))
SPAM_MESSAGE_COUNT  = 5
SPAM_WINDOW_SECONDS = 8
CHAT_REPLY_DELAY   = int(os.getenv("CHAT_REPLY_DELAY", "30"))
CHAT_ENABLED       = os.getenv("CHAT_ENABLED", "true").lower() == "true"
