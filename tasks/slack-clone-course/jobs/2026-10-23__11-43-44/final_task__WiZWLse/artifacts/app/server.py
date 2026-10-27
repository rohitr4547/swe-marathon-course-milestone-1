import asyncio,json,argparse,re,datetime,secrets
from aiohttp import web,WSMsgType
import core
NODE=0
def out(x,status=200): return web.json_response(x,status=status)
def err(status,msg): return out({'error':msg},status)
async def body(req):
 try:
  x=await req.json()
  if not isinstance(x,dict): raise ValueError()
  return x
 except: raise web.HTTPBadRequest(text=json.dumps({'error':'invalid json'}),content_type='application/json')
def uid(req):
 h=req.headers.get('Authorization','')
 if not h.startswith('Bearer '): raise web.HTTPUnauthorized(text=json.dumps({'error':'authentication required'}),content_type='application/json')
 u=core.user_for_token(h[7:])
 if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'authentication required'}),content_type='application/json')
 return u
def getch(i):
 try: i=int(i)
 except: return None
 return core.get_channel(i)
def access(u,ch):
 return core.can_read_channel(u['id'],ch)
def role(u,w):
 return core.member_role(w,u['id'])
async def health(r): return out({'status':'ok','node_id':NODE})
async def register(r):
 b=await body(r); un=b.get('username'); pw=b.get('password')
 if not isinstance(un,str) or not re.fullmatch(r'[A-Za-z0-9_]+',un): return err(400,'invalid username')
 if not isinstance(pw,str) or len(pw)<8:return err(400,'invalid password')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM users WHERE username=?',(un,)).fetchone(): return err(409,'username exists')
  c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,core.hash_password(pw),b.get('display_name') or un))
  u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t},201)
async def login(r):
 b=await body(r); u=None
 if isinstance(b.get('username'),str):
  c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone();c.close()
 if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']): return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(r): return out({'user':core.user_to_json(uid(r))})
async def workspaces(r):
 u=uid(r);c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(r):
 u=uid(r);b=await body(r); s=b.get('slug');n=b.get('name')
 if not isinstance(s,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',s) or not isinstance(n,str) or not n:return err(400,'invalid workspace')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(s,)).fetchone():return err(409,'exists')
  c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(s,n,u['id']));w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
  core.add_workspace_member(c,w['id'],u['id'],'owner');g=core.create_channel_row(c,w['id'],'general',0,'',u['id'])
 return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},201)
async def ws_detail(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],r.query.get('include_archived') in ('1','true'))).fetchall();c.close()
 if not role(u,w['id']): return err(403,'forbidden')
 rows=[x for x in rows if not x['is_private'] or access(u,x)]
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':[read_obj(u['id'],x['id']) for x in rows]})
def read_obj(u,ch):
 c=core.connect()
 x=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u,ch)).fetchone()
 last=x[0] if x else 0
 es=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall()
 c.close()
 latest={}
 for e in es:
  try:
   z=json.loads(e['payload']); m=z.get('message')
   if m and z.get('type') in ('message.created','message.reply','message.updated'):
    latest[m['id']]=m
  except Exception: pass
 rows=[m for m in latest.values() if m.get('author_id')!=u]
 return {'channel_id':ch,'last_read_event_id':last,'unread_count':len(rows),'mention_count':sum(u in (m.get('mentions') or []) for m in rows)}

