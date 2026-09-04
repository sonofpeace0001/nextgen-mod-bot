"""Postgres (Supabase) persistence for all bot data, in the mod_bot schema.

Was SQLite on local container disk, which Railway wipes on every deploy/restart --
every warning, mod log entry, appeal, and retention cycle was being lost on every
redeploy. Moved to a dedicated Postgres schema (mod_bot) in the same Supabase project
as the NEXTGEN Academy website, under a role scoped ONLY to that schema (no access to
the website's public-schema data).

Uses one long-lived, auto-reconnecting psycopg2 connection with the exact same
synchronous call pattern the old sqlite3 code used, so no other module needed to
change how it calls this one. Every function signature and return shape below is
unchanged from before.
"""
import os
import psycopg2
import psycopg2.extras

_DSN = dict(
    host=os.getenv("SUPABASE_DB_HOST", "db.hvwuozfsdckopxlbailm.supabase.co"),
    port=int(os.getenv("SUPABASE_DB_PORT", "5432")),
    dbname=os.getenv("SUPABASE_DB_NAME", "postgres"),
    user=os.getenv("SUPABASE_DB_USER", "mod_bot_service"),
    password=os.getenv("SUPABASE_DB_PASSWORD", ""),
    connect_timeout=10,
)

_conn_obj = None
_NOW = "to_char(now(), 'YYYY-MM-DD HH24:MI:SS')"  # matches SQLite's datetime('now') format


def _run(query, params=(), fetch=None, commit=False):
    """fetch: None | 'one' | 'all'. Reconnects and retries once if the connection dropped
    (network blip, Supabase restart, idle timeout) instead of just crashing the caller."""
    global _conn_obj
    for attempt in range(2):
        try:
            if _conn_obj is None or _conn_obj.closed:
                _conn_obj = psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **_DSN)
            cur = _conn_obj.cursor()
            cur.execute(query, params)
            result = None
            if fetch == "one":
                row = cur.fetchone()
                result = dict(row) if row else None
            elif fetch == "all":
                result = [dict(r) for r in cur.fetchall()]
            if commit:
                _conn_obj.commit()
            cur.close()
            return result
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            try:
                if _conn_obj: _conn_obj.close()
            except Exception:
                pass
            _conn_obj = None
            if attempt == 1:
                raise


