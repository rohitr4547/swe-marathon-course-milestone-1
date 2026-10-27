import asyncio,argparse,json,re,sqlite3,datetime,secrets
from aiohttp import web,WSMsgType
import core
class E(Exception):
 def __init__(self,s,m):self.s=s;self.m=m
def out(x,s=200):return web.json_response(x,status=s)
def tok(r):
 h=r.headers.get('Authorization','');return h[7:] if h.startswith('Bearer ') else None
def usr(r):
 u=core.user_for_token(tok(r))
 if not u:raise E(401,'unauthorized')
 return u
def ch(i):
 x=core.get_channel(i)
 if not x:raise E(404,'not found')
 return x
def ws(slug):
 x=core.get_workspace(slug)
 if not x:raise E(404,'not found')
 return x
def access(u,x):
 if not core.can_read_channel(u['id'],x):raise E(403,'forbidden')
def wrole(u,w):
 x=core.member_role(w['id'],u['id'])
 if not x:raise E(403,'forbidden')
 return x
@web.middleware
async def mw(r,handler):
 try:return await handler(r)
 except E as e:return out({'error':e.m},e.s)
 except PermissionError:return out({'error':'forbidden'},403)
 except LookupError:return out({'error':'not found'},404)
 except ValueError as e:return out({'error':str(e)},400)
 except RuntimeError as e:return out({'error':str(e)},423 if str(e)!='deleted' else 409)
 except sqlite3.IntegrityError:return out({'error':'conflict'},409)
 except Exception as e:return out({'error':str(e)},500)
async def health(r):return out({'status':'ok','node_id':r.app['node']})
async def register(r):
 b=await r.json();u=b.get('username');p=b.get('password')
 if not isinstance(u,str) or not re.fullmatch(r'[A-Za-z0-9_]+',u):raise E(400,'invalid username')
 if not isinstance(p,str) or len(p)<8:raise E(400,'invalid password')
 with core.tx() as c:
  if c.execute('select 1 from users where username=?',(u,)).fetchone():raise E(409,'username exists')
  q=c.execute('insert into users(username,password,display_name) values(?,?,?)',(u,core.hash_password(p),b.get('display_name') or u));i=q.lastrowid;t=core.issue_token(c,i);x=c.execute('select * from users where id=?',(i,)).fetchone()
 return out({'user':core.user_to_json(x),'token':t},201)
async def login(r):
 b=await r.json();c=core.connect();x=c.execute('select * from users where username=?',(b.get('username'),)).fetchone();c.close()
 if not x or not core.verify_password(b.get('password',''),x['password']):raise E(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,x['id'])
 return out({'user':core.user_to_json(x),'token':t})
async def me(r):return out({'user':core.user_to_json(usr(r))})
async def listw(r):
 u=usr(r);c=core.connect();a=c.execute('select w.* from workspaces w join memberships m on m.workspace_id=w.id where m.user_id=?',(u['id'],)).fetchall();c.close();return out({'workspaces':[core.workspace_to_json(x) for x in a]})
async def makew(r):
 u=usr(r);b=await r.json();s=b.get('slug')
 if not isinstance(s,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',s) or not b.get('name'):raise E(400,'invalid workspace')
 with core.tx() as c:
  if c.execute('select 1 from workspaces where slug=?',(s,)).fetchone():raise E(409,'duplicate slug')
  c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(s,b['name'],u['id']));i=c.execute('select last_insert_rowid()').fetchone()[0];core.add_workspace_member(c,i,u['id'],'owner');g=core.create_channel_row(c,i,'general',0,'',u['id'])
 return out({'workspace':core.workspace_to_json(core.get_workspace(s)),'general_channel':core.channel_to_json(g)},201)
async def detail(r):
 u=usr(r);w=ws(r.match_info['slug']);wrole(u,w);inc=r.query.get('include_archived') in ('1','true');c=core.connect();q='select * from channels where workspace_id=?'+('' if inc else ' and is_archived=0');a=c.execute(q,(w['id'],)).fetchall();c.close();a=[x for x in a if not x['is_private'] or core._cm(x['id'],u['id']) or w['owner_id']==u['id']];return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in a],'read_state':[core.read_state(u['id'],x['id']) for x in a]})
