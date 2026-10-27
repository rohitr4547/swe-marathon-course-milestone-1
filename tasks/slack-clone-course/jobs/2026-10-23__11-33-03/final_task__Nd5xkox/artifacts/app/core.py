import sqlite3, os, hashlib, secrets, datetime, re, json, base64, time
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000'); return c
class Tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def tx(): return Tx()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,max_uses INTEGER,uses INTEGER DEFAULT 0,expires_at TEXT,created_at TEXT,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    '''); c.close()
def hash_password(p):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+p).encode()).hexdigest()
def verify_password(p,x):
    try: s,h=x.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+p).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    d={k:r[k] for k in ('id','workspace_id','name','topic','last_event_id')}; d.update(is_private=bool(r['is_private']),is_dm=bool(r['is_dm']),is_archived=bool(r['is_archived'])); return d
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_channel_member(c,cid,uid):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid)); return x.rowcount>0
def add_workspace_member(c,wid,uid,role):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g: add_channel_member(c,g['id'],uid)
def create_channel_row(c,wid,name,private,topic,uid):
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic))
    r=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); add_channel_member(c,r['id'],uid); return r
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
    p={'type':kind,'event_id':n,'channel_id':cid,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(p))); return n
def join_channel(user,cid):
    ch=get_channel(cid)
    if not ch: return 404,'unknown channel'
    role=member_role(ch['workspace_id'],user['id']); ws=get_workspace_by_id(ch['workspace_id'])
    if ch['is_private']:
        c=connect(); cm=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,user['id'])).fetchone(); c.close()
        if not cm:return 403,'private channel'
    if role=='guest':return 403,'guest'
    if role is None and ws['join_mode']=='invite_only':return 403,'invite only'
    with tx() as c:
        if role is None:add_workspace_member(c,ch['workspace_id'],user['id'],'member')
        add_channel_member(c,cid,user['id'])
    return 200,None
def get_workspace_by_id(i):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(i,)).fetchone(); c.close(); return r
def invitation_to_json(r): return {k:r[k] for k in ('code','workspace_id','invited_user_id','max_uses','uses','expires_at','created_at')}
def create_invitation(actor,slug,b):
    ws=get_workspace(slug); role=member_role(ws['id'],actor['id'])
    if role not in ('owner','admin'): return 403,None
    if not isinstance(b,dict):return 400,None
    for k in ('expires_in_seconds','max_uses'):
        if k in b and (not isinstance(b[k],int) or isinstance(b[k],bool) or b[k]<=0):return 400,None
    target=b.get('invited_user_id'); name=b.get('invited_username')
    if target is not None and (not isinstance(target,int) or not user_for_id(target)):return 404,None
    if name is not None:
        if not isinstance(name,str):return 400,None
        u=user_by_name(name)
        if not u:return 404,None
        target=u['id']
    if any(k in b and not isinstance(b[k],(int,str)) for k in ('invited_user_id','invited_username')):return 400,None
    uses=1 if target else b.get('max_uses',1); exp=None
    if 'expires_in_seconds' in b: exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=b['expires_in_seconds'])).isoformat()
    code=secrets.token_urlsafe(18)
    with tx() as c:c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,0)',(code,ws['id'],target,uses,0,exp,now_iso()))
    return 201,invitation_to_json(connect().execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone())
def user_for_id(i):
    c=connect();r=c.execute('SELECT * FROM users WHERE id=?',(i,)).fetchone();c.close();return r
def user_by_name(n):
    c=connect();r=c.execute('SELECT * FROM users WHERE username=?',(n,)).fetchone();c.close();return r
def accept_invitation(u,code):
    c=connect(); inv=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0',(code,)).fetchone()
    if not inv:return 404,None
    ws=get_workspace_by_id(inv['workspace_id']); role=member_role(ws['id'],u['id'])
    if role:return 200,workspace_to_json(ws)
    if inv['expires_at'] and inv['expires_at']<=datetime.datetime.now(datetime.timezone.utc).isoformat():return 404,None
    if inv['uses']>=inv['max_uses']:return 404,None
    if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return 403,None
    with tx() as t:
        t.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,));add_workspace_member(t,ws['id'],u['id'],'member')
    return 200,workspace_to_json(ws)
if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--init-db',action='store_true')
    p.parse_args()
    init_db()
