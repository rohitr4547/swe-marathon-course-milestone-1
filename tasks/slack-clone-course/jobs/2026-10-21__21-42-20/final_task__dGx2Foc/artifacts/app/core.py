import sqlite3,os,re,json,base64,secrets,hashlib,datetime
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
class tx:
 def __enter__(self): self.c=connect();self.c.execute('BEGIN IMMEDIATE');return self.c
 def __exit__(self,t,v,b):
  self.c.execute('ROLLBACK' if t else 'COMMIT');self.c.close()
def now_iso(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
 c=connect();c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]',event_id INTEGER);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at TEXT,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));''');c.close()
def hash_password(p):
 s=secrets.token_hex(16);return s+'$'+hashlib.sha256((s+p).encode()).hexdigest()
def verify_password(p,s):
 try:a,b=s.split('$');return hashlib.sha256((a+p).encode()).hexdigest()==b
 except:return False
def issue_token(c,uid):
 t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));return t
def user_for_token(t):
 if not t:return None
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def user_to_json(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def get_workspace(slug):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone();c.close();return r
def member_role(w,u):
 c=connect();r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,u)).fetchone();c.close();return r['role'] if r else None
def add_workspace_member(c,w,u,role='member'):
 c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w,u,role))
 g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
 if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u))
def channel_to_json(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def get_channel(i):
 c=connect();r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return r
def create_channel_row(c,w,name,private,topic,creator):
 c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic or ''));i=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(i,creator));return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def add_channel_member(c,ch,u):
 x=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u));return x.rowcount>0
def can_read_channel(u,ch): return bool(member_role(ch['workspace_id'],u) and (not ch['is_private'] or _cm(ch['id'],u)))
def _cm(ch,u):
 c=connect();x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)).fetchone();c.close();return x
def join_channel(u,chid):
 ch=get_channel(chid)
 if not ch: raise ValueError('not found')
 role=member_role(ch['workspace_id'],u['id']);w=get_workspace_by_id(ch['workspace_id'])
 if ch['is_private'] and not _cm(chid,u['id']): raise PermissionError()
 if role=='guest': raise PermissionError()
 with tx() as c:
  if not role and w['join_mode']=='open': add_workspace_member(c,w['id'],u['id'],'member')
  elif not role: raise PermissionError()
  added=add_channel_member(c,chid,u['id'])
  if added: emit_event(c,chid,'member.joined',{'user_id':u['id']})
 return get_channel(chid)
def get_workspace_by_id(i):
 c=connect();r=c.execute('SELECT * FROM workspaces WHERE id=?',(i,)).fetchone();c.close();return r
def emit_event(c,ch,kind,payload):
 n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
 p={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(p)));return n
def require_writable(ch):
 if ch['is_archived']: raise RuntimeError('archived')
def resolve_mentions(c,body,ch,author):
 out=[]
 if '@channel' in body:
  for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)):
   if r[0]!=author and r[0] not in out:out.append(r[0])
 for m in re.finditer(r'(?<![\w@.])@(\w+)',body):
  r=c.execute('SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=? AND u.username=?',(ch['workspace_id'],m.group(1))).fetchone()
  if r and r[0]!=author and r[0] not in out:out.append(r[0])
 return out
def reactions_for(mid):
 c=connect();rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)).fetchall();c.close();d={}
 for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
 return [{'emoji':e,'count':len(x),'user_ids':x} for e,x in d.items()]
def message_to_json(r):
 if not r:return None
 c=connect();a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone();cnt=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n'];c.close()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':cnt,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions_for(r['id']),'mentions':json.loads(r['mentions'] or '[]')}
def create_message(u,chid,body,parent=None):
 ch=get_channel(chid);require_writable(ch)
 if not body or not body.strip():raise ValueError('empty body')
 with tx() as c:
  if parent:
   p=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(parent,)).fetchone()
   if not p or p['channel_id']!=chid or p['parent_id']:raise ValueError('bad parent')
  ment=resolve_mentions(c,body,ch,u['id']);c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(chid,u['id'],body,parent,now_iso(),json.dumps(ment)));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  kind='message.reply' if parent else 'message.created';eid=emit_event(c,chid,kind,{'message':message_to_json(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())});c.execute('UPDATE messages SET event_id=? WHERE id=?',(eid,mid))
 return message_to_json(get_message(mid))
def get_message(i):
 c=connect();r=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();c.close();return r
def encode_cursor(k,v):return base64.urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
def decode_cursor(k,s):
 try:
  x=json.loads(base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4)));assert x['k']==k and isinstance(x['v'],int);return x['v']
 except:raise ValueError('bad cursor')
def parse_paging(q,k):
 if 'offset' in q or 'page' in q:raise ValueError('offset unsupported')
 try:n=int(q.get('limit',50))
 except:raise ValueError('bad limit')
 if n<1 or n>200:raise ValueError('bad limit')
 return n,decode_cursor(k,q['cursor']) if q.get('cursor') else None
def head_event_id(ch):
 c=connect();r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();c.close();return r[0] if r else 0
def events_after(ch,since):
 c=connect();r=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since));x=[json.loads(a[0]) for a in r];c.close();return x