async def wmembers(r):
 u=usr(r);w=ws(r.match_info['slug']);wrole(u,w);c=core.connect();a=c.execute('select m.*,u.username,u.display_name from memberships m join users u on u.id=m.user_id where m.workspace_id=?',(w['id'],)).fetchall();c.close();return out({'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in a]})
async def makech(r):
 u=usr(r);w=ws(r.match_info['slug']);ro=wrole(u,w);b=await r.json();n=b.get('name');pr=bool(b.get('is_private'));tp=b.get('topic','')
 if ro=='guest' or pr and ro not in ('owner','admin'):raise E(403,'forbidden')
 if not isinstance(n,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',n) or len(tp)>250:raise E(400,'invalid channel')
 with core.tx() as c:
  if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],n)).fetchone():raise E(409,'duplicate channel')
  x=core.create_channel_row(c,w['id'],n,pr,tp,u['id'])
 return out({'channel':core.channel_to_json(x)},201)
async def join(r):
 u=usr(r);x=ch(int(r.match_info['id']));return out({'channel':core.channel_to_json(core.join_channel(u,x['id']))})
async def leave(r):
 u=usr(r);x=ch(int(r.match_info['id']))
 with core.tx() as c:
  if c.execute('delete from channel_members where channel_id=? and user_id=?',(x['id'],u['id'])).rowcount:core.emit_event(c,x['id'],'member.left',{'user_id':u['id']})
 return out({'channel':core.channel_to_json(x)})
async def cmembers(r):
 u=usr(r);x=ch(int(r.match_info['id']));access(u,x);c=core.connect();a=c.execute('select u.* from channel_members m join users u on u.id=m.user_id where m.channel_id=?',(x['id'],)).fetchall();c.close();return out({'members':[core.user_to_json(z) for z in a]})
async def patchch(r):
 u=usr(r);x=ch(int(r.match_info['id']));w=core.get_workspace_by_id(x['workspace_id'])
 if not core._cm(x['id'],u['id']) and w['owner_id']!=u['id']:raise E(403,'forbidden')
 core.require_writable(x);b=await r.json();t=b.get('topic')
 if not isinstance(t,str) or len(t)>250:raise E(400,'invalid topic')
 with core.tx() as c:c.execute('update channels set topic=? where id=?',(t,x['id']));core.emit_event(c,x['id'],'channel.updated',{})
 return out({'channel':core.channel_to_json(core.get_channel(x['id']))})
async def archive(r):
 u=usr(r);x=ch(int(r.match_info['id']));w=core.get_workspace_by_id(x['workspace_id'])
 if w['owner_id']!=u['id']:raise E(403,'forbidden')
 yes=r.path.endswith('/archive') and not r.path.endswith('/unarchive')
 with core.tx() as c:c.execute('update channels set is_archived=? where id=?',(yes,x['id']));core.emit_event(c,x['id'],'channel.archived' if yes else 'channel.unarchived',{})
 return out({'channel':core.channel_to_json(core.get_channel(x['id']))})
async def msgs(r):
 u=usr(r);x=ch(int(r.match_info['id']));access(u,x);n,c=core.parse_paging(r.query,'msg');a,z=core.list_messages(x['id'],n,c);return out({'messages':a,'next_cursor':z})
async def post(r):
 u=usr(r);x=ch(int(r.match_info['id']));access(u,x);b=await r.json();return out({'message':core.create_message(u,x['id'],b.get('body'),b.get('parent_id'))},201)
async def reps(r):
 u=usr(r);m=core.get_message(int(r.match_info['id']))
 if not m:raise E(404,'not found')
 x=ch(m['channel_id']);access(u,x);n,c=core.parse_paging(r.query,'rep');a,z=core.list_replies(x['id'],n,c,m['id']);return out({'replies':a,'next_cursor':z})
async def edit(r):
 u=usr(r);m=core.get_message(int(r.match_info['id']))
 if not m:raise E(404,'not found')
 return out({'message':core.edit_message(u['id'],m['id'],(await r.json()).get('body'))})
async def delete(r):
 u=usr(r);m=core.get_message(int(r.match_info['id']))
 if not m:raise E(404,'not found')
 core.delete_message(u['id'],m['id']);return out({'deleted':True})
async def react(r):
 u=usr(r);m=core.get_message(int(r.match_info['id']))
 if not m:raise E(404,'not found')
 x=ch(m['channel_id']);access(u,x)
 emoji=(await r.json()).get('emoji') if r.method=='POST' else r.match_info['emoji']
 return out({'reactions':core.react(u['id'],m['id'],emoji,r.method=='POST')})
async def pin(r):
 u=usr(r);m=core.get_message(int(r.match_info['id']))
 if not m:raise E(404,'not found')
 x=ch(m['channel_id']);access(u,x)
 if r.method=='POST':return out({'pin':core.pin(u['id'],m['id'])})
 core.unpin(m['id']);return out({'unpinned':True})
