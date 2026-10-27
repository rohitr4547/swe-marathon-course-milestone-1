import sqlite3, os, hashlib, secrets, json, datetime, re, base64, time
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class tx:
 def __enter__(self):
  self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
 def __exit__(self,a,b,d):
  self.c.execute('ROLLBACK' if a else 'COMMIT'); self.c.close()
def now_iso():
 return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,max_uses INTEGER,uses INTEGER DEFAULT 0,expires_at TEXT,created_at TEXT,revoked INTEGER DEFAULT 0);'''); c.close()
def hash_password(p):
 s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+p).encode()).hexdigest()
def verify_password(p,x):
 try:
  s,h=x.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+p).encode()).hexdigest())
 except: return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
 if not t:return None
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
 return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_workspace(slug):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def create_channel_row(c,w,name,private,topic,creator):
 cur=cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic))
 i=cur.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(i,creator)); return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def init_general(c,w,u):
 return create_channel_row(c,w,'general',0,'',u)
def invitation_to_json(r): return {k:r[k] for k in ('code','workspace_id','invited_user_id','max_uses','uses','expires_at','created_at')}
def valid_inv(r):
 return r and not r['revoked'] and r['uses']<r['max_uses'] and (not r['expires_at'] or r['expires_at']>now_iso())
if __name__=='__main__' and '--init-db' in __import__('sys').argv:init_db()
