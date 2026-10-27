import sqlite3, os, hashlib, secrets, json, base64, re
from datetime import datetime, timezone
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
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,created_at TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    '''); c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try: s,h=stored.split('$'); return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except: return False
def issue_token(c,user_id):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,user_id)); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_channel_member(c,cid,uid):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid)); return x.rowcount>0
def add_workspace_member(c,wid,uid,role='member'):
    x=c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g: add_channel_member(c,g['id'],uid)
    return x.rowcount>0
def create_channel_row(c,wid,name,private,topic,creator):
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic))
    r=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); add_channel_member(c,r['id'],creator); return r
def emit_event(c,cid,kind,payload):
    c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(cid,))
    n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':cid}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return n
def can_read_channel(uid,ch):
    role=member_role(ch['workspace_id'],uid)
    if not role:return False
    if ch['is_private']:
        c=connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close(); return bool(x)
    return role!='guest'
def require_writable(ch):
    if ch['is_archived']: raise ValueError('archived')
def resolve_mentions(c,body,ch,author):
    out=[]
    for name in re.findall(r'(?<![\w@.])@(\w+)',body):
        if name in ('channel','here'): 
            rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)).fetchall()
            ids=[x[0] for x in rows if x[0]!=author]
        else:
            u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone(); ids=[u[0]] if u and u[0]!=author and can_read_channel(u[0],ch) else []
        for x in ids:
            if x not in out: out.append(x)
    return out
def message_to_json(r):
    c=connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(created_at)',(r['id'],)).fetchall()
    n=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':json.loads(r['mentions'] or '[]')}
def create_message(uid,cid,body,parent=None):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch: raise LookupError()
        if ch['is_archived']: raise ValueError('archived')
        if parent:
            p=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(parent,)).fetchone()
            if not p or p['channel_id']!=cid or p['parent_id'] is not None: raise ValueError('parent')
        mentions=resolve_mentions(c,body,ch,uid); t=now_iso()
        c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,uid,body,parent,t,json.dumps(mentions)))
        r=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
        emit_event(c,cid,'message.reply' if parent else 'message.created',{'message':message_to_json(r)})
        return message_to_json(r)
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x['k']==k and isinstance(x['v'],int); return x['v']
    except: raise ValueError('cursor')
def parse_paging(q,k):
    if 'offset' in q or 'page' in q: raise ValueError('paging')
    lim=int(q.get('limit','50'))
    if lim<1 or lim>200: raise ValueError('limit')
    return lim,decode_cursor(k,q['cursor']) if q.get('cursor') else None