def init_db():
    # Schema creation is a one-time DBA task done via migration with an elevated
    # connection (see the mod_bot_service role grants) -- the app's own runtime role
    # intentionally cannot CREATE SCHEMA, only CREATE within it. These are idempotent
    # (IF NOT EXISTS) so this just confirms tables exist; it doesn't recreate anything.
    _run("""
    CREATE TABLE IF NOT EXISTS mod_bot.warnings (
        id BIGSERIAL PRIMARY KEY, guild_id BIGINT, user_id BIGINT,
        moderator TEXT, reason TEXT, timestamp TEXT DEFAULT """ + _NOW + """
    );
    CREATE TABLE IF NOT EXISTS mod_bot.mod_log (
        id BIGSERIAL PRIMARY KEY, guild_id BIGINT, action TEXT,
        target_id BIGINT, moderator TEXT, reason TEXT,
        timestamp TEXT DEFAULT """ + _NOW + """
    );
    CREATE TABLE IF NOT EXISTS mod_bot.spam_exempt (user_id BIGINT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS mod_bot.tempbans (
        id BIGSERIAL PRIMARY KEY, guild_id BIGINT, user_id BIGINT,
        moderator TEXT, reason TEXT, unban_at TEXT
    );
    CREATE TABLE IF NOT EXISTS mod_bot.appeals (
        id BIGSERIAL PRIMARY KEY, guild_id BIGINT, user_id BIGINT,
        reason TEXT, status TEXT DEFAULT 'pending',
        message_id BIGINT DEFAULT 0, timestamp TEXT DEFAULT """ + _NOW + """
    );
    CREATE TABLE IF NOT EXISTS mod_bot.notes (
        id BIGSERIAL PRIMARY KEY, guild_id BIGINT, user_id BIGINT,
        moderator TEXT, content TEXT, created_at TEXT DEFAULT """ + _NOW + """
    );
    CREATE TABLE IF NOT EXISTS mod_bot.reports (
        id BIGSERIAL PRIMARY KEY, guild_id BIGINT, reporter_id BIGINT,
        target_id BIGINT, reason TEXT, message_content TEXT DEFAULT '',
        message_url TEXT DEFAULT '', status TEXT DEFAULT 'open',
        message_id BIGINT DEFAULT 0, timestamp TEXT DEFAULT """ + _NOW + """
    );
    CREATE TABLE IF NOT EXISTS mod_bot.reaction_roles (
        id BIGSERIAL PRIMARY KEY, guild_id BIGINT, channel_id BIGINT,
        message_id BIGINT, emoji TEXT, role_id BIGINT,
        description TEXT DEFAULT '', created_by TEXT
    );
    CREATE TABLE IF NOT EXISTS mod_bot.ignored_channels (
        channel_id BIGINT PRIMARY KEY, guild_id BIGINT,
        added_by TEXT, added_at TEXT DEFAULT """ + _NOW + """
    );
    CREATE TABLE IF NOT EXISTS mod_bot.welcomed_users (
        user_id BIGINT PRIMARY KEY, guild_id BIGINT,
        awaiting_reply INTEGER DEFAULT 1, created_at TEXT DEFAULT """ + _NOW + """
    );
    CREATE TABLE IF NOT EXISTS mod_bot.member_level (
        user_id BIGINT PRIMARY KEY, level TEXT DEFAULT 'novice'
    );
    CREATE TABLE IF NOT EXISTS mod_bot.kv_store (
        key TEXT PRIMARY KEY, value TEXT
    );
    CREATE TABLE IF NOT EXISTS mod_bot.last_activity (
        user_id BIGINT, guild_id BIGINT, last_seen TEXT, warned_at TEXT,
        cycle_start TEXT, msg_count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, guild_id)
    );
    """, commit=True)
    _load_ignored_channels()


def add_warning(gid, uid, mod, reason):
    _run("INSERT INTO mod_bot.warnings (guild_id,user_id,moderator,reason) VALUES (%s,%s,%s,%s)",
         (gid, uid, mod, reason), commit=True)
    r = _run("SELECT COUNT(*) AS c FROM mod_bot.warnings WHERE guild_id=%s AND user_id=%s", (gid, uid), fetch="one")
    return r["c"]

def get_warnings(gid, uid):
    return _run("SELECT * FROM mod_bot.warnings WHERE guild_id=%s AND user_id=%s ORDER BY id", (gid, uid), fetch="all")

def clear_warnings(gid, uid):
    _run("DELETE FROM mod_bot.warnings WHERE guild_id=%s AND user_id=%s", (gid, uid), commit=True)

def log_action(gid, action, tid, mod, reason=""):
    _run("INSERT INTO mod_bot.mod_log (guild_id,action,target_id,moderator,reason) VALUES (%s,%s,%s,%s,%s)",
         (gid, action, tid, mod, reason), commit=True)

def get_recent_log(gid, limit=10):
    return _run("SELECT * FROM mod_bot.mod_log WHERE guild_id=%s ORDER BY id DESC LIMIT %s", (gid, limit), fetch="all")

def get_user_mod_history(gid, uid):
    return _run("SELECT * FROM mod_bot.mod_log WHERE guild_id=%s AND target_id=%s ORDER BY id DESC", (gid, uid), fetch="all")

def is_spam_exempt(uid):
    return _run("SELECT 1 FROM mod_bot.spam_exempt WHERE user_id=%s", (uid,), fetch="one") is not None