async def members(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not role(u,w['id']):return err(403,'forbidden')
 c=core.connect();x=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
 return out({'members':[{'user_id':z['user_id'],'role':z['role'],'username':z['username'],'display_name':z['display_name']} for z in x]})
async def create_ch(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 b=await body(r);n=b.get('name');p=b.get('is_private',False);topic=b.get('topic','')
 if not isinstance(n,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',n) or not isinstance(p,bool) or not isinstance(topic,str) or len(topic)>250:return err(400,'invalid channel')
 rr=role(u,w['id'])
 if rr not in ('owner','admin') and p:return err(403,'forbidden')
 if rr not in ('owner','admin','member'):return err(403,'forbidden')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone():return err(409,'exists')
  x=core.create_channel_row(c,w['id'],n,p,topic,u['id'])
 return out({'channel':core.channel_to_json(x)},201)
async def join(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 w=core.get_workspace_id(ch['workspace_id']);rr=role(u,w['id'])
 if ch['is_private'] or rr=='guest':return err(403,'forbidden')
 if not rr and w['join_mode']=='invite_only':return err(403,'forbidden')
 with core.tx() as c:
  if not rr:core.add_workspace_member(c,w['id'],u['id'],'member')
  if core.add_channel_member(c,ch['id'],u['id']):core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def leave(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 with core.tx() as c:
  x=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
  if x.rowcount: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
 return out({'channel':core.channel_to_json(ch)})
async def channel_members(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 c=core.connect();x=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return out({'members':[core.user_to_json(z) for z in x]})
async def messages(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 try:n,cur=core.paging(r.query,'msg')
 except:return err(400,'bad paging')
 c=core.connect();q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL';args=[ch['id']]
 if cur is not None:q+=' AND id<?';args.append(cur)
 q+=' ORDER BY id DESC LIMIT ?';args.append(n+1);rows=c.execute(q,args).fetchall();c.close();more=len(rows)>n;rows=rows[:n]
 return out({'messages':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('msg',rows[-1]['id']) if more else None})
async def post(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 if bool(ch['is_archived']):return err(423,'ARCHIVED')
 b=await body(r);bodyv=b.get('body');parent=b.get('parent_id')
 if not isinstance(bodyv,str) or not bodyv.strip() or (parent is not None and (isinstance(parent,bool) or not isinstance(parent,int))):return err(400,'invalid body')
 try:m=core.create_message(u['id'],ch['id'],bodyv,parent)
 except ValueError as e:return err(423 if str(e)=='ARCHIVED' else 400,str(e))
 return out({'message':m},201)
async def replies(r):
 u=uid(r);parent=getch(0); 
 try: mid=int(r.match_info['id'])
 except:return err(404,'not found')
 c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
 if not p:return err(404,'not found')
 ch=core.get_channel(p['channel_id'])
 if not access(u,ch):return err(403,'forbidden')
 try:n,cur=core.paging(r.query,'rep')
 except:return err(400,'bad paging')
 q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';a=[mid]
 if cur is not None:q+=' AND id>?';a.append(cur)
 q+=' ORDER BY id ASC LIMIT ?';a.append(n+1);rows=c.execute(q,a).fetchall();c.close();more=len(rows)>n;rows=rows[:n]
 return out({'replies':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('rep',rows[-1]['id']) if more else None})
async def patch_message(r):
 u=uid(r)
 try:mid=int(r.match_info['id'])
 except:return err(404,'not found')
 b=await body(r);v=b.get('body')
 if not isinstance(v,str) or not v.strip():return err(400,'invalid body')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 if m['author_id']!=u['id']:return err(403,'forbidden')
 with core.tx() as c:
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  if m['deleted']:return err(409,'deleted')
  mentions=core.resolve_mentions(c,v,ch,u['id']);c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(v,core.now_iso(),json.dumps(mentions),mid))
  x=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();core.emit_event(c,ch['id'],'message.updated',{'message':core.message_to_json(x)})
 return out({'message':core.message_to_json(x)})
async def delete_message(r):
 u=uid(r)
 try:mid=int(r.match_info['id'])
 except:return err(404,'not found')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id'])
 if m['author_id']!=u['id'] and role(u,ch['workspace_id'])!='owner':return err(403,'forbidden')
 if not m['deleted']:
  with core.tx() as c:
   c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit_event(c,ch['id'],'message.deleted',{'message_id':mid})
 return out({'deleted':True})
async def react(r):
 u=uid(r);mid=int(r.match_info['id']);b=await body(r);e=b.get('emoji')
 if not isinstance(e,str) or not e:return err(400,'invalid emoji')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 if not access(u,core.get_channel(m['channel_id'])):return err(403,'forbidden')
 with core.tx() as c:
  x=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],e))
  if x.rowcount:core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':mid,'emoji':e,'user_id':u['id']})
 return out({'reactions':core.reactions_for(mid)})
async def unreact(r):
 u=uid(r);mid=int(r.match_info['id']);e=r.match_info['emoji']
 with core.tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return err(404,'not found')
  x=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],e))
  if x.rowcount:core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':mid,'emoji':e,'user_id':u['id']})
 return out({'reactions':core.reactions_for(mid)})
async def pin(r):
 u=uid(r);mid=int(r.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 if not access(u,core.get_channel(m['channel_id'])):return err(403,'forbidden')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()));x=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
 return out({'pin':{'message_id':mid,'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
async def unpin(r):
 u=uid(r);mid=int(r.match_info['id'])
 with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
 return out({'unpinned':True})
async def pins(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 try:n,cur=core.paging(r.query,'pin')
 except:return err(400,'bad paging')
 c=core.connect();q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';a=[ch['id']]
 if cur is not None:q+=' AND p.message_id<?';a.append(cur)
 q+=' ORDER BY p.message_id DESC LIMIT ?';a.append(n+1);rs=c.execute(q,a).fetchall();c.close();more=len(rs)>n;rs=rs[:n]
 return out({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':core.encode_cursor('pin',rs[-1]['message_id']) if more else None})
async def search(r):
 u=uid(r);qv=r.query.get('q')
 if not isinstance(qv,str) or not qv:return err(400,'missing q')
 wsfilter=r.query.get('workspace_id')
 if wsfilter is not None:
  try: wsfilter=int(wsfilter)
  except: return err(400,'invalid workspace_id')
 try:n,cur=core.paging(r.query,'srch')
 except:return err(400,'bad paging')
 c=core.connect();q='SELECT m.* FROM messages m JOIN channels sc ON sc.id=m.channel_id JOIN members wm ON wm.workspace_id=sc.workspace_id AND wm.user_id=? WHERE m.deleted=0 AND (sc.is_private=0 OR EXISTS (SELECT 1 FROM channel_members pc WHERE pc.channel_id=sc.id AND pc.user_id=?)) AND lower(m.body) LIKE lower(?)';a=[u['id'],u['id'],'%'+qv+'%']
 if wsfilter is not None:q+=' AND sc.workspace_id=?';a.append(wsfilter)
 if cur is not None:q+=' AND m.id<?';a.append(cur)
 q+=' ORDER BY m.id DESC LIMIT ?';a.append(n+1);rs=c.execute(q,a).fetchall();c.close();more=len(rs)>n;rs=rs[:n]
 return out({'results':[core.message_to_json(x) for x in rs],'next_cursor':core.encode_cursor('srch',rs[-1]['id']) if more else None})
async def read(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 if r.method=='POST':
  b=await body(r);v=b.get('last_read_event_id')
  if isinstance(v,bool) or not isinstance(v,int) or v<0:return err(400,'invalid read state')
  with core.tx() as c:
   old=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone();v=max(v,old[0] if old else 0);c.execute('INSERT INTO reads VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],ch['id'],v))
 return out({'read_state':read_obj(u['id'],ch['id'])})
async def patch_channel(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 if ch['is_archived']:return err(423,'ARCHIVED')
 if role(u,ch['workspace_id'])!='owner':
  c0=core.connect(); joined=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
  if not joined:return err(403,'forbidden')
 b=await body(r);t=b.get('topic')
 if not isinstance(t,str) or len(t)>250:return err(400,'invalid topic')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(t,ch['id']));core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def archive(r):
 u=uid(r);ch=getch(r.match_info['id'])
 if not ch:return err(404,'not found')
 if role(u,ch['workspace_id'])!='owner':return err(403,'forbidden')
 archived = r.path.endswith('/archive') and not r.path.endswith('/unarchive')
 with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(archived),ch['id']));core.emit_event(c,ch['id'],'channel.archived' if archived else 'channel.unarchived',{})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def change_role(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 try: target=int(r.match_info['user_id'])
 except: return err(400,'invalid user id')
 if not w:return err(404,'not found')
 rr=role(u,w['id']);b=await body(r);new=b.get('role')
 if new not in ('admin','member','guest'):return err(400,'invalid role')
 c=core.connect();old=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone();c.close()
 if not old:return err(404,'not found')
 if rr not in ('owner','admin') or old['role']=='owner' or (rr=='admin' and old['role']=='admin'):return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(new,w['id'],target))
 return out({'member':{'user_id':target,'role':new}})
async def transfer(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug']);b=await body(r);n=b.get('new_owner_id')
 if isinstance(n,bool) or not isinstance(n,int): return err(400,'invalid owner id')
 if not w:return err(404,'not found')
 if role(u,w['id'])!='owner':return err(403,'forbidden')
 c=core.connect();x=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],n)).fetchone();c.close()
 if not x or x['role']!='admin':return err(400,'target must be admin')
 with core.tx() as c:c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']));c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],n));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(n,w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def patch_ws(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(u,w['id']) not in ('owner','admin'):return err(403,'forbidden')
 b=await body(r);name=b.get('name',w['name']);jm=b.get('join_mode',w['join_mode'])
 if not isinstance(name,str) or not isinstance(jm,str) or jm not in ('open','invite_only'):return err(400,'invalid settings')
 with core.tx() as c:c.execute('UPDATE workspaces SET name=?,join_mode=? WHERE id=?',(name,jm,w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invite_create(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(u,w['id']) not in ('owner','admin'):return err(403,'forbidden')
 b=await body(r);mx=b.get('max_uses',1);exp=b.get('expires_in_seconds');target=b.get('invited_user_id')
 if isinstance(mx,bool) or not isinstance(mx,int) or mx<1:return err(400,'invalid max_uses')
 if exp is not None and (isinstance(exp,bool) or not isinstance(exp,(int,float)) or exp<0):return err(400,'invalid expiry')
 if target is not None and (isinstance(target,bool) or not isinstance(target,int)):return err(400,'invalid target')
 if target is not None: mx=1
 if target is None and isinstance(b.get('invited_username'),str):
  c=core.connect();x=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();c.close();target=x[0] if x else None
 code=secrets.token_urlsafe(18);expires=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=exp)).isoformat() if isinstance(exp,(int,float)) else None
 with core.tx() as c:c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,?)',(code,w['id'],u['id'],expires,mx,0,target,0))
 return out({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':mx,'uses':0,'expires_at':expires,'invited_user_id':target}},201)
async def invite_list(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(u,w['id']) not in ('owner','admin'):return err(403,'forbidden')
 c=core.connect();xs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(w['id'],core.now_iso())).fetchall();c.close()
 return out({'invitations':[dict(x) for x in xs]})

async def invite_delete(r):
 u=uid(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(u,w['id']) not in ('owner','admin'):return err(403,'forbidden')
 with core.tx() as c:
  x=c.execute('SELECT * FROM invitations WHERE code=? AND workspace_id=? AND revoked=0',(r.match_info['code'],w['id'])).fetchone()
  if not x:return err(404,'not found')
  c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(r.match_info['code'],))
 return out({'deleted':True})

async def invite_accept(r):
 u=uid(r);code=r.match_info['code'];c=core.connect();x=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone();c.close()
 if not x or x['revoked'] or x['uses']>=x['max_uses'] or (x['expires_at'] and x['expires_at']<core.now_iso()):return err(404,'invalid invitation')
 if x['invited_user_id'] and x['invited_user_id']!=u['id']:return err(403,'targeted invitation')
 with core.tx() as c:
  core.add_workspace_member(c,x['workspace_id'],u['id'],'member');c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
 w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
 return out({'workspace':core.workspace_to_json(w)})
async def ws_handler(r):
 token=r.query.get('token','');u=core.user_for_token(token)
 if not u:return web.Response(status=401)
 ws=web.WebSocketResponse();await ws.prepare(r);subs={};running=True
 async def poll():
  while running:
   for ch,last in list(subs.items()):
    for e in core.events_after(ch,last):
     await ws.send_str(e['payload']);subs[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for m in ws:
   if m.type!=WSMsgType.TEXT:continue
   try:x=json.loads(m.data)
   except:continue
   ch=x.get('channel_id');typ=x.get('type')
   if typ=='subscribe' and isinstance(ch,int):
    subs[ch]=core.head_event_id(ch);await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
   elif typ=='resume' and isinstance(ch,int):
    since=x.get('since_event_id',0);subs[ch]=since
    for e in core.events_after(ch,since):
     await ws.send_str(e['payload']);subs[ch]=e['event_id']
    await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':subs.get(ch,0)})
 finally:
  running=False;task.cancel()
 return ws
def setup():
 a=web.Application()
 a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
 a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_patch('/api/workspaces/{slug}',patch_ws);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',change_role);a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
 a.router.add_post('/api/workspaces/{slug}/channels',create_ch);a.router.add_post('/api/channels/{id}/join',join);a.router.add_post('/api/channels/{id}/leave',leave);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_patch('/api/channels/{id}',patch_channel)
 a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive)
 a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_patch('/api/messages/{id}',patch_message);a.router.add_delete('/api/messages/{id}',delete_message)
 a.router.add_post('/api/messages/{id}/reactions',react);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreact);a.router.add_post('/api/messages/{id}/pin',pin);a.router.add_delete('/api/messages/{id}/pin',unpin);a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_get('/api/search',search);a.router.add_get('/api/channels/{id}/read',read);a.router.add_post('/api/channels/{id}/read',read)
 a.router.add_post('/api/workspaces/{slug}/invitations',invite_create);a.router.add_get('/api/workspaces/{slug}/invitations',invite_list);a.router.add_post('/api/invitations/{code}/accept',invite_accept);a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',invite_delete);a.router.add_get('/api/ws',ws_handler)
 return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(setup(),host='127.0.0.1',port=8000+NODE)
