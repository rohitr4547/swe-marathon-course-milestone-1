import argparse,json,re,datetime,secrets
from aiohttp import web
import core
NODE=0
def out(d,status=200): return web.json_response(d,status=status)
def err(s,m): return out({'error':m},s)
@web.middleware
async def mw(req,handler):
 try:return await handler(req)
 except web.HTTPException as e:return err(e.status, e.reason)
 except Exception as e:return err(500,str(e))
def auth(req):
 x=req.headers.get('Authorization','')
 if not x.startswith('Bearer '): raise web.HTTPUnauthorized()
 u=core.user_for_token(x[7:])
 if not u: raise web.HTTPUnauthorized()
 return u
async def body(req):
 try:return await req.json()
 except:return {}
def slugok(s):return isinstance(s,str) and re.fullmatch(r'[a-z0-9-]{2,32}',s)!=None
def user_required(req):
 try:return auth(req)
 except web.HTTPUnauthorized: raise
async def health(req):return out({'status':'ok','node_id':NODE})
async def register(req):
 b=await body(req); u=b.get('username'); p=b.get('password')
 if not isinstance(u,str) or not re.fullmatch(r'[A-Za-z0-9_]+',u) or not isinstance(p,str) or len(p)<8:return err(400,'invalid registration')
 with core.tx() as c:
  try:cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,core.hash_password(p),b.get('display_name') or u))
  except: return err(409,'username already exists')
  r=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.issue_token(c,r['id'])
 return out({'user':core.user_to_json(r),'token':t},201)
async def login(req):
 b=await body(req); c=core.connect(); r=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone(); c.close()
 if not r or not core.verify_password(b.get('password',''),r['password']):return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,r['id'])
 return out({'user':core.user_to_json(r),'token':t})
async def me(req):return out({'user':core.user_to_json(user_required(req))})
async def workspaces(req):
 u=user_required(req); c=core.connect(); rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close(); return out({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_ws(req):
 u=user_required(req); b=await body(req)
 if not slugok(b.get('slug')) or not isinstance(b.get('name'),str) or not b['name']:return err(400,'invalid workspace')
 with core.tx() as c:
  try:
   cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); w=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(w,u['id'],'owner'));g=core.init_general(c,w,u['id']); wr=c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()
  except: return err(409,'duplicate workspace')
 return out({'workspace':core.workspace_to_json(wr),'general_channel':core.channel_to_json(g)},201)
def ws_access(req,slug):
 u=user_required(req); w=core.get_workspace(slug)
 if not w:raise web.HTTPNotFound()
 return u,w
async def ws_detail(req):
 u,w=ws_access(req,req.match_info['slug']); role=core.member_role(w['id'],u['id']); c=core.connect(); rs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],req.query.get('include_archived') in ('1','true'))).fetchall();c.close()
 return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rs],'read_state':{}})
async def members(req):
 u,w=ws_access(req,req.match_info['slug'])
 if not core.member_role(w['id'],u['id']): return err(403,'workspace membership required')
 c=core.connect();rs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close();return out({'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in rs]})
async def channels(req):
 u,w=ws_access(req,req.match_info['slug']); role=core.member_role(w['id'],u['id']); b=await body(req)
 if role not in ('owner','admin','member') or (b.get('is_private',False) and role not in ('owner','admin')):return err(403,'forbidden')
 name=b.get('name')
 if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',name) or name=='general':return err(400,'invalid channel')
 with core.tx() as c:
  try:r=core.create_channel_row(c,w['id'],name,bool(b.get('is_private')),b.get('topic',''),u['id'])
  except:return err(409,'duplicate channel')
 return out({'channel':core.channel_to_json(r)},201)
async def join(req):
 u=user_required(req); ch=core.get_channel(int(req.match_info['id']))
 if not ch:raise web.HTTPNotFound()
 role=core.member_role(ch['workspace_id'],u['id']); w=core.get_workspace_by_id(ch['workspace_id']) if hasattr(core,'get_workspace_by_id') else None
 if not w:
  c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
 if ch['is_private'] or role=='guest' or (not role and w['join_mode']=='invite_only'):return err(403,'forbidden')
 with core.tx() as c:
  if not role:core.add_workspace_member(c,w['id'],u['id'],'member')
  c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
 return out({'channel':core.channel_to_json(ch)})
async def patch_member(req):
 u,w=ws_access(req,req.match_info['slug']); actor=core.member_role(w['id'],u['id']); target=int(req.match_info['uid']); b=await body(req); role=b.get('role')
 if actor not in ('owner','admin'):return err(403,'forbidden')
 c=core.connect(); old=c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone();c.close()
 if not old:return err(404,'member not found')
 if role not in ('owner','admin','member','guest') or role=='owner':return err(400,'invalid role')
 if target==w['owner_id'] or (actor=='admin' and old['role']=='admin'):return err(403,'forbidden')
 with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target))
 return out({'member':{'user_id':target,'role':role}})
