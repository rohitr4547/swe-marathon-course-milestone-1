import sqlite3, hashlib, secrets, re, json, base64, datetime, os, argparse
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY,workspace_id INTEGER,code TEXT UNIQUE,created_at TEXT,expires_at TEXT,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,uid,now_iso())); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r):
    return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_channel_member(c,cid,uid):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid)); return x.rowcount>0
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g: add_channel_member(c,g['id'],uid)
def channel_to_json(r):
    return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def create_channel_row(c,wid,name,private,topic,uid):
    x=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic or ''))
    cid=x.lastrowid; add_channel_member(c,cid,uid); return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':cid}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return n
def workspace_detail(wid,uid):
    c=connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
    chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(wid,)).fetchall(); c.close()
    return workspace_to_json(w),[channel_to_json(x) for x in chans]
def get_channel_members(cid):
    c=connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,)).fetchall(); c.close(); return [user_to_json(x) for x in rows]
def invitation_to_json(r):
    return {k:r[k] for k in ('id','workspace_id','code','created_at','expires_at','max_uses','uses','invited_user_id')}
def init_user_workspace(c,uid,slug,name):
    x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,uid)); wid=x.lastrowid
    cur=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?,'general')",(wid,)); gid=cur.lastrowid
    c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner')); c.execute('INSERT INTO channel_members VALUES(?,?)',(gid,uid))
    return c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(),c.execute('SELECT * FROM channels WHERE id=?',(gid,)).fetchone()
def find_user(x):
    c=connect(); r=c.execute('SELECT * FROM users WHERE id=? OR username=?',(x,x)).fetchone(); c.close(); return r
if __name__=='__main__':
    init_db()