def list_messages(chid,limit,cursor=None,replies=False):
 c=connect();where='channel_id=? AND deleted=0 AND '+('parent_id IS NOT NULL' if replies else 'parent_id IS NULL')
 args=[chid]
 if cursor is not None:where+=' AND id<?';args.append(cursor)
 rs=c.execute('SELECT * FROM messages WHERE '+where+' ORDER BY id DESC LIMIT ?',args+[limit+1]).fetchall();c.close()
 more=len(rs)>limit;rs=rs[:limit];return [message_to_json(x) for x in rs],(encode_cursor('rep' if replies else 'msg',rs[-1]['id']) if more and rs else None)
def list_replies(chid,limit,cursor,parent):
 c=connect();args=[parent]
 q='SELECT * FROM messages WHERE channel_id=? AND parent_id=? AND deleted=0'
 args=[chid,parent]
 if cursor is not None:q+=' AND id>?';args.append(cursor)
 rs=c.execute(q+' ORDER BY id ASC LIMIT ?',args+[limit+1]).fetchall();c.close();more=len(rs)>limit;rs=rs[:limit]
 return [message_to_json(x) for x in rs],(encode_cursor('rep',rs[-1]['id']) if more and rs else None)
def edit_message(uid,mid,body):
 r=get_message(mid)
 if not r:raise LookupError()
 if r['deleted']:raise RuntimeError('deleted')
 if r['author_id']!=uid:raise PermissionError()
 if not body or not body.strip():raise ValueError('empty body')
 with tx() as c:
  c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now_iso(),mid));x=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();emit_event(c,r['channel_id'],'message.updated',{'message':message_to_json(x)})
 return message_to_json(get_message(mid))
def delete_message(uid,mid):
 r=get_message(mid)
 if not r:raise LookupError()
 w=get_workspace_by_id(get_channel(r['channel_id'])['workspace_id'])
 if r['author_id']!=uid and w['owner_id']!=uid:raise PermissionError()
 if r['deleted']:return
 with tx() as c:
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
def react(uid,mid,emoji,adding=True):
 r=get_message(mid)
 if not r:raise LookupError()
 with tx() as c:
  if adding:
   x=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,uid,emoji))
   if not x.rowcount:return reactions_for(mid)
   emit_event(c,r['channel_id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':uid})
  else:
   x=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,uid,emoji))
   if x.rowcount:emit_event(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':uid})
 return reactions_for(mid)
def pin(uid,mid):
 r=get_message(mid)
 if not r:raise LookupError()
 with tx() as c:
  x=c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,uid,now_iso()))
  if x.rowcount:emit_event(c,r['channel_id'],'message.updated',{'message_id':mid,'pinned':True})
  p=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
 return {'message_id':mid,'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']}
def unpin(mid):
 r=get_message(mid)
 if not r:raise LookupError()
 with tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
 return True
def list_pins(chid,limit,cursor=None):
 c=connect();args=[chid];q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'
 if cursor is not None:q+=' AND p.message_id<?';args.append(cursor)
 rs=c.execute(q+' ORDER BY p.message_id DESC LIMIT ?',args+[limit+1]).fetchall();c.close();more=len(rs)>limit;rs=rs[:limit]
 return [{'message':message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],(encode_cursor('pin',rs[-1]['message_id']) if more and rs else None)
def search(uid,q,limit,cursor=None,wid=None):
 c=connect();args=[uid,'%'+q+'%'];where='m.deleted=0 AND LOWER(m.body) LIKE LOWER(?) AND cm.user_id=?'
 args=[ '%'+q+'%',uid]
 if wid is not None:where+=' AND ch.workspace_id=?';args.append(wid)
 if cursor is not None:where+=' AND m.id<?';args.append(cursor)
 rs=c.execute('SELECT DISTINCT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN memberships wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? LEFT JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? WHERE '+where.replace('cm.user_id=?','(ch.is_private=0 OR cm.user_id=?)')+' ORDER BY m.id DESC LIMIT ?', [uid,uid]+args+[limit+1]).fetchall();c.close();more=len(rs)>limit;rs=rs[:limit]
 return [message_to_json(x) for x in rs],(encode_cursor('srch',rs[-1]['id']) if more and rs else None)
def read_state(uid,chid):
 c=connect();x=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(uid,chid)).fetchone();last=x[0] if x else 0
 rs=c.execute('SELECT m.* FROM messages m WHERE m.channel_id=? AND m.deleted=0 AND m.author_id!=? AND m.event_id>?',(chid,uid,last)).fetchall();c.close()
 return {'channel_id':chid,'last_read_event_id':last,'unread_count':len(rs),'mention_count':sum(uid in json.loads(x['mentions'] or '[]') for x in rs)}
def mark_read(uid,chid,n):
 if not isinstance(n,int) or n<0:raise ValueError('invalid read cursor')
 with tx() as c:c.execute('INSERT INTO read_state VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=MAX(last_read_event_id,excluded.last_read_event_id)',(uid,chid,n))
 return read_state(uid,chid)

if __name__=='__main__':
 import sys
 if '--init-db' in sys.argv:
  init_db()
