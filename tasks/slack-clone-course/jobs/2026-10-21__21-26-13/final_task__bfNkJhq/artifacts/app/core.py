import sqlite3, os, hashlib, secrets, base64, json, re
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
def init_db():
    os.makedirs('/app/data',exist_ok=True)
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
    '''); c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def hash_password(pw):
    s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:s,h=stored.split('$',1); return hashlib.sha256((s+pw).encode()).hexdigest()==h
    except:return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member_role(wid,uid):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); c.close(); return r['role'] if r else None
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],uid))
def create_channel_row(c,wid,name,private,topic,creator):
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(private),topic))
    r=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone()
    c.execute('INSERT INTO channel_members VALUES(?,?)',(r['id'],creator)); return r
def channel_to_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def add_channel_member(c,cid,uid):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,uid)); return x.rowcount>0
def remove_channel_member(c,cid,uid):
    x=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)); return x.rowcount>0
def emit_event(c,cid,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':cid}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame))); return n
def join_channel(user,cid):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch: raise KeyError()
        role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],user['id'])).fetchone()
        if ch['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,user['id'])).fetchone(): raise PermissionError()
        if not role:
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            if w['join_mode']!='open' or role and role['role']=='guest': raise PermissionError()
            add_workspace_member(c,w['id'],user['id'],'member')
        if add_channel_member(c,cid,user['id']): emit_event(c,cid,'member.joined',{'user_id':user['id']})
    return get_channel(cid)
def can_read_channel(uid,ch):
    c=connect(); m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],uid)).fetchone(); c.close()
    return bool(m or (not ch['is_private'] and role and role['role']!='guest'))
def require_writable(ch):
    if ch['is_archived']: raise RuntimeError('archived')
def validate_topic(t):
    if not isinstance(t,str) or len(t)>250: raise ValueError('invalid topic')
def set_archived(actor,cid,flag):
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch: raise KeyError()
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
        if w['owner_id']!=actor['id']: raise PermissionError()
        if bool(ch['is_archived'])!=flag:
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(flag),cid)); emit_event(c,cid,'channel.archived' if flag else 'channel.unarchived',{})
    return get_channel(cid)
def workspace_detail(user,slug,include_archived=False):
    w=get_workspace(slug)
    if not w: raise KeyError()
    if not member_role(w['id'],user['id']): raise PermissionError()
    c=connect(); q='SELECT * FROM channels WHERE workspace_id=? AND (? OR is_archived=0)'; rows=c.execute(q,(w['id'],int(include_archived))).fetchall(); rows=[r for r in rows if not r['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['id'],user['id'])).fetchone()]
    rs=[] 
    for r in rows:
        rs.append(read_state(user['id'],r['id']))
    c.close(); return w,[channel_to_json(r) for r in rows],rs
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':int(v)}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x['k']==k and isinstance(x['v'],int); return x['v']
    except: raise ValueError('cursor')
def parse_paging(q,k):
    if 'page' in q or 'offset' in q: raise ValueError('offset')
    try:n=int(q.get('limit','50')); assert 1<=n<=200
    except: raise ValueError('limit')
    return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
def message_to_json(r):
    c=connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); rr=c.execute('SELECT * FROM reactions WHERE message_id=?',(r['id'],)).fetchall(); reps=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']; c.close()
    em={}
    for x in rr: em.setdefault(x['emoji'],[]).append(x['user_id'])
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in em.items()],'mentions':[]}
def create_message(uid,cid,body,parent=None):
    if not body: raise ValueError('empty')
    with tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not ch: raise KeyError()
        if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone() and not can_read_channel(uid,ch): raise PermissionError()
        require_writable(ch); t=now_iso(); c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,uid,body,parent,t)); r=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone(); emit_event(c,cid,'message.reply' if parent else 'message.created',{'message':message_to_json(r)})
    return message_to_json(r)
def list_messages(cid,n,cur,reply=False):
    c=connect(); op='>' if reply else '<'; where='parent_id=? AND deleted=0' if reply else 'channel_id=? AND parent_id IS NULL AND deleted=0'; args=[cid]; 
    if cur is not None: where+=' AND id '+op+' ?'; args.append(cur)
    order='ASC' if reply else 'DESC'; rows=c.execute(f'SELECT * FROM messages WHERE {where} ORDER BY id {order} LIMIT ?',args+[n+1]).fetchall(); c.close(); more=len(rows)>n; rows=rows[:n]; return [message_to_json(r) for r in rows],(encode_cursor('rep' if reply else 'msg',rows[-1]['id']) if more and rows else None)
def head_event_id(cid):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(cid,since):
    c=connect(); r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close(); return [json.loads(x['payload']) for x in r]
def init(): init_db()
if __name__=='__main__' and '--init-db' in os.sys.argv:init_db()

def get_workspace_by_id(i):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(i,)).fetchone(); c.close(); return r

def resolve_mentions(c, body, channel, author_id):
    ids=[]
    members=[x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(channel['id'],)).fetchall()]
    if re.search(r'(?<![\w@])@channel(?![\w])',body):
        ids.extend(x for x in members if x!=author_id)
    names=set(re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)(?![\w@])',body))
    for name in names:
        if name=='channel': continue
        u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
        if u and u['id']!=author_id and u['id'] in members: ids.append(u['id'])
    return list(dict.fromkeys(ids))

def read_state(user_id, channel_id):
    c=connect()
    ch=c.execute('SELECT * FROM channels WHERE id=?',(channel_id,)).fetchone()
    z=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(user_id,channel_id)).fetchone()
    last=z['last_read_event_id'] if z else 0
    rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel_id,last)).fetchall()
    unread=mention=0
    for row in rows:
        e=json.loads(row['payload'])
        if e.get('type') not in ('message.created','message.reply'): continue
        m=e.get('message') or {}
        if m.get('author_id')==user_id: continue
        unread+=1
        if user_id in (m.get('mentions') or []): mention+=1
    c.close()
    return {'channel_id':channel_id,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}

_old_message_to_json=message_to_json
def message_to_json(r):
    c=connect()
    u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rr=c.execute('SELECT * FROM reactions WHERE message_id=?',(r['id'],)).fetchall()
    reps=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    ids=resolve_mentions(c,r['body'],ch,r['author_id'])
    c.close()
    em={}
    for x in rr: em.setdefault(x['emoji'],[]).append(x['user_id'])
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in em.items()],'mentions':ids}
