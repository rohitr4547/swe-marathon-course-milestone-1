import sqlite3, hashlib, secrets, datetime, json, base64, re, os, contextlib
DB='/app/data/huddle.db'
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
@contextlib.contextmanager
def tx():
    c=connect()
    try: c.execute('BEGIN IMMEDIATE'); yield c; c.execute('COMMIT')
    except: c.execute('ROLLBACK'); raise
    finally: c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    try: c.execute('ALTER TABLE invitations ADD COLUMN created_at TEXT')
    except: pass
    try: c.execute('ALTER TABLE invitations ADD COLUMN revoked INTEGER DEFAULT 0')
    except: pass
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY,workspace_id INTEGER,code TEXT UNIQUE,invited_user_id INTEGER,expires_at TEXT,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,created_at TEXT,revoked INTEGER DEFAULT 0);
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
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_channel_member(c,ch,u):
    x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u)); return x.rowcount>0
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g: add_channel_member(c,g['id'],u)
def create_channel_row(c,w,name,private,topic,creator):
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic or ''))
    r=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); add_channel_member(c,r['id'],creator); return r
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':ch}; frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return n
def can_read_channel(uid,ch):
    c=connect(); m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone()
    role=member_role(ch['workspace_id'],uid); c.close()
    return bool(m) or (not ch['is_private'] and role is not None)
def join_channel(user,chid):
    ch=get_channel(chid)
    if not ch: raise KeyError()
    role=member_role(ch['workspace_id'],user['id']); w=get_workspace_by_id(ch['workspace_id'])
    if ch['is_private'] or role=='guest' or (role is None and w['join_mode']=='invite_only'): raise PermissionError()
    with tx() as c:
        if not role: add_workspace_member(c,ch['workspace_id'],user['id'])
        added=add_channel_member(c,chid,user['id'])
        if added: emit_event(c,chid,'member.joined',{'user_id':user['id']})
    return ch
def get_workspace_by_id(i):
    c=connect();r=c.execute('SELECT * FROM workspaces WHERE id=?',(i,)).fetchone();c.close();return r
def workspace_detail(w,u,arch=False):
    c=connect(); chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (? OR is_archived=0)',(w['id'],int(arch))).fetchall()
    chans=[x for x in chans if not x['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u)).fetchone()]
    states=[read_state(u,x['id']) for x in chans]; c.close()
    return workspace_to_json(w),[channel_to_json(x) for x in chans],states
def resolve_mentions(c,body,ch,author):
    ids=[]; names=re.findall(r'(?<![\w@.])@(\w+)',body)
    for n in names:
        if n=='channel':
            rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author)).fetchall()
            ids += [x['user_id'] for x in rows]
        else:
            x=c.execute('SELECT u.id FROM users u JOIN members m ON m.user_id=u.id WHERE u.username=? AND m.workspace_id=?',(n,ch['workspace_id'])).fetchone()
            if x and x['id']!=author and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],x['id'])).fetchone(): ids.append(x['id'])
    return list(dict.fromkeys(ids))
def msg_json(r):
    c=connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rs=c.execute('SELECT emoji, count(*) n, group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall(); c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':sum(1 for _ in []) if False else _reply_count(r['id']),'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs],'mentions':json.loads(r['mentions'] or '[]')}
def _reply_count(i):
 c=connect();n=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(i,)).fetchone()['n'];c.close();return n
def create_message(user,chid,body,parent=None):
 ch=get_channel(chid)
 if not ch: raise KeyError()
 if not can_read_channel(user['id'],ch): raise PermissionError()
 if ch['is_archived']: raise RuntimeError()
 if not isinstance(body,str) or not body.strip(): raise ValueError()
 with tx() as c:
  men=resolve_mentions(c,body,ch,user['id']); c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(chid,user['id'],body,parent,now_iso(),json.dumps(men)))
  r=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone(); kind='message.reply' if parent else 'message.created'; emit_event(c,chid,kind,{'message':msg_json(r)})
 return r
def read_state(u,chid):
    c=connect()
    row=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u,chid)).fetchone()
    last=int(row['last_read_event_id']) if row else 0
    evs=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(chid,last)).fetchall()
    latest={}
    for e in evs:
        try:
            f=json.loads(e['payload'])
            m=f.get('message')
            if m and m.get('id') is not None:
                latest[m['id']]=f
            elif f.get('type')=='message.deleted' and f.get('message_id') is not None:
                latest[f['message_id']]=f
        except: pass
    unread=mentions=0
    for f in latest.values():
        m=f.get('message')
        if f.get('type')=='message.deleted' or not m or m.get('author_id')==u: continue
        unread+=1
        if u in (m.get('mentions') or []): mentions+=1
    c.close()
    return {'channel_id':chid,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}

def mark_read(u,chid,event_id):
    if isinstance(event_id,bool) or not isinstance(event_id,int) or event_id<0: raise ValueError()
    with tx() as c:
        old=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u,chid)).fetchone()
        val=max(event_id, int(old['last_read_event_id']) if old else 0)
        c.execute('INSERT INTO read_state(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u,chid,val))
    return read_state(u,chid)

def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x['k']==k and isinstance(x['v'],int); return x['v']
 except: raise ValueError()
if __name__=='__main__' and '--init-db' in __import__('sys').argv:init_db()

def head_event_id(channel_id):
    c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(channel_id,)).fetchone(); c.close()
    return r['last_event_id'] if r else 0
def events_after(channel_id,since):
    c=connect(); r=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel_id,since)).fetchall(); c.close(); return r

