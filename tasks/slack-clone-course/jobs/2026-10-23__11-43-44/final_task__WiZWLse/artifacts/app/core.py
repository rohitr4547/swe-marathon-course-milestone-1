import sqlite3,os,hashlib,secrets,base64,json,re,datetime,time
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
def tx():
 class C:
  def __enter__(s):
   s.c=connect(); s.c.execute('BEGIN IMMEDIATE'); return s.c
  def __exit__(s,t,v,b):
   s.c.execute('ROLLBACK' if t else 'COMMIT'); s.c.close()
 return C()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect(); c.execute('PRAGMA journal_mode=WAL')
 for q in [
 'CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "")',
 'CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER)',
 'CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open")',
 'CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id))',
 'CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name))',
 'CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id))',
 'CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT "[]")',
 'CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji))',
 'CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT)',
 'CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id))',
 'CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id))',
 'CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,created_by INTEGER,expires_at TEXT,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0)'
 ]: c.execute(q)
 c.close()
def hash_password(pw):
 salt=secrets.token_hex(16); return salt+'$'+hashlib.sha256((salt+pw).encode()).hexdigest()
def verify_password(pw,stored):
 try:
  salt,h=stored.split('$',1); return secrets.compare_digest(h,hashlib.sha256((salt+pw).encode()).hexdigest())
 except: return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES (?,?)',(t,uid)); return t
def user_for_token(t):
 c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
 return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':r['timezone'],'avatar_url':r['avatar_url'],'status_text':r['status_text'],'status_emoji':r['status_emoji']}
def workspace_to_json(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def get_workspace(slug):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_workspace_id(wid):
 c=connect(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); c.close(); return r
def member_role(w,u):
 c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def channel_to_json(r):
 return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def get_channel(i):
 c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def add_channel_member(c,ch,u):
 x=c.execute('INSERT OR IGNORE INTO channel_members VALUES (?,?)',(ch,u)); return x.rowcount>0
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO members VALUES (?,?,?)',(w,u,role))
 g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(w,)).fetchone()
 if g: add_channel_member(c,g['id'],u)
def create_channel_row(c,w,name,private,topic,creator):
 c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); r=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); add_channel_member(c,r['id'],creator); return r
def emit_event(c,ch,kind,payload):
 c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(ch,)); n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0]
 x={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(x))); return n
def can_read_channel(uid,ch):
 c=connect(); r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close()
 if r:return True
 return not ch['is_private'] and member_role(ch['workspace_id'],uid) is not None
def require_writable(ch):
 if ch['is_archived']: raise ValueError('ARCHIVED')
def resolve_mentions(c,body,ch,author):
 out=[]
 for n in re.findall(r'(?<![\w@.])@(\w+)',body):
  if n=='channel':
   rows=c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)).fetchall()
   ids=[x[0] for x in rows]
  else:
   x=c.execute('SELECT u.id FROM users u JOIN members m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=?',(ch['workspace_id'],n)).fetchone(); ids=[x[0]] if x else []
  for i in ids:
   if i!=author and i not in out: out.append(i)
 return out
def message_to_json(r):
 c=connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall(); c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':connect().execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rr],'mentions':json.loads(r['mentions'] or '[]')}
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))); assert x['k']==k and type(x['v']) is int; return x['v']
 except: raise ValueError('cursor')
def paging(q,k):
 if 'page' in q or 'offset' in q: raise ValueError('offset')
 try:n=int(q.get('limit','50'))
 except:raise ValueError('limit')
 if n<1 or n>200:raise ValueError('limit')
 return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
def create_message(uid,ch,body,parent=None):
 with tx() as c:
  r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone(); require_writable(r); ms=resolve_mentions(c,body,r,uid); t=now_iso()
  c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch,uid,body,parent,t,json.dumps(ms))); m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone(); emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':message_to_json(m)}); return message_to_json(m)
def reactions_for(mid):
 c=connect(); x=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall(); c.close(); return [{'emoji':r['emoji'],'count':r['n'],'user_ids':[int(z) for z in r['ids'].split(',')]} for r in x]
if __name__=='__main__' and '--init-db' in __import__('sys').argv:init_db()
def head_event_id(ch):
 c=connect();x=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return x[0] if x else 0
def events_after(ch,since):
 c=connect();x=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall();c.close();return [dict(z) for z in x]
