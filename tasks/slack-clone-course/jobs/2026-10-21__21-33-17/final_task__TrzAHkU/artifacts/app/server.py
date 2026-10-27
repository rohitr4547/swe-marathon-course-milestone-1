import asyncio,json,argparse,re
from aiohttp import web,WSMsgType
import core
NODE=0
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
def token(req): 
 h=req.headers.get('Authorization','')
 return h[7:] if h.startswith('Bearer ') else ''
def user(req):
 u=core.user_for_token(token(req))
 if not u: raise web.HTTPUnauthorized()
 return u
def role(w,u): return core.member_role(w,u if isinstance(u,int) else u['id'])
def channel_or(i):
 try:r=core.get_channel(int(i))
 except:r=None
 return r
def access(u,ch):
 return core.channel_access(u['id'],ch)
def message_access(u,ch):
 if core.channel_access(u['id'],ch): return True
 return (not ch['is_private']) and bool(core.member_role(ch['workspace_id'],u['id']))
def page(q,k):
 try:return core.parse_paging(q,k)
 except: raise web.HTTPBadRequest()
def members(w):
 c=core.connect(); rs=c.execute('SELECT u.*,m.role FROM users u JOIN workspace_members m ON m.user_id=u.id WHERE m.workspace_id=?',(w,)).fetchall(); c.close(); return [{'user_id':x['id'],'role':x['role'],**core.user_to_json(x)} for x in rs]
async def health(r): return out({'status':'ok','node_id':NODE})
async def register(r):
 try:b=await r.json(); n=b.get('username');p=b.get('password')
 except:return err(400,'invalid body')
 if not isinstance(n,str) or not re.fullmatch(r'[A-Za-z0-9_]+',n) or not isinstance(p,str) or len(p)<8:return err(400,'invalid credentials')
 with core.tx() as c:
  try:cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(n,core.hash_password(p),b.get('display_name') or n,core.now_iso())); uid=cur.lastrowid;t=core.issue_token(c,uid)
  except Exception:return err(409,'username exists')
 c=core.connect(); x=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close();return out({'user':core.user_to_json(x),'token':t},201)
async def login(r):
 try:b=await r.json()
 except:return err(400,'invalid body')
 c=core.connect();x=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone()
 if not x or not core.verify_password(b.get('password',''),x['password']):c.close();return err(401,'invalid credentials')
 t=core.issue_token(c,x['id']);c.close();return out({'user':core.user_to_json(x),'token':t})
async def me(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 return out({'user':core.user_to_json(u)})
async def workspaces(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 c=core.connect(); xs=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return out({'workspaces':[core.workspace_to_json(x) for x in xs]})
async def create_ws(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid body')
 if not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))) or not b.get('name'):return err(400,'invalid workspace')
 with core.tx() as c:
  try:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id,created_at) VALUES(?,?,?,?)',(b['slug'],b['name'],u['id'],core.now_iso()));w=cur.lastrowid
  except:return err(409,'slug exists')
  c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(w,u['id'],'owner'));g=core.create_channel_row(c,w,'general',0,'',u['id'])
 return out({'workspace':core.workspace_to_json(core.get_workspace(b['slug'])),'general_channel':core.channel_to_json(g)},201)
