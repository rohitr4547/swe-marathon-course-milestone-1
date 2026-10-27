import sqlite3, os, hashlib, secrets, json, base64, re
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class tx:
    def __enter__(self):
        self.c=connect();self.c.execute('BEGIN IMMEDIATE');return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT');self.c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect();c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
    ''');c.close()
def hash_password(pw):
    s=secrets.token_hex(16);return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:s,h=stored.split('$',1);return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except:return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
    if not t:return None
    c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_to_json(r):
    return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_workspace(slug):
    c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def get_channel(cid):
    c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close();return r
def member_role(wid,uid):
    c=connect();r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone();c.close();return r['role'] if r else None
def add_channel_member(c,cid,uid):
    n=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid)).rowcount;return n>0
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(wid,uid,role))
    g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(wid,)).fetchone()
    if g:add_channel_member(c,g['id'],uid)
def create_channel_row(c,wid,name,private,topic,creator):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic))
    cid=cur.lastrowid;add_channel_member(c,cid,creator);return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def can_read_channel(uid,ch):
    c=connect()
    ok=bool(c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone())
    if not ok and not ch['is_private']:
        ok=bool(c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],uid)).fetchone())
    c.close();return ok
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':cid,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame)));return n
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4)));assert isinstance(x,dict) and x['k']==k and isinstance(x['v'],int) and not isinstance(x['v'],bool);return x['v']
    except: raise ValueError()
def parse_paging(q,k):
    lim=int(q.get('limit','50')); assert 1<=lim<=200
    cur=q.get('cursor');return lim,(decode_cursor(k,cur) if cur else None)
def reactions_for(mid):
    c=connect();rs=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)).fetchall();c.close();d={}
    for r in rs:d.setdefault(r['emoji'],[]).append(r['user_id'])
    return [{'emoji':e,'count':len(u),'user_ids':u} for e,u in d.items()]
def message_to_json(r):
    c=connect();u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':count_replies(r['id']),'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions_for(r['id']),'mentions':json.loads(r['mentions'] or '[]')}
def count_replies(mid):
    c=connect();n=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(mid,)).fetchone()[0];c.close();return n
def resolve_mentions(c,body,ch,author):
    out=[]
    names=re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body)
    if 'channel' in names:
        out.extend(r['user_id'] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author)).fetchall())
    for name in names:
        if name.lower() in ('channel','here'): continue
        u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
        if u and u['id']!=author and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(): out.append(u['id'])
    return list(dict.fromkeys(out))
def create_message(uid,cid,body,parent=None):
    if not isinstance(body,str) or not body.strip(): raise ValueError('body')
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch: raise PermissionError()
        joined=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
        workspace_member=c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],uid)).fetchone()
        if not joined and (ch['is_private'] or not workspace_member): raise PermissionError()
        if ch['is_archived']: raise RuntimeError()
        mentions=resolve_mentions(c,body,ch,uid);t=now_iso()
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,uid,body,parent,t,json.dumps(mentions)))
        mid=cur.lastrowid
        kind='message.reply' if parent else 'message.created'
        event_message={'id':mid,'channel_id':cid,'author_id':uid,'body':body,'parent_id':parent,'reply_count':0,'created_at':t,'edited_at':None,'files':[],'reactions':[],'mentions':mentions}
        emit_event(c,cid,kind,{'message':event_message})
        return c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
def read_state(uid,cid):
    c=connect();x=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone();last=x[0] if x else 0
    rows=c.execute('SELECT e.event_id,m.author_id,m.mentions FROM events e JOIN messages m ON json_extract(e.payload,"$.message.id")=m.id WHERE e.channel_id=? AND e.event_id>? AND m.deleted=0',(cid,last)).fetchall()
    unread=sum(r['author_id']!=uid for r in rows);mentions=sum(r['author_id']!=uid and uid in json.loads(r['mentions'] or '[]') for r in rows);c.close()
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}
if __name__=='__main__' and '--init-db' in os.sys.argv:init_db()
def get_message(mid):
    c=connect();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close();return r
def get_workspace_by_id(wid):
    c=connect();r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();c.close();return r
def events_after(cid,since):
    c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall();c.close();return [json.loads(x[0]) for x in r]
def head_event_id(cid):
    c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone();c.close();return r[0] if r else 0
