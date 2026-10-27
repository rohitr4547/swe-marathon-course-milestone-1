import sqlite3, hashlib, secrets, os, re, json, datetime, time
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
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
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY,workspace_id INTEGER,code TEXT UNIQUE,expires_at REAL,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0,created_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r):
    return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def add_channel_member(c,cid,uid):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid)); return x.rowcount>0
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:add_channel_member(c,g['id'],uid)
def create_channel_row(c,wid,name,private,topic,creator):
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic))
    cid=c.execute("SELECT last_insert_rowid()").fetchone()[0]; add_channel_member(c,cid,creator); return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def create_workspace(uid,slug,name):
    with tx() as c:
        c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,uid)); wid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner')); ch=create_channel_row(c,wid,'general',0,'',uid)
        return c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(),ch
def join_channel(uid,cid):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch: raise KeyError()
        ws=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
        role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ws['id'],uid)).fetchone()
        cm=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
        if ch['is_private'] and not cm: raise PermissionError()
        if role and role['role']=='guest': raise PermissionError()
        if not role:
            if ws['join_mode']!='open': raise PermissionError()
            add_workspace_member(c,ws['id'],uid,'member')
        add_channel_member(c,cid,uid)
        return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def set_member_role(wslug,caller,target,role):
    with tx() as c:
        ws=c.execute('SELECT * FROM workspaces WHERE slug=?',(wslug,)).fetchone(); cr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ws['id'],caller)).fetchone()
        tr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ws['id'],target)).fetchone()
        if not tr: raise KeyError('target')
        if role not in ('admin','member','guest'): raise ValueError()
        if tr['role']=='owner' or (cr['role']=='admin' and tr['role']=='admin'): raise PermissionError()
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,ws['id'],target))
        return {'user_id':target,'role':role}
def transfer(wslug,caller,target):
    with tx() as c:
        ws=c.execute('SELECT * FROM workspaces WHERE slug=?',(wslug,)).fetchone()
        if ws['owner_id']!=caller: raise PermissionError()
        r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ws['id'],target)).fetchone()
        if not r or r['role']!='admin': raise ValueError()
        c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(ws['id'],caller))
        c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(ws['id'],target))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,ws['id']))
        return c.execute('SELECT * FROM workspaces WHERE id=?',(ws['id'],)).fetchone()
def invitation_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'code':r['code'],'expires_at':r['expires_at'],'max_uses':r['max_uses'],'uses':r['uses'],'invited_user_id':r['invited_user_id']}
def create_invite(wslug,caller,data):
    with tx() as c:
        ws=c.execute('SELECT * FROM workspaces WHERE slug=?',(wslug,)).fetchone()
        role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ws['id'],caller)).fetchone()['role']
        if role not in ('owner','admin'): raise PermissionError()
        target=data.get('invited_user_id')
        if 'invited_user_id' in data:
            if not isinstance(target,int): raise ValueError()
            if not c.execute('SELECT id FROM users WHERE id=?',(target,)).fetchone(): raise KeyError()
        if 'invited_username' in data:
            if not isinstance(data['invited_username'],str): raise ValueError()
            u=c.execute('SELECT id FROM users WHERE username=?',(data['invited_username'],)).fetchone()
            if not u: raise KeyError()
            target=u['id']
        exp=data.get('expires_in_seconds',None); mx=data.get('max_uses',1)
        if exp is not None and (not isinstance(exp,int) or isinstance(exp,bool) or exp<=0): raise ValueError()
        if not isinstance(mx,int) or isinstance(mx,bool) or mx<=0: raise ValueError()
        if target is not None: mx=1
        code=secrets.token_urlsafe(18); ea=time.time()+exp if exp is not None else None
        c.execute('INSERT INTO invitations(workspace_id,code,expires_at,max_uses,invited_user_id,created_at) VALUES(?,?,?,?,?,?)',(ws['id'],code,ea,mx,target,now_iso()))
        return c.execute('SELECT * FROM invitations WHERE id=?',(c.execute("SELECT last_insert_rowid()").fetchone()[0],)).fetchone()
def accept_invite(uid,code):
    with tx() as c:
        i=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0',(code,)).fetchone()
        if not i or (i['expires_at'] and i['expires_at']<=time.time()) or i['uses']>=i['max_uses']: raise KeyError()
        if i['invited_user_id'] and i['invited_user_id']!=uid: raise PermissionError()
        m=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(i['workspace_id'],uid)).fetchone()
        if not m:
            c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(i['id'],)); add_workspace_member(c,i['workspace_id'],uid,'member')
        ws=c.execute('SELECT * FROM workspaces WHERE id=?',(i['workspace_id'],)).fetchone(); return ws
def init_main():
    init_db()
if __name__=='__main__' and '--init-db' in os.sys.argv: init_db()
