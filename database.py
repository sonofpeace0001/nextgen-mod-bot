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
    """)
    c.commit()
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