def add_tempban(gid, uid, mod, reason, unban_at):
    _run("INSERT INTO mod_bot.tempbans (guild_id,user_id,moderator,reason,unban_at) VALUES (%s,%s,%s,%s,%s)",
         (gid, uid, mod, reason, unban_at.isoformat()), commit=True)

def remove_tempban(gid, uid):
    _run("DELETE FROM mod_bot.tempbans WHERE guild_id=%s AND user_id=%s", (gid, uid), commit=True)

def get_pending_tempbans():
    return _run("SELECT * FROM mod_bot.tempbans", fetch="all")

def get_active_tempban(gid, uid):
    return _run("SELECT * FROM mod_bot.tempbans WHERE guild_id=%s AND user_id=%s", (gid, uid), fetch="one")

def add_appeal(gid, uid, reason):
    r = _run("INSERT INTO mod_bot.appeals (guild_id,user_id,reason) VALUES (%s,%s,%s) RETURNING id",
              (gid, uid, reason), fetch="one", commit=True)
    return r["id"]

def get_appeal(aid):
    return _run("SELECT * FROM mod_bot.appeals WHERE id=%s", (aid,), fetch="one")

def update_appeal_status(aid, status):
    _run("UPDATE mod_bot.appeals SET status=%s WHERE id=%s", (status, aid), commit=True)

def update_appeal_message(aid, mid):
    _run("UPDATE mod_bot.appeals SET message_id=%s WHERE id=%s", (mid, aid), commit=True)

def get_user_appeals(gid, uid):
    return _run("SELECT * FROM mod_bot.appeals WHERE guild_id=%s AND user_id=%s ORDER BY id DESC", (gid, uid), fetch="all")

def get_pending_appeals():
    return _run("SELECT * FROM mod_bot.appeals WHERE status='pending'", fetch="all")

def add_note(gid, uid, mod, content):
    r = _run("INSERT INTO mod_bot.notes (guild_id,user_id,moderator,content) VALUES (%s,%s,%s,%s) RETURNING id",
              (gid, uid, mod, content), fetch="one", commit=True)
    return r["id"]

def get_note(nid, gid):
    return _run("SELECT * FROM mod_bot.notes WHERE id=%s AND guild_id=%s", (nid, gid), fetch="one")

def edit_note(nid, gid, content):
    _run("UPDATE mod_bot.notes SET content=%s WHERE id=%s AND guild_id=%s", (content, nid, gid), commit=True)

def delete_note(nid, gid):
    _run("DELETE FROM mod_bot.notes WHERE id=%s AND guild_id=%s", (nid, gid), commit=True)

def get_user_notes(gid, uid):
    return _run("SELECT * FROM mod_bot.notes WHERE guild_id=%s AND user_id=%s ORDER BY id DESC", (gid, uid), fetch="all")

def add_report(gid, reporter, target, reason, msg_content="", msg_url=""):
    r = _run(
        "INSERT INTO mod_bot.reports (guild_id,reporter_id,target_id,reason,message_content,message_url) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (gid, reporter, target, reason, msg_content, msg_url), fetch="one", commit=True,
    )
    return r["id"]

def get_report(rid):
    return _run("SELECT * FROM mod_bot.reports WHERE id=%s", (rid,), fetch="one")

def update_report_status(rid, status):
    _run("UPDATE mod_bot.reports SET status=%s WHERE id=%s", (status, rid), commit=True)

def update_report_message(rid, mid):
    _run("UPDATE mod_bot.reports SET message_id=%s WHERE id=%s", (mid, rid), commit=True)

def get_pending_reports():
    return _run("SELECT * FROM mod_bot.reports WHERE status='open'", fetch="all")

def get_reports_against(gid, uid):
    return _run("SELECT * FROM mod_bot.reports WHERE guild_id=%s AND target_id=%s ORDER BY id DESC", (gid, uid), fetch="all")

def add_reaction_role(gid, cid, mid, emoji, rid, desc, created_by):
    _run(
        "INSERT INTO mod_bot.reaction_roles (guild_id,channel_id,message_id,emoji,role_id,description,created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (gid, cid, mid, emoji, rid, desc, created_by), commit=True,
    )