async def ws_detail(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect(); cs=c.execute('SELECT c.* FROM channels c LEFT JOIN channel_members cm ON cm.channel_id=c.id AND cm.user_id=? WHERE c.workspace_id=? AND (c.is_private=0 OR cm.user_id IS NOT NULL) AND (c.is_archived=0 OR ?)',(u['id'],w['id'],r.query.get('include_archived') in ('1','true'))).fetchall();c.close()
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs],'read_state':[core.read_state(u['id'],x['id']) for x in cs]})
async def ws_members(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if not role(w['id'],u['id']):return err(403,'forbidden')
 return out({'members':members(w['id'])})
async def patch_ws(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 with core.tx() as c:
  if 'name' in b:c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
  if 'join_mode' in b and b.get('join_mode') not in ('open','invite_only'):return err(400,'invalid join_mode')
  if 'join_mode' in b:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def create_channel(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 rr=role(w['id'],u['id'])
 if rr not in ('owner','admin','member'):return err(403,'forbidden')
 if b.get('is_private') and rr not in ('owner','admin'):return err(403,'forbidden')
 if not re.fullmatch(r'[a-z0-9-]{1,32}',str(b.get('name',''))) or len(b.get('topic',''))>250:return err(400,'invalid channel')
 try:
  with core.tx() as c:x=core.create_channel_row(c,w['id'],b['name'],b.get('is_private',False),b.get('topic',''),u['id'])
 except:return err(409,'duplicate channel')
 return out({'channel':core.channel_to_json(x)},201)
async def join_impl(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 w=core.get_workspace_id(ch['workspace_id']);rr=role(w['id'],u['id'])
 if ch['is_private'] and not access(u,ch):return err(403,'private channel')
 if rr=='guest':return err(403,'forbidden')
 if not rr and w['join_mode']=='invite_only':return err(403,'invite required')
 try:
  with core.tx() as c:
   if not rr:core.add_workspace_member(c,w['id'],u['id'],'member')
   if core.add_channel_member(c,ch['id'],u['id']):core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
  return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
 except Exception:
  import traceback
  traceback.print_exc()
  return err(500,'join failure')
async def join(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 w=core.get_workspace_id(ch['workspace_id']); rr=role(w['id'],u['id'])
 if ch['is_private'] and not access(u,ch):return err(403,'private channel')
 if rr=='guest':return err(403,'forbidden')
 if not rr and w['join_mode']=='invite_only':return err(403,'invite required')
 with core.tx() as c:
  if not rr:core.add_workspace_member(c,w['id'],u['id'],'member')
  if core.add_channel_member(c,ch['id'],u['id']):core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})

async def leave(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 with core.tx() as c:
  if c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).rowcount:core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
 return out({'channel':core.channel_to_json(ch)})
async def channel_patch(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch) and role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 if ch['is_archived']:return err(423,'archived')
 if len(b.get('topic',''))>250:return err(400,'invalid topic')
 with core.tx() as c:
  c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',ch['topic']),ch['id']));core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def messages(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not message_access(u,ch):return err(403,'forbidden')
 try:lim,cur=page(r.query,'msg')
 except web.HTTPBadRequest:return err(400,'invalid paging')
 xs=core.list_messages(ch['id'],lim,cur); nxt=core.encode_cursor('msg',xs[lim-1]['id']) if len(xs)>lim else None
 return out({'messages':[core.msg_json(x) for x in xs[:lim]],'next_cursor':nxt})
async def post_message(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not message_access(u,ch):return err(403,'forbidden')
 if ch['is_archived']:return err(423,'archived')
 try:x=core.create_message(ch['id'],u['id'],b.get('body',''),b.get('parent_id'))
 except ValueError as e:return err(400 if str(e)!='archived' else 423,str(e))
 return out({'message':core.msg_json(x)},201)
async def replies(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not p:return err(404,'not found')
 if not core.channel_access(u['id'],core.get_channel(p['channel_id'])):return err(403,'forbidden')
 try:lim,cur=page(r.query,'rep')
 except:return err(400,'invalid paging')
 xs=core.list_messages(p['channel_id'],lim,cur,p['id']);n=core.encode_cursor('rep',xs[lim-1]['id']) if len(xs)>lim else None
 return out({'replies':[core.msg_json(x) for x in xs[:lim]],'next_cursor':n})
async def edit(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 c=core.connect();x=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 if x['author_id']!=u['id']:return err(403,'forbidden')
 if x['deleted']:return err(409,'deleted')
 with core.tx() as c:
  c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b.get('body',''),core.now_iso(),x['id']));core.emit_event(c,x['channel_id'],'message.updated',{'message':core.msg_json(c.execute('SELECT * FROM messages WHERE id=?',(x['id'],)).fetchone())})
 return out({'message':core.msg_json(core.connect().execute('SELECT * FROM messages WHERE id=?',(x['id'],)).fetchone())})
async def delete(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 if x['author_id']!=u['id'] and role(core.get_channel(x['channel_id'])['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 if not x['deleted']:
  with core.tx() as c:c.execute('UPDATE messages SET deleted=1 WHERE id=?',(x['id'],));core.emit_event(c,x['channel_id'],'message.deleted',{'message_id':x['id']})
 return out({'deleted':True})
async def react(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 with core.tx() as c:
  added=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(x['id'],u['id'],b.get('emoji',''))).rowcount
  if added:core.emit_event(c,x['channel_id'],'reaction.added',{'message_id':x['id'],'emoji':b['emoji']})
 return out({'reactions':core.reactions_for(x['id'])})
async def unreact(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 with core.tx() as c:
  if c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(x['id'],u['id'],r.match_info['emoji'])).rowcount:core.emit_event(c,x['channel_id'],'reaction.removed',{'message_id':x['id'],'emoji':r.match_info['emoji']})
 return out({'reactions':core.reactions_for(x['id'])})
async def pin(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT * FROM messages WHERE id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 with core.tx() as c:
  c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(x['id'],u['id'],core.now_iso()))
 return out({'pin':{'message_id':x['id'],'pinned_by':u['id'],'pinned_at':core.connect().execute('SELECT pinned_at FROM pins WHERE message_id=?',(x['id'],)).fetchone()['pinned_at']}})
async def unpin(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 c=core.connect();x=c.execute('SELECT m.* FROM messages m WHERE m.id=?',(r.match_info['id'],)).fetchone();c.close()
 if not x:return err(404,'not found')
 ch=channel_or(x['channel_id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(x['id'],))
 return out({'unpinned':True})
async def search(r):
 try:u=user(r);lim,cur=page(r.query,'srch')
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 q=r.query.get('q','')
 if not q:return err(400,'query required')
 ws=r.query.get('workspace_id')
 c=core.connect()
 sql="""SELECT DISTINCT m.* FROM messages m
 JOIN channels ch ON ch.id=m.channel_id
 JOIN workspace_members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=?
 LEFT JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=?
 WHERE m.deleted=0 AND (ch.is_private=0 OR cm.user_id IS NOT NULL)
 AND lower(m.body) LIKE ?"""
 args=[u['id'],u['id'],'%'+q.lower()+'%']
 if ws:
  try:int(ws)
  except:c.close();return err(400,'invalid workspace_id')
  sql+=' AND ch.workspace_id=?';args.append(int(ws))
 if cur:sql+=' AND m.id < ?';args.append(cur)
 sql+=' ORDER BY m.id DESC LIMIT ?';args.append(lim+1)
 xs=c.execute(sql,args).fetchall();c.close()
 return out({'results':[core.msg_json(x) for x in xs[:lim]],'next_cursor':core.encode_cursor('srch',xs[lim-1]['id']) if len(xs)>lim else None})
async def read(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 if r.method=='POST':
  try:b=await r.json();v=b['last_read_event_id'];assert isinstance(v,int) and v>=0
  except:return err(400,'invalid')
  with core.tx() as c:c.execute('INSERT INTO read_state VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=max(last_read_event_id,excluded.last_read_event_id)',(u['id'],ch['id'],v))
 return out({'read_state':core.read_state(u['id'],ch['id'])})
async def ws_handler(r):
 t=r.query.get('token','');u=core.user_for_token(t)
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(r); subs={}
 async def poll():
  while not ws.closed:
   for cid,last in list(subs.items()):
    for e in core.events_after(cid,last):
     await ws.send_json(json.loads(e['payload']));subs[cid]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for m in ws:
   if m.type==WSMsgType.TEXT:
    d=json.loads(m.data);cid=int(d.get('channel_id',0))
    if d.get('type')=='subscribe':
     subs[cid]=core.head_event_id(cid);await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
    elif d.get('type')=='resume':
     n=int(d.get('since_event_id',0));subs[cid]=n
     for e in core.events_after(cid,n):await ws.send_json(json.loads(e['payload']));subs[cid]=e['event_id']
     await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
 finally:task.cancel()
 return ws

async def patch_member(r):
 try:u=user(r); b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 rr=role(w['id'],u['id']); target=int(r.match_info['user_id']); old=role(w['id'],target)
 if old is None:return err(404,'not found')
 if b.get('role')=='owner':return err(400,'invalid role')
 if old=='owner' or rr not in ('owner','admin') or (rr=='admin' and old=='admin'):return err(403,'forbidden')
 try:core.set_member_role(w['id'],target,b.get('role'))
 except ValueError:return err(400,'invalid role')
 return out({'member':{'user_id':target,'role':b['role']}})
async def transfer(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if w['owner_id']!=u['id']:return err(403,'forbidden')
 try:core.transfer_ownership(w['id'],int(b['new_owner_id']))
 except ValueError:return err(400,'target must be admin')
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invite_create(r):
 try:u=user(r);b=await r.json()
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 try:x=core.create_invitation(w['id'],u['id'],b)
 except:return err(400,'invalid')
 return out({'invitation':core.invitation_to_json(x)},201)
async def invite_list(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 c=core.connect();xs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(w['id'],core.now_iso())).fetchall();c.close()
 return out({'invitations':[core.invitation_to_json(x) for x in xs]})
async def invite_accept(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 try:core.accept_invitation(u['id'],r.match_info['code'])
 except PermissionError:return err(403,'forbidden')
 except:return err(404,'not found')
 return out({'accepted':True})
async def invite_revoke(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 if role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE invitations SET revoked=1 WHERE workspace_id=? AND code=?',(w['id'],r.match_info['code']))
 return out({'deleted':True})
async def archive(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 if role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
 active=(r.match_info['action']=='archive')
 if bool(ch['is_archived'])==active:return out({'channel':core.channel_to_json(ch)})
 with core.tx() as c:
  c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(active),ch['id']))
  core.emit_event(c,ch['id'],'channel.archived' if active else 'channel.unarchived',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
 return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def ch_members(r):
 try:u=user(r)
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 c=core.connect();xs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
 return out({'members':[core.user_to_json(x) for x in xs]})
async def pins(r):
 try:u=user(r);lim,cur=page(r.query,'pin')
 except web.HTTPUnauthorized:return err(401,'unauthorized')
 except:return err(400,'invalid paging')
 ch=channel_or(r.match_info['id'])
 if not ch:return err(404,'not found')
 if not access(u,ch):return err(403,'forbidden')
 c=core.connect();q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';args=[ch['id']]
 if cur:q+=' AND p.message_id<?';args.append(cur)
 q+=' ORDER BY p.message_id DESC LIMIT ?';xs=c.execute(q,args+[lim+1]).fetchall();c.close()
 return out({'pins':[{'message':core.msg_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in xs[:lim]],'next_cursor':core.encode_cursor('pin',xs[lim-1]['message_id']) if len(xs)>lim else None})

def make():
 a=web.Application()
 for method,path,fn in [('GET','/api/health',health),('POST','/api/auth/register',register),('POST','/api/auth/login',login),('GET','/api/auth/me',me),('GET','/api/workspaces',workspaces),('POST','/api/workspaces',create_ws),('GET','/api/workspaces/{slug}',ws_detail),('GET','/api/workspaces/{slug}/members',ws_members),('PATCH','/api/workspaces/{slug}',patch_ws),('POST','/api/workspaces/{slug}/channels',create_channel),('POST','/api/channels/{id}/join',join),('POST','/api/channels/{id}/leave',leave),('PATCH','/api/channels/{id}',channel_patch),('GET','/api/channels/{id}/messages',messages),('POST','/api/channels/{id}/messages',post_message),('GET','/api/messages/{id}/replies',replies),('PATCH','/api/messages/{id}',edit),('DELETE','/api/messages/{id}',delete),('POST','/api/messages/{id}/reactions',react),('DELETE','/api/messages/{id}/reactions/{emoji}',unreact),('POST','/api/messages/{id}/pin',pin),('DELETE','/api/messages/{id}/pin',unpin),('GET','/api/search',search),('GET','/api/channels/{id}/read',read),('POST','/api/channels/{id}/read',read),('GET','/api/ws',ws_handler),('PATCH','/api/workspaces/{slug}/members/{user_id}',patch_member),('POST','/api/workspaces/{slug}/transfer_ownership',transfer),('POST','/api/workspaces/{slug}/invitations',invite_create),('GET','/api/workspaces/{slug}/invitations',invite_list),('POST','/api/invitations/{code}/accept',invite_accept),('DELETE','/api/workspaces/{slug}/invitations/{code}',invite_revoke),('POST','/api/channels/{id}/{action:archive|unarchive}',archive),('GET','/api/channels/{id}/members',ch_members),('GET','/api/channels/{id}/pins',pins)]:a.router.add_route(method,path,fn)
 return a
if __name__=='__main__':
 core.init_db();p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;web.run_app(make(),host='127.0.0.1',port=8000+NODE)