async def transfer(req):
 u,w=ws_access(req,req.match_info['slug']);b=await body(req)
 if u['id']!=w['owner_id']:return err(403,'owner only')
 target=b.get('new_owner_id');c=core.connect();x=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone();c.close()
 if not x or x['role']!='admin':return err(400,'target must be admin')
 with core.tx() as c:c.execute('UPDATE members SET role=CASE WHEN user_id=? THEN "owner" ELSE CASE WHEN user_id=? THEN "admin" ELSE role END END WHERE workspace_id=?',(target,u['id'],w['id']));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
 return out({'workspace':core.workspace_to_json(core.get_workspace(req.match_info['slug']))})
async def patch_ws(req):
 u,w=ws_access(req,req.match_info['slug'])
 if u['id']!=w['owner_id'] and core.member_role(w['id'],u['id'])!='admin':return err(403,'forbidden')
 b=await body(req)
 if 'join_mode' in b and b['join_mode'] not in ('open','invite_only') or 'name' in b and not b['name']:return err(400,'invalid settings')
 with core.tx() as c:
  sets=[];vals=[]
  for k in ('name','join_mode'):
   if k in b:sets.append(k+'=?');vals.append(b[k])
  if sets:c.execute('UPDATE workspaces SET '+','.join(sets)+' WHERE id=?',vals+[w['id']])
 return out({'workspace':core.workspace_to_json(core.get_workspace(req.match_info['slug']))})
async def invitations(req):
 u,w=ws_access(req,req.match_info['slug']);role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0',(w['id'],)).fetchall();c.close()
 return out({'invitations':[core.invitation_to_json(x) for x in rs if core.valid_inv(x)]})
async def create_inv(req):
 u,w=ws_access(req,req.match_info['slug']);role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return err(403,'forbidden')
 b=await body(req); mx=b.get('max_uses',1); ex=b.get('expires_in_seconds'); target=b.get('invited_user_id')
 if not isinstance(mx,int) or mx<=0 or (ex is not None and (not isinstance(ex,int) or ex<=0)):return err(400,'invalid invitation')
 c=core.connect(); target=target or None
 if b.get('invited_username'):
  q=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();target=q['id'] if q else None
 if ('invited_username' in b or 'invited_user_id' in b) and not target:c.close();return err(404,'user not found')
 c.close(); code=secrets.token_urlsafe(18); expires=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=ex)).isoformat(timespec='milliseconds').replace('+00:00','Z') if ex else None
 with core.tx() as c:
  c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,0)',(code,w['id'],target,1 if target else mx,0,expires,core.now_iso()))
  r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
 return out({'invitation':core.invitation_to_json(r)},201)
async def accept(req):
 u=user_required(req);c=core.connect();r=c.execute('SELECT * FROM invitations WHERE code=?',(req.match_info['code'],)).fetchone();c.close()
 if not core.valid_inv(r):return err(404,'invitation not found')
 if r['invited_user_id'] and r['invited_user_id']!=u['id']:return err(403,'targeted invitation')
 with core.tx() as c:
  if not core.member_role(r['workspace_id'],u['id']):
   core.add_workspace_member(c,r['workspace_id'],u['id'],'member')
  c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(r['code'],))
 w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()
 return out({'workspace':core.workspace_to_json(w)})
async def revoke(req):
 u,w=ws_access(req,req.match_info['slug']);role=core.member_role(w['id'],u['id'])
 if role not in ('owner','admin'):return err(403,'forbidden')
 with core.tx() as c:
  r=c.execute('SELECT * FROM invitations WHERE code=? AND workspace_id=?',(req.match_info['code'],w['id'])).fetchone()
  if not r:return err(404,'not found')
  c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(r['code'],))
 return out({'deleted':True})
async def ws(req):
 user=user_required(req); q=req.query.get('token')
 if q and not core.user_for_token(q):return err(401,'invalid token')
 return web.Response(status=501)
def app():
 a=web.Application(middlewares=[mw]);a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/channels',channels);a.router.add_patch('/api/workspaces/{slug}',patch_ws);a.router.add_patch('/api/workspaces/{slug}/members/{uid}',patch_member);a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);a.router.add_get('/api/workspaces/{slug}/invitations',invitations);a.router.add_post('/api/workspaces/{slug}/invitations',create_inv);a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke);a.router.add_post('/api/invitations/{code}/accept',accept);a.router.add_post('/api/channels/{id}/join',join);return a
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