def get_reaction_role(mid, emoji):
    return _run("SELECT * FROM mod_bot.reaction_roles WHERE message_id=%s AND emoji=%s", (mid, emoji), fetch="one")

def get_all_reaction_roles(gid):
    return _run("SELECT * FROM mod_bot.reaction_roles WHERE guild_id=%s", (gid,), fetch="all")

def remove_reaction_role(gid, mid, emoji):
    global _conn_obj
    if _conn_obj is None or _conn_obj.closed:
        _run("SELECT 1")  # force a connect via the normal reconnect path
    cur = _conn_obj.cursor()
    cur.execute("DELETE FROM mod_bot.reaction_roles WHERE guild_id=%s AND message_id=%s AND emoji=%s", (gid, mid, emoji))
    n = cur.rowcount
    _conn_obj.commit()
    cur.close()
    return n > 0

def remove_reaction_roles_for_message(gid, mid):
    global _conn_obj
    if _conn_obj is None or _conn_obj.closed:
        _run("SELECT 1")
    cur = _conn_obj.cursor()
    cur.execute("DELETE FROM mod_bot.reaction_roles WHERE guild_id=%s AND message_id=%s", (gid, mid))
    n = cur.rowcount
    _conn_obj.commit()
    cur.close()
    return n

# ── Ignored Channels (persistent) ─────────────────────────────────

def _load_ignored_channels():
    """Load ignored channels from DB into config.IGNORED_CHANNEL_IDS."""
    import config
    try:
        for r in _run("SELECT channel_id FROM mod_bot.ignored_channels", fetch="all"):
            config.IGNORED_CHANNEL_IDS.add(r["channel_id"])
    except Exception:
        pass

def add_ignored_channel(gid, cid, added_by):
    import config
    _run(
        "INSERT INTO mod_bot.ignored_channels (channel_id, guild_id, added_by) VALUES (%s,%s,%s) "
        "ON CONFLICT (channel_id) DO NOTHING",
        (cid, gid, added_by), commit=True,
    )
    config.IGNORED_CHANNEL_IDS.add(cid)

def remove_ignored_channel(gid, cid):
    import config
    global _conn_obj
    if _conn_obj is None or _conn_obj.closed:
        _run("SELECT 1")
    cur = _conn_obj.cursor()
    cur.execute("DELETE FROM mod_bot.ignored_channels WHERE channel_id=%s AND guild_id=%s", (cid, gid))
    n = cur.rowcount
    _conn_obj.commit()
    cur.close()
    config.IGNORED_CHANNEL_IDS.discard(cid)
    return n > 0

def get_ignored_channels(gid):
    return _run("SELECT * FROM mod_bot.ignored_channels WHERE guild_id=%s", (gid,), fetch="all")

# ── Welcome DM tracking (Task 1) ──────────────────────────────────

def add_welcomed_user(uid, gid):
    _run(
        "INSERT INTO mod_bot.welcomed_users (user_id, guild_id, awaiting_reply, created_at) "
        f"VALUES (%s,%s,1,{_NOW}) "
        "ON CONFLICT (user_id) DO UPDATE SET guild_id=EXCLUDED.guild_id, awaiting_reply=1, created_at=EXCLUDED.created_at",
        (uid, gid), commit=True,
    )

def get_welcomed_user(uid):
    return _run("SELECT * FROM mod_bot.welcomed_users WHERE user_id=%s", (uid,), fetch="one")

def clear_awaiting_reply(uid):
    _run("UPDATE mod_bot.welcomed_users SET awaiting_reply=0 WHERE user_id=%s", (uid,), commit=True)

# ── Member skill level (Task 5) ───────────────────────────────────