def invitation_to_json(r):
    return {'code':r['code'],'workspace_id':r['workspace_id'],'invited_user_id':r['invited_user_id'],'max_uses':r['max_uses'],'uses':r['uses'],'expires_at':r['expires_at'],'created_at':r['created_at']}
def create_invitation(actor,slug,body):
    w=get_workspace(slug)
    if not w: raise KeyError()
    if member_role(w['id'],actor['id']) not in ('owner','admin'): raise PermissionError()
    if not isinstance(body,dict): raise ValueError()
    for k in ('max_uses','expires_in_seconds','invited_user_id'):
        if k in body and (isinstance(body[k],bool) or not isinstance(body[k],int)): raise ValueError()
    mx=body.get('max_uses',1); ex=body.get('expires_in_seconds')
    if mx<=0 or (ex is not None and ex<=0): raise ValueError()
    target=body.get('invited_user_id')
    if 'invited_username' in body:
        if not isinstance(body['invited_username'],str): raise ValueError()
        c=connect(); x=c.execute('SELECT id FROM users WHERE username=?',(body['invited_username'],)).fetchone(); c.close()
        if not x: raise KeyError()
        target=x['id']
    if target is not None:
        c=connect(); x=c.execute('SELECT id FROM users WHERE id=?',(target,)).fetchone(); c.close()
        if not x: raise KeyError()
        mx=1
    exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=ex)).isoformat(timespec='milliseconds').replace('+00:00','Z') if ex is not None else None
    code=secrets.token_urlsafe(18); created=now_iso()
    with tx() as c:
        c.execute('INSERT INTO invitations(workspace_id,code,invited_user_id,expires_at,max_uses,created_at,revoked) VALUES(?,?,?,?,?,?,0)',(w['id'],code,target,exp,mx,created))
        r=c.execute('SELECT * FROM invitations WHERE id=last_insert_rowid()').fetchone()
    return r
def accept_invitation(user,code):
    with tx() as c:
        r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
        if not r: raise KeyError()
        if r['revoked'] or r['uses']>=r['max_uses'] or (r['expires_at'] and r['expires_at']<=now_iso()): raise KeyError()
        if r['invited_user_id'] is not None and r['invited_user_id']!=user['id']: raise PermissionError()
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()
        if c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],user['id'])).fetchone(): return w
        c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(r['id'],))
        add_workspace_member(c,w['id'],user['id'],'member')
        return w
def revoke_invitation(actor,slug,code):
    w=get_workspace(slug)
    if not w: raise KeyError()
    if member_role(w['id'],actor['id']) not in ('owner','admin'): raise PermissionError()
    with tx() as c:
        r=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND code=?',(w['id'],code)).fetchone()
        if not r: raise KeyError()
        c.execute('UPDATE invitations SET revoked=1 WHERE id=?',(r['id'],))
def update_workspace(actor,slug,body):
    w=get_workspace(slug)
    if not w: raise KeyError()
    if member_role(w['id'],actor['id']) not in ('owner','admin'): raise PermissionError()
    if not isinstance(body,dict): raise ValueError()
    name=body.get('name',w['name']); mode=body.get('join_mode',w['join_mode'])
    if not isinstance(name,str) or not name or not isinstance(mode,str) or mode not in ('open','invite_only'): raise ValueError()
    with tx() as c:c.execute('UPDATE workspaces SET name=?,join_mode=? WHERE id=?',(name,mode,w['id']))
    return get_workspace(slug)

def pin(user, mid):
    c=connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone(); c.close()
    if not m: raise KeyError()
    ch=get_channel(m['channel_id'])
    if not can_read_channel(user['id'],ch): raise PermissionError()
    with tx() as c:
        c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(mid,user['id'],now_iso()))
        r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return r
def unpin(user,mid):
    c=connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m: raise KeyError()
    ch=get_channel(m['channel_id'])
    if not can_read_channel(user['id'],ch): raise PermissionError()
    with tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
def list_pins(user,chid,limit,cur):
    ch=get_channel(chid)
    if not ch: raise KeyError()
    if not can_read_channel(user['id'],ch): raise PermissionError()
    c=connect(); q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND m.deleted=0'; a=[chid]
    if cur is not None:q+=' AND p.message_id<?';a.append(cur)
    q+=' ORDER BY p.message_id DESC LIMIT ?';a.append(limit+1); rs=c.execute(q,a).fetchall();c.close()
    more=len(rs)>limit; rs=rs[:limit]
    vals=[]
    for x in rs:
        vals.append({'message':msg_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']})
    return vals, (encode_cursor('pin',rs[-1]['message_id']) if more and rs else None)
def search(user,text,limit,cur,wid=None):
    c=connect(); q='SELECT m.*,ch.workspace_id FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND lower(m.body) LIKE ?';a=['%'+text.lower()+'%']
    if wid is not None:q+=' AND ch.workspace_id=?';a.append(wid)
    q+=' AND (EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=m.channel_id AND cm.user_id=?) OR (ch.is_private=0 AND EXISTS (SELECT 1 FROM members wm WHERE wm.workspace_id=ch.workspace_id AND wm.user_id=?)))';a.extend([user['id'],user['id']])
    if cur is not None:q+=' AND m.id<?';a.append(cur)
    q+=' ORDER BY m.id DESC LIMIT ?';a.append(limit+1);rs=c.execute(q,a).fetchall();c.close()
    more=len(rs)>limit;rs=rs[:limit]
    return [msg_json(x) for x in rs],(encode_cursor('srch',rs[-1]['id']) if more and rs else None)
