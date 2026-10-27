import sqlite3,os,hashlib,secrets,json,re,base64,datetime,sys
DB='/app/data/huddle.db'
def connect():
 os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class tx:
 def __enter__(self): self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open',created_at TEXT);
CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]',event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY,workspace_id INTEGER,code TEXT UNIQUE,created_by INTEGER,invited_user_id INTEGER,expires_at TEXT,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
'''); c.close()
def hash_password(p):
 s=secrets.token_hex(16); return s+'$'+hashlib.sha256((s+p).encode()).hexdigest()
def verify_password(p,x):
 try:s,h=x.split('$',1); return hashlib.sha256((s+p).encode()).hexdigest()==h
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
def get_workspace_id(wid):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def add_channel_member(c,ch,u): return c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u)).rowcount>0
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:add_channel_member(c,g['id'],u)
def channel_to_json(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def create_channel_row(c,w,name,private,topic,creator):
 cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic or '')); i=cur.lastrowid; add_channel_member(c,i,creator); return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def emit_event(c,ch,kind,payload):
 r=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone(); n=r['last_event_id']; frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return n
def parse_paging(q,kind):
 try: lim=int(q.get('limit','50'))
 except: raise ValueError()
 if not 1<=lim<=200 or 'page' in q or 'offset' in q: raise ValueError()
 cur=q.get('cursor'); v=None
 if cur:
  try:
   d=json.loads(base64.urlsafe_b64decode(cur+'===')); assert d['k']==kind and isinstance(d['v'],int); v=d['v']
  except: raise ValueError()
 return lim,v
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def channel_access(u,ch):
 c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone(); c.close(); return bool(r)
def can_read_channel(u,ch): return channel_access(u,ch)
def resolve_mentions(c,body,ch,author):
 out=[]
 for n in re.findall(r'(?<![\w@.])@(\w+)',body):
  if n=='channel':
   rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],author)).fetchall(); ids=[x['user_id'] for x in rows]
  else:
   x=c.execute('SELECT u.id FROM users u JOIN workspace_members m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=?',(ch['workspace_id'],n)).fetchone(); ids=[] if not x or x['id']==author else [x['id']]
  for i in ids:
   if i not in out:out.append(i)
 return out
def reactions_for(mid):
 c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)).fetchall(); c.close(); d={}
 for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
 return [{'emoji':e,'count':len(v),'user_ids':v} for e,v in d.items()]
def msg_json(r):
 c=connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); replies=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']; c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions_for(r['id']),'mentions':json.loads(r['mentions'] or '[]')}
def create_message(ch,u,body,parent=None):
 with tx() as c:
  row=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
  if row['is_archived']: raise ValueError('archived')
  if not isinstance(body,str) or not body.strip(): raise ValueError('empty')
  mentions=resolve_mentions(c,body,row,u); t=now_iso(); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch,u,body,parent,t,json.dumps(mentions))); mid=cur.lastrowid
  n=emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':msg_json(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
  c.execute('UPDATE messages SET event_id=? WHERE id=?',(n,mid)); return c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
def list_messages(ch,lim,cur,parent=None):
 c=connect(); wh='channel_id=? AND deleted=0 AND '+('parent_id IS NULL' if parent is None else 'parent_id = ?'); args=[ch]+([] if parent is None else [parent]); 
 if cur:
  wh+=(' AND id > ?' if parent is not None else ' AND id < ?');args.append(cur)
 order='ASC' if parent is not None else 'DESC'
 rows=c.execute('SELECT * FROM messages WHERE '+wh+' ORDER BY id '+order+' LIMIT ?',args+[lim+1]).fetchall(); c.close(); return rows
def read_state(u,ch):
 c=connect(); x=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u,ch)).fetchone(); last=x['last_read_event_id'] if x else 0
 rows=c.execute('SELECT author_id,event_id,mentions FROM messages WHERE channel_id=? AND deleted=0 AND event_id>?',(ch,last)).fetchall(); c.close()
 own=[r for r in rows if r['author_id']!=u]; return {'channel_id':ch,'last_read_event_id':last,'unread_count':len(own),'mention_count':sum(u in json.loads(r['mentions']) for r in own)}
if __name__=='__main__':
 if '--init-db' in sys.argv:init_db()

def head_event_id(ch):
 c=connect(); r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close(); return r['last_event_id'] if r else 0
def events_after(ch,since):
 c=connect(); rs=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since,)).fetchall(); c.close(); return rs
def set_member_role(w,target,role_name):
 with tx() as c:
  r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,target)).fetchone()
  if not r: raise LookupError()
  if role_name not in ('admin','member','guest'): raise ValueError()
  c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',(role_name,w,target))
  return c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,target)).fetchone()['role']
def transfer_ownership(w,new_owner):
 with tx() as c:
  r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w,new_owner)).fetchone()
  if not r or r['role']!='admin': raise ValueError()
  old=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(w,)).fetchone()['owner_id']
  c.execute('UPDATE workspace_members SET role="admin" WHERE workspace_id=? AND user_id=?',(w,old))
  c.execute('UPDATE workspace_members SET role="owner" WHERE workspace_id=? AND user_id=?',(w,new_owner))
  c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(new_owner,w))
def invitation_to_json(r):
 return {'id':r['id'],'workspace_id':r['workspace_id'],'code':r['code'],'created_by':r['created_by'],'invited_user_id':r['invited_user_id'],'expires_at':r['expires_at'],'max_uses':r['max_uses'],'uses':r['uses']}
def create_invitation(w,creator,body):
 code=secrets.token_urlsafe(12)
 invited=body.get('invited_user_id')
 if not invited and body.get('invited_username'):
  c=connect(); x=c.execute('SELECT id FROM users WHERE username=?',(body['invited_username'],)).fetchone(); c.close(); invited=x['id'] if x else None
 exp=None
 if body.get('expires_in_seconds'):
  exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=int(body['expires_in_seconds']))).isoformat()
 with tx() as c:
  c.execute('INSERT INTO invitations(workspace_id,code,created_by,invited_user_id,expires_at,max_uses) VALUES(?,?,?,?,?,?)',(w,code,creator,invited,exp,1 if invited else int(body.get('max_uses',1))))
  return c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
def accept_invitation(u,code):
 with tx() as c:
  x=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
  if not x or x['revoked'] or x['uses']>=x['max_uses'] or (x['expires_at'] and x['expires_at']<now_iso()): raise LookupError()
  if x['invited_user_id'] and x['invited_user_id']!=u: raise PermissionError()
  add_workspace_member(c,x['workspace_id'],u,'member'); c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(x['id'],))
  return x['workspace_id']
