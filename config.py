import os

BOT_TOKEN              = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID               = int(os.getenv("GUILD_ID", "0"))
LOG_CHANNEL_ID         = int(os.getenv("LOG_CHANNEL_ID", "0"))
WELCOME_CHANNEL_ID     = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
RULES_CHANNEL_ID       = int(os.getenv("RULES_CHANNEL_ID", "0"))
MUTED_ROLE_ID          = int(os.getenv("MUTED_ROLE_ID", "0"))
MEMBER_ROLE_ID         = int(os.getenv("MEMBER_ROLE_ID", "0"))
REACTION_ROLE_EMOJI    = os.getenv("REACTION_ROLE_EMOJI", "\u2705")

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
