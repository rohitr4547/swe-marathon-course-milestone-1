import asyncio, argparse, json, re
from aiohttp import web
import core

def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def body(req):
    try:
        x=await req.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(req):
    h=req.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
def ok(x,status=200): return web.json_response(x,status=status)
def role_ok(w,u,roles=('owner','admin')):
    return core.member_role(w['id'],u['id']) in roles
def valid_slug(s): return isinstance(s,str) and re.fullmatch(r'[a-z0-9-]{2,32}',s) is not None
def valid_uname(s): return isinstance(s,str) and re.fullmatch(r'[A-Za-z0-9_]+',s) is not None

async def health(req):
    return ok({'status':'ok','node_id':req.app['node']})
async def register(req):
    b=await body(req)
    if not b or not isinstance(b.get('username'),str) or not valid_uname(b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
    if 'display_name' in b and not isinstance(b['display_name'],str):return json_error(400,'invalid display_name')
    try:
        with core.tx() as c:
            c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name',b['username'])))
            u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
    except Exception as e:return json_error(409,'username exists')
    return ok({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    b=await body(req)
    if not b or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str):return json_error(400,'invalid login')
    u=core.user_by_name(b['username'])
    if not u or not core.verify_password(b['password'],u['password']):return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return ok({'user':core.user_to_json(u),'token':t})
async def me(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    return ok({'user':core.user_to_json(u)})
async def workspaces(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return ok({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    b=await body(req)
    if not b or not valid_slug(b.get('slug')) or not isinstance(b.get('name'),str) or not b['name']:return json_error(400,'invalid workspace')
    try:
        with core.tx() as c:
            c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']))
            w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
            ch=core.create_channel_row(c,w['id'],'general',False,'',u['id']);core.add_workspace_member(c,w['id'],u['id'],'owner')
    except: return json_error(409,'duplicate slug')
    return ok({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
async def workspace(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'not a member')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],req.query.get('include_archived') in ('1','true'))).fetchall();c.close()
    return ok({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':{}})
async def members(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'not a member')
    c=core.connect(); rs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
    return ok({'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in rs]})
async def create_channel(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    r=core.member_role(w['id'],u['id'])
    if not r:return json_error(403,'not a member')
    if r=='guest':return json_error(403,'guest')
    b=await body(req)
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']) or ('is_private' in b and not isinstance(b['is_private'],bool)) or ('topic' in b and (not isinstance(b['topic'],str) or len(b['topic'])>250)):return json_error(400,'invalid channel')
    if b.get('is_private') and r not in ('owner','admin'):return json_error(403,'private channels restricted')
    try:
        with core.tx() as c: ch=core.create_channel_row(c,w['id'],b['name'],b.get('is_private',False),b.get('topic',''),u['id'])
    except:return json_error(409,'duplicate channel')
    return ok({'channel':core.channel_to_json(ch)},201)
async def join(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    cid=int(req.match_info['id']) if req.match_info['id'].isdigit() else 0
    s,e=core.join_channel(u,cid)
    return json_error(s,e) if s!=200 else ok({'channel':core.channel_to_json(core.get_channel(cid))})
async def patch_member(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug']); tid=req.match_info['user_id']
    if not w:return json_error(404,'workspace not found')
    try: tid=int(tid)
    except:return json_error(400,'invalid user id')
    target=core.user_for_id(tid)
    if not target or not core.member_role(w['id'],tid):return json_error(404,'member not found')
    actor=core.member_role(w['id'],u['id']); old=core.member_role(w['id'],tid); b=await body(req)
    if actor not in ('owner','admin'):return json_error(403,'forbidden')
    if not b or 'role' not in b or not isinstance(b.get('role'),str) or b['role'] not in ('owner','admin','member','guest'):return json_error(400,'invalid role')
    if b['role']=='owner':return json_error(400,'invalid role')
    if tid==w['owner_id'] or (actor=='admin' and old in ('owner','admin')):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],tid))
    return ok({'member':{'user_id':tid,'role':b['role']}})
async def transfer(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if u['id']!=w['owner_id']:return json_error(403,'owner only')
    b=await body(req)
    if not b or not isinstance(b.get('new_owner_id'),int):return json_error(400,'invalid target')
    tid=b['new_owner_id']; r=core.member_role(w['id'],tid)
    if r!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],tid))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(tid,w['id']))
    return ok({'workspace':core.workspace_to_json(core.get_workspace(req.match_info['slug']))})
async def patch_workspace(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if not role_ok(w,u):return json_error(403,'admin only')
    b=await body(req)
    if not b:return json_error(400,'invalid body')
    if 'name' in b and (not isinstance(b['name'],str) or not b['name']):return json_error(400,'invalid name')
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join mode')
    if any(k not in ('name','join_mode') for k in b):return json_error(400,'invalid field')
    with core.tx() as c:
        if 'name' in b:c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
        if 'join_mode' in b:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
    return ok({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invitations(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if not role_ok(w,u):return json_error(403,'admin only')
    c=core.connect(); rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses',(w['id'],)).fetchall();c.close()
    import datetime
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    rs=[x for x in rs if not x['expires_at'] or x['expires_at']>now]
    return ok({'invitations':[core.invitation_to_json(x) for x in rs]})
async def create_invite(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    b=await body(req)
    if not isinstance(b,dict):return json_error(400,'invalid body')
    status,x=core.create_invitation(u,w['slug'],b)
    return json_error(status,'invitation error') if status!=201 else ok({'invitation':x},201)
async def accept(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    status,x=core.accept_invitation(u,req.match_info['code'])
    return json_error(status,'invitation unavailable') if status!=200 else ok({'workspace':x})
async def revoke(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if not role_ok(w,u):return json_error(403,'admin only')
    c=core.connect(); x=c.execute('SELECT * FROM invitations WHERE code=? AND workspace_id=?',(req.match_info['code'],w['id'])).fetchone();c.close()
    if not x:return json_error(404,'invitation not found')
    with core.tx() as t:t.execute('UPDATE invitations SET revoked=1 WHERE code=?',(x['code'],))
    return ok({'deleted':True})
async def channel(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    try:cid=int(req.match_info['id'])
    except:return json_error(404,'channel not found')
    ch=core.get_channel(cid)
    if not ch:return json_error(404,'channel not found')
    if not core.member_role(ch['workspace_id'],u['id']):return json_error(403,'not a member')
    return ok({'channel':core.channel_to_json(ch)})
async def ws(req):
    token=req.query.get('token'); u=core.user_for_token(token)
    if not u:return web.Response(status=401)
    w=web.WebSocketResponse(); await w.prepare(req)
    async for msg in w:
        if msg.type==web.WSMsgType.TEXT:
            try:
                x=json.loads(msg.data)
                if x.get('type')=='subscribe':
                    cid=int(x['channel_id']); ch=core.get_channel(cid)
                    if ch:
                        await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':ch['last_event_id']})
            except: pass
    return w
def app(node):
    a=web.Application();a['node']=node
    a.router.add_get('/api/health',health)
    a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace)
    a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_get('/api/workspaces/{slug}/members',members)
    a.router.add_patch('/api/workspaces/{slug}',patch_workspace)
    a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member)
    a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
    a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/workspaces/{slug}/invitations',create_invite);a.router.add_get('/api/workspaces/{slug}/invitations',invitations)
    a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke)
    a.router.add_post('/api/invitations/{code}/accept',accept)
    a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}',channel)
    a.router.add_get('/api/ws',ws)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);n=p.parse_args().node
    web.run_app(app(n),host='127.0.0.1',port=8000+n)
