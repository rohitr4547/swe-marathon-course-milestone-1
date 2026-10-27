import sqlite3, os, hashlib, secrets, base64, json, datetime, re, argparse
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
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    '''); c.close()
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r):
    return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_channel_member(c,cid,uid):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid)); return x.rowcount>0
def create_channel_row(c,wid,name,private,topic,uid):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic))
    cid=cur.lastrowid; add_channel_member(c,cid,uid); return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:add_channel_member(c,g['id'],uid)
def emit_event(c,cid,kind,payload):
    r=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()
    n=r['last_event_id']; frame={'type':kind,'event_id':n,'channel_id':cid}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return n
def encode_cursor(kind,v):
    return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(kind,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); 
        if x.get('k')!=kind or type(x.get('v')) is not int: raise ValueError()
        return x['v']
    except: raise ValueError()
if __name__=='__main__':
    if '--init-db' in __import__('sys').argv:init_db()