async def pins(r):
 u=usr(r);x=ch(int(r.match_info['id']));access(u,x);n,c=core.parse_paging(r.query,'pin');a,z=core.list_pins(x['id'],n,c);return out({'pins':a,'next_cursor':z})
async def search(r):
 u=usr(r);q=r.query.get('q')
 if not q:raise E(400,'missing q')
 n,c=core.parse_paging(r.query,'srch');wid=int(r.query['workspace_id']) if r.query.get('workspace_id') else None;a,z=core.search(u['id'],q,n,c,wid);return out({'results':a,'next_cursor':z})
async def read(r):
 u=usr(r);x=ch(int(r.match_info['id']));access(u,x)
 if r.method=='GET':return out({'read_state':core.read_state(u['id'],x['id'])})
 return out({'read_state':core.mark_read(u['id'],x['id'],(await r.json()).get('last_read_event_id'))})
async def wsock(r):
 u=core.user_for_token(r.query.get('token'))
 if not u:raise E(401,'unauthorized')
 s=web.WebSocketResponse();await s.prepare(r);sub={}
 async def poll():
  while not s.closed:
   for i,last in list(sub.items()):
    for e in core.events_after(i,last):await s.send_json(e);sub[i]=e['event_id']
   await asyncio.sleep(.05)
 t=asyncio.create_task(poll())
 try:
  async for q in s:
   if q.type!=WSMsgType.TEXT:continue
   b=json.loads(q.data);i=int(b.get('channel_id',0))
   if b.get('type')=='subscribe':sub[i]=core.head_event_id(i);await s.send_json({'type':'subscribed','channel_id':i,'head_event_id':sub[i]})
   elif b.get('type')=='resume':
    sub[i]=int(b.get('since_event_id',0))
    for e in core.events_after(i,sub[i]):await s.send_json(e);sub[i]=e['event_id']
    await s.send_json({'type':'resumed','channel_id':i,'head_event_id':core.head_event_id(i)})
 finally:t.cancel()
 return s
async def patchw(r):
 u=usr(r);w=ws(r.match_info['slug']);ro=wrole(u,w)
 if ro not in ('owner','admin'):raise E(403,'forbidden')
 b=await r.json()
 if b.get('join_mode',w['join_mode']) not in ('open','invite_only'):raise E(400,'invalid workspace')
 with core.tx() as c:c.execute('update workspaces set name=?,join_mode=? where id=?',(b.get('name',w['name']),b.get('join_mode',w['join_mode']),w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def patchmember(r):
 u=usr(r);w=ws(r.match_info['slug']);ro=wrole(u,w);i=int(r.match_info['user_id']);b=await r.json();nr=b.get('role')
 if i==u['id'] and ro=='owner' or ro not in ('owner','admin'):raise E(403,'forbidden')
 if nr not in ('admin','member','guest'):raise E(400,'invalid role')
 with core.tx() as c:
  x=c.execute('select role from memberships where workspace_id=? and user_id=?',(w['id'],i)).fetchone()
  if not x:raise E(404,'member not found')
  if ro=='admin' and x['role'] in ('admin','owner'):raise E(403,'forbidden')
  c.execute('update memberships set role=? where workspace_id=? and user_id=?',(nr,w['id'],i))
 return out({'member':{'user_id':i,'role':nr}})
async def transfer(r):
 u=usr(r);w=ws(r.match_info['slug'])
 if w['owner_id']!=u['id']:raise E(403,'forbidden')
 i=(await r.json()).get('new_owner_id')
 with core.tx() as c:
  x=c.execute('select role from memberships where workspace_id=? and user_id=?',(w['id'],i)).fetchone()
  if not x or x['role']!='admin':raise E(400,'target must be admin')
  c.execute('update workspaces set owner_id=? where id=?',(i,w['id']));c.execute("update memberships set role=case when user_id=? then 'owner' else 'admin' end where workspace_id=? and user_id in (?,?)",(i,w['id'],u['id'],i))
 return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invitations(r):
 u=usr(r);w=ws(r.match_info['slug']);ro=wrole(u,w)
 if ro not in ('owner','admin'):raise E(403,'forbidden')
 if r.method=='GET':
  c=core.connect();a=c.execute('select * from invitations where workspace_id=? and revoked=0 and uses<max_uses',(w['id'],)).fetchall();c.close()
  now=datetime.datetime.now(datetime.timezone.utc)
  a=[x for x in a if not x['expires_at'] or datetime.datetime.fromisoformat(x['expires_at'].replace('Z','+00:00'))>now]
  return out({'invitations':[dict(x) for x in a]})
 b=await r.json();target=b.get('invited_user_id')
 if target is None and b.get('invited_username'):
  cc=core.connect();rr=cc.execute('select id from users where username=?',(b['invited_username'],)).fetchone();cc.close();target=rr['id'] if rr else None
 code=secrets.token_urlsafe(12);exp=None
 if b.get('expires_in_seconds') is not None:
  try: exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=int(b['expires_in_seconds']))).isoformat()
  except: raise E(400,'invalid expiry')
 with core.tx() as c:c.execute('insert into invitations(code,workspace_id,invited_user_id,expires_at,max_uses) values(?,?,?,?,?)',(code,w['id'],target,exp,1 if target else int(b.get('max_uses',1))))
 c=core.connect();x=c.execute('select * from invitations where code=?',(code,)).fetchone();c.close();return out({'invitation':dict(x)},201)