def set_member_level(uid, level):
    _run(
        "INSERT INTO mod_bot.member_level (user_id, level) VALUES (%s,%s) "
        "ON CONFLICT (user_id) DO UPDATE SET level=EXCLUDED.level",
        (uid, level), commit=True,
    )

def get_member_level(uid):
    r = _run("SELECT level FROM mod_bot.member_level WHERE user_id=%s", (uid,), fetch="one")
    return r["level"] if r else None

# ── Tiny key/value store (Task 2 prompt index, etc.) ──────────────

def kv_get(key, default=None):
    r = _run("SELECT value FROM mod_bot.kv_store WHERE key=%s", (key,), fetch="one")
    return r["value"] if r else default

def kv_set(key, value):
    _run(
        "INSERT INTO mod_bot.kv_store (key, value) VALUES (%s,%s) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        (key, str(value)), commit=True,
    )

# ── Member activity (retention / auto-kick) ────────────────────────
# Each member accumulates a message count within a "cycle" (cycle_start -> +N days).
# record_message() increments the count; it does NOT reset the cycle -- only the daily
# retention check evaluates and resets/kicks once a cycle's days are up. seed_cycle()
# starts a member's very first cycle at zero (new joins, and untracked-member seeding on
# first deploy), without touching anyone who already has a row.

def record_message(gid, uid):
    """A qualifying message just happened: +1 to this member's current-cycle count.
    Starts their first cycle (count=1) if they don't have one yet. If an existing row
    somehow has no cycle_start (e.g. a row migrated from an older schema that seeding
    hasn't backfilled yet), this also self-heals it to now -- never inherit a stale
    cycle_start that could put a member instantly over the day threshold."""
    _run(
        f"INSERT INTO mod_bot.last_activity (user_id, guild_id, last_seen, cycle_start, msg_count, warned_at) "
        f"VALUES (%s,%s,{_NOW},{_NOW},1,NULL) "
        "ON CONFLICT (user_id, guild_id) DO UPDATE SET "
        f"last_seen={_NOW}, msg_count=mod_bot.last_activity.msg_count+1, "
        f"cycle_start=COALESCE(mod_bot.last_activity.cycle_start, {_NOW})",
        (uid, gid), commit=True,
    )

def seed_cycle(gid, uid):
    """Start a member's first cycle at zero messages. No-op if they already have a row,
    so this never clobbers progress -- safe to call unconditionally on join or at startup."""
    _run(
        f"INSERT INTO mod_bot.last_activity (user_id, guild_id, last_seen, cycle_start, msg_count, warned_at) "
        f"VALUES (%s,%s,{_NOW},{_NOW},0,NULL) "
        "ON CONFLICT (user_id, guild_id) DO NOTHING",
        (uid, gid), commit=True,
    )

def reset_cycle(gid, uid):
    """Member passed their cycle (hit the message quota in time): start a fresh one."""
    _run(
        f"UPDATE mod_bot.last_activity SET cycle_start={_NOW}, msg_count=0, warned_at=NULL "
        "WHERE guild_id=%s AND user_id=%s",
        (gid, uid), commit=True,
    )

def get_all_activity(gid):
    """{user_id: (cycle_start_str, msg_count, warned_at_str)} for every tracked member."""
    rows = _run("SELECT user_id, cycle_start, msg_count, warned_at FROM mod_bot.last_activity WHERE guild_id=%s",
                (gid,), fetch="all")
    return {r["user_id"]: (r["cycle_start"], r["msg_count"], r["warned_at"]) for r in rows}

def mark_warned(gid, uid):
    """Record that the inactivity warning was just sent, so it isn't repeated every day
    until the member's cycle resets (passed) or ends (kicked)."""
    _run(
        f"UPDATE mod_bot.last_activity SET warned_at={_NOW} WHERE guild_id=%s AND user_id=%s",
        (gid, uid), commit=True,
    )

def remove_activity(gid, uid):
    _run("DELETE FROM mod_bot.last_activity WHERE guild_id=%s AND user_id=%s", (gid, uid), commit=True)
