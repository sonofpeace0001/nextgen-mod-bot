"""SQLite persistence for all bot data."""
import sqlite3, threading

_DB = "moderation.db"
_local = threading.local()

def _conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(_DB)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
    return _local.conn

def init_db():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER,
        moderator TEXT, reason TEXT, timestamp TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS mod_log (
        id INTEGER PRIMARY KEY, guild_id INTEGER, action TEXT,
        target_id INTEGER, moderator TEXT, reason TEXT,
        timestamp TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS spam_exempt (user_id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS tempbans (
        id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER,
        moderator TEXT, reason TEXT, unban_at TEXT
    );
    CREATE TABLE IF NOT EXISTS appeals (
        id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER,
        reason TEXT, status TEXT DEFAULT 'pending',
        message_id INTEGER DEFAULT 0, timestamp TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER,
        moderator TEXT, content TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY, guild_id INTEGER, reporter_id INTEGER,
        target_id INTEGER, reason TEXT, message_content TEXT DEFAULT '',
        message_url TEXT DEFAULT '', status TEXT DEFAULT 'open',
        message_id INTEGER DEFAULT 0, timestamp TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS reaction_roles (
        id INTEGER PRIMARY KEY, guild_id INTEGER, channel_id INTEGER,
        message_id INTEGER, emoji TEXT, role_id INTEGER,
        description TEXT DEFAULT '', created_by TEXT
    );
    CREATE TABLE IF NOT EXISTS ignored_channels (
        channel_id INTEGER PRIMARY KEY, guild_id INTEGER,
        added_by TEXT, added_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS welcomed_users (
        user_id INTEGER PRIMARY KEY, guild_id INTEGER,
        awaiting_reply INTEGER DEFAULT 1, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS member_level (
        user_id INTEGER PRIMARY KEY, level TEXT DEFAULT 'novice'
    );
    CREATE TABLE IF NOT EXISTS kv_store (
        key TEXT PRIMARY KEY, value TEXT
    );
    CREATE TABLE IF NOT EXISTS last_activity (
        user_id INTEGER, guild_id INTEGER, last_seen TEXT, warned_at TEXT,
        cycle_start TEXT, msg_count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, guild_id)
    );
    """)
    c.commit()
    # Migration: last_activity may already exist (from an earlier deploy of this feature)
    # without one or more of these columns. CREATE TABLE IF NOT EXISTS above won't add
    # columns to an already-existing table, so add each one, ignoring "already exists".
    for _col, _decl in (("warned_at", "TEXT"), ("cycle_start", "TEXT"), ("msg_count", "INTEGER DEFAULT 0")):
        try:
            c.execute(f"ALTER TABLE last_activity ADD COLUMN {_col} {_decl}")
            c.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    # Load ignored channels from DB into config at startup
    _load_ignored_channels()

def add_warning(gid, uid, mod, reason):
    c = _conn(); c.execute("INSERT INTO warnings (guild_id,user_id,moderator,reason) VALUES (?,?,?,?)", (gid,uid,mod,reason)); c.commit()
    return c.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?", (gid,uid)).fetchone()[0]
def get_warnings(gid, uid):
    return [dict(r) for r in _conn().execute("SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id", (gid,uid)).fetchall()]
def clear_warnings(gid, uid):
    c = _conn(); c.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (gid,uid)); c.commit()
def log_action(gid, action, tid, mod, reason=""):
    c = _conn(); c.execute("INSERT INTO mod_log (guild_id,action,target_id,moderator,reason) VALUES (?,?,?,?,?)", (gid,action,tid,mod,reason)); c.commit()
def get_recent_log(gid, limit=10):
    return [dict(r) for r in _conn().execute("SELECT * FROM mod_log WHERE guild_id=? ORDER BY id DESC LIMIT ?", (gid,limit)).fetchall()]
def get_user_mod_history(gid, uid):
    return [dict(r) for r in _conn().execute("SELECT * FROM mod_log WHERE guild_id=? AND target_id=? ORDER BY id DESC", (gid,uid)).fetchall()]
def is_spam_exempt(uid):
    return _conn().execute("SELECT 1 FROM spam_exempt WHERE user_id=?", (uid,)).fetchone() is not None
def add_tempban(gid, uid, mod, reason, unban_at):
    c = _conn(); c.execute("INSERT INTO tempbans (guild_id,user_id,moderator,reason,unban_at) VALUES (?,?,?,?,?)", (gid,uid,mod,reason,unban_at.isoformat())); c.commit()
def remove_tempban(gid, uid):
    c = _conn(); c.execute("DELETE FROM tempbans WHERE guild_id=? AND user_id=?", (gid,uid)); c.commit()
def get_pending_tempbans():
    return [dict(r) for r in _conn().execute("SELECT * FROM tempbans").fetchall()]
def get_active_tempban(gid, uid):
    r = _conn().execute("SELECT * FROM tempbans WHERE guild_id=? AND user_id=?", (gid,uid)).fetchone()
    return dict(r) if r else None
def add_appeal(gid, uid, reason):
    c = _conn(); c.execute("INSERT INTO appeals (guild_id,user_id,reason) VALUES (?,?,?)", (gid,uid,reason)); c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]
def get_appeal(aid):
    r = _conn().execute("SELECT * FROM appeals WHERE id=?", (aid,)).fetchone()
    return dict(r) if r else None
def update_appeal_status(aid, status):
    c = _conn(); c.execute("UPDATE appeals SET status=? WHERE id=?", (status,aid)); c.commit()
def update_appeal_message(aid, mid):
    c = _conn(); c.execute("UPDATE appeals SET message_id=? WHERE id=?", (mid,aid)); c.commit()
def get_user_appeals(gid, uid):
    return [dict(r) for r in _conn().execute("SELECT * FROM appeals WHERE guild_id=? AND user_id=? ORDER BY id DESC", (gid,uid)).fetchall()]
def get_pending_appeals():
    return [dict(r) for r in _conn().execute("SELECT * FROM appeals WHERE status='pending'").fetchall()]
def add_note(gid, uid, mod, content):
    c = _conn(); c.execute("INSERT INTO notes (guild_id,user_id,moderator,content) VALUES (?,?,?,?)", (gid,uid,mod,content)); c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]
def get_note(nid, gid):
    r = _conn().execute("SELECT * FROM notes WHERE id=? AND guild_id=?", (nid,gid)).fetchone()
    return dict(r) if r else None
def edit_note(nid, gid, content):
    c = _conn(); c.execute("UPDATE notes SET content=? WHERE id=? AND guild_id=?", (content,nid,gid)); c.commit()
def delete_note(nid, gid):
    c = _conn(); c.execute("DELETE FROM notes WHERE id=? AND guild_id=?", (nid,gid)); c.commit()
def get_user_notes(gid, uid):
    return [dict(r) for r in _conn().execute("SELECT * FROM notes WHERE guild_id=? AND user_id=? ORDER BY id DESC", (gid,uid)).fetchall()]
def add_report(gid, reporter, target, reason, msg_content="", msg_url=""):
    c = _conn(); c.execute("INSERT INTO reports (guild_id,reporter_id,target_id,reason,message_content,message_url) VALUES (?,?,?,?,?,?)", (gid,reporter,target,reason,msg_content,msg_url)); c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]
def get_report(rid):
    r = _conn().execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return dict(r) if r else None
def update_report_status(rid, status):
    c = _conn(); c.execute("UPDATE reports SET status=? WHERE id=?", (status,rid)); c.commit()
def update_report_message(rid, mid):
    c = _conn(); c.execute("UPDATE reports SET message_id=? WHERE id=?", (mid,rid)); c.commit()
def get_pending_reports():
    return [dict(r) for r in _conn().execute("SELECT * FROM reports WHERE status='open'").fetchall()]
def get_reports_against(gid, uid):
    return [dict(r) for r in _conn().execute("SELECT * FROM reports WHERE guild_id=? AND target_id=? ORDER BY id DESC", (gid,uid)).fetchall()]
def add_reaction_role(gid, cid, mid, emoji, rid, desc, created_by):
    c = _conn(); c.execute("INSERT INTO reaction_roles (guild_id,channel_id,message_id,emoji,role_id,description,created_by) VALUES (?,?,?,?,?,?,?)", (gid,cid,mid,emoji,rid,desc,created_by)); c.commit()
def get_reaction_role(mid, emoji):
    r = _conn().execute("SELECT * FROM reaction_roles WHERE message_id=? AND emoji=?", (mid,emoji)).fetchone()
    return dict(r) if r else None
def get_all_reaction_roles(gid):
    return [dict(r) for r in _conn().execute("SELECT * FROM reaction_roles WHERE guild_id=?", (gid,)).fetchall()]
def remove_reaction_role(gid, mid, emoji):
    c = _conn(); cur = c.execute("DELETE FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?", (gid,mid,emoji)); c.commit(); return cur.rowcount > 0
def remove_reaction_roles_for_message(gid, mid):
    c = _conn(); cur = c.execute("DELETE FROM reaction_roles WHERE guild_id=? AND message_id=?", (gid,mid)); c.commit(); return cur.rowcount

# ── Ignored Channels (persistent) ─────────────────────────────────

def _load_ignored_channels():
    """Load ignored channels from DB into config.IGNORED_CHANNEL_IDS."""
    import config
    try:
        rows = _conn().execute("SELECT channel_id FROM ignored_channels").fetchall()
        for r in rows:
            config.IGNORED_CHANNEL_IDS.add(r[0])
    except:
        pass

def add_ignored_channel(gid, cid, added_by):
    import config
    c = _conn()
    c.execute("INSERT OR IGNORE INTO ignored_channels (channel_id, guild_id, added_by) VALUES (?,?,?)", (cid, gid, added_by))
    c.commit()
    config.IGNORED_CHANNEL_IDS.add(cid)

def remove_ignored_channel(gid, cid):
    import config
    c = _conn()
    cur = c.execute("DELETE FROM ignored_channels WHERE channel_id=? AND guild_id=?", (cid, gid))
    c.commit()
    config.IGNORED_CHANNEL_IDS.discard(cid)
    return cur.rowcount > 0

def get_ignored_channels(gid):
    return [dict(r) for r in _conn().execute("SELECT * FROM ignored_channels WHERE guild_id=?", (gid,)).fetchall()]

# ── Welcome DM tracking (Task 1) ──────────────────────────────────

def add_welcomed_user(uid, gid):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO welcomed_users (user_id, guild_id, awaiting_reply, created_at) "
        "VALUES (?,?,1,datetime('now'))",
        (uid, gid),
    )
    c.commit()

def get_welcomed_user(uid):
    r = _conn().execute("SELECT * FROM welcomed_users WHERE user_id=?", (uid,)).fetchone()
    return dict(r) if r else None

def clear_awaiting_reply(uid):
    c = _conn(); c.execute("UPDATE welcomed_users SET awaiting_reply=0 WHERE user_id=?", (uid,)); c.commit()

# ── Member skill level (Task 5) ───────────────────────────────────

def set_member_level(uid, level):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO member_level (user_id, level) VALUES (?,?)", (uid, level))
    c.commit()

def get_member_level(uid):
    r = _conn().execute("SELECT level FROM member_level WHERE user_id=?", (uid,)).fetchone()
    return r[0] if r else None

# ── Tiny key/value store (Task 2 prompt index, etc.) ──────────────

def kv_get(key, default=None):
    r = _conn().execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    return r[0] if r else default

def kv_set(key, value):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO kv_store (key, value) VALUES (?,?)", (key, str(value)))
    c.commit()

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
    c = _conn()
    c.execute(
        "INSERT INTO last_activity (user_id, guild_id, last_seen, cycle_start, msg_count, warned_at) "
        "VALUES (?,?,datetime('now'),datetime('now'),1,NULL) "
        "ON CONFLICT(user_id, guild_id) DO UPDATE SET "
        "last_seen=datetime('now'), msg_count=msg_count+1, "
        "cycle_start=COALESCE(cycle_start, datetime('now'))",
        (uid, gid),
    )
    c.commit()

def seed_cycle(gid, uid):
    """Start a member's first cycle at zero messages. No-op if they already have a row,
    so this never clobbers progress -- safe to call unconditionally on join or at startup."""
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO last_activity (user_id, guild_id, last_seen, cycle_start, msg_count, warned_at) "
        "VALUES (?,?,datetime('now'),datetime('now'),0,NULL)",
        (uid, gid),
    )
    c.commit()

def reset_cycle(gid, uid):
    """Member passed their cycle (hit the message quota in time): start a fresh one."""
    c = _conn()
    c.execute(
        "UPDATE last_activity SET cycle_start=datetime('now'), msg_count=0, warned_at=NULL "
        "WHERE guild_id=? AND user_id=?",
        (gid, uid),
    )
    c.commit()

def get_all_activity(gid):
    """{user_id: (cycle_start_str, msg_count, warned_at_str)} for every tracked member."""
    return {r[0]: (r[1], r[2], r[3]) for r in _conn().execute(
        "SELECT user_id, cycle_start, msg_count, warned_at FROM last_activity WHERE guild_id=?", (gid,)
    ).fetchall()}

def mark_warned(gid, uid):
    """Record that the inactivity warning was just sent, so it isn't repeated every day
    until the member's cycle resets (passed) or ends (kicked)."""
    c = _conn()
    c.execute(
        "UPDATE last_activity SET warned_at=datetime('now') WHERE guild_id=? AND user_id=?",
        (gid, uid),
    )
    c.commit()

def remove_activity(gid, uid):
    c = _conn(); c.execute("DELETE FROM last_activity WHERE guild_id=? AND user_id=?", (gid, uid)); c.commit()