async def accept_inv(r):
 u=usr(r);code=r.match_info['code'];c=core.connect();x=c.execute('select * from invitations where code=? and revoked=0',(code,)).fetchone();c.close()
 if not x or x['uses']>=x['max_uses']:raise E(404,'invalid invitation')
 if x['expires_at']:
  try:
   if datetime.datetime.fromisoformat(x['expires_at'].replace('Z','+00:00'))<=datetime.datetime.now(datetime.timezone.utc):raise E(404,'invalid invitation')
  except E:raise
  except:raise E(404,'invalid invitation')
 if x['invited_user_id'] and x['invited_user_id']!=u['id']:raise E(403,'invitation targeted to another user')
 with core.tx() as c:
  c.execute('update invitations set uses=uses+1 where code=?',(code,));core.add_workspace_member(c,x['workspace_id'],u['id'],'member')
 return out({'workspace':core.workspace_to_json(core.get_workspace_by_id(x['workspace_id']))})
async def revoke_inv(r):
 u=usr(r);w=ws(r.match_info['slug']);ro=wrole(u,w)
 if ro not in ('owner','admin'):raise E(403,'forbidden')
 with core.tx() as c:
  x=c.execute('select code from invitations where code=? and workspace_id=?',(r.match_info['code'],w['id'])).fetchone()
  if not x:raise E(404,'not found')
  c.execute('update invitations set revoked=1 where code=?',(r.match_info['code'],))
 return out({'deleted':True})
def app(n):
 a=web.Application(middlewares=[mw]);a['node']=n;r=a.router
 r.add_get('/api/health',health);r.add_post('/api/auth/register',register);r.add_post('/api/auth/login',login);r.add_get('/api/auth/me',me)
 r.add_get('/api/workspaces',listw);r.add_post('/api/workspaces',makew);r.add_get('/api/workspaces/{slug}',detail);r.add_patch('/api/workspaces/{slug}',patchw);r.add_get('/api/workspaces/{slug}/members',wmembers);r.add_patch('/api/workspaces/{slug}/members/{user_id}',patchmember);r.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);r.add_post('/api/workspaces/{slug}/channels',makech);r.add_route('*','/api/workspaces/{slug}/invitations',invitations);r.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_inv);r.add_post('/api/invitations/{code}/accept',accept_inv)
 r.add_post('/api/channels/{id}/join',join);r.add_post('/api/channels/{id}/leave',leave);r.add_get('/api/channels/{id}/members',cmembers);r.add_patch('/api/channels/{id}',patchch);r.add_post('/api/channels/{id}/archive',archive);r.add_post('/api/channels/{id}/unarchive',archive);r.add_get('/api/channels/{id}/messages',msgs);r.add_post('/api/channels/{id}/messages',post);r.add_get('/api/channels/{id}/pins',pins);r.add_route('*','/api/channels/{id}/read',read)
 r.add_get('/api/messages/{id}/replies',reps);r.add_patch('/api/messages/{id}',edit);r.add_delete('/api/messages/{id}',delete);r.add_post('/api/messages/{id}/reactions',react);r.add_delete('/api/messages/{id}/reactions/{emoji}',react);r.add_post('/api/messages/{id}/pin',pin);r.add_delete('/api/messages/{id}/pin',pin);r.add_get('/api/search',search);r.add_get('/api/ws',wsock)
 return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);n=p.parse_args().node;web.run_app(app(n),host='127.0.0.1',port=8000+n)
