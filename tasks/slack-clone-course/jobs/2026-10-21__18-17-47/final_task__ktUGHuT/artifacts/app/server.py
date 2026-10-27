import asyncio, argparse, json, re, datetime
from aiohttp import web, WSMsgType
import core
NODE=0
def err(status,msg):
    cls={400:web.HTTPBadRequest,401:web.HTTPUnauthorized,403:web.HTTPForbidden,
         404:web.HTTPNotFound,409:web.HTTPConflict}.get(status,web.HTTPInternalServerError)
    raise cls(reason=msg,headers={'Content-Type':'application/json'})
@web.middleware
async def json_errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as e:
        return web.json_response({'error':e.reason or 'error'},status=e.status)
def out(data,status=200): return web.json_response(data,status=status)
async def body(req):
    try:return await req.json()
    except: err(400,'invalid json')
def user(req):
    h=req.headers.get('Authorization','')
    if not h.startswith('Bearer '): err(401,'unauthorized')
    u=core.user_for_token(h[7:])
    if not u: err(401,'unauthorized')
    return u
def ws_or_404(slug):
    w=core.get_workspace(slug)
    if not w: err(404,'workspace not found')
    return w
def role(w,u):
    return core.member_role(w['id'],u['id'])
def admin(w,u):
    if role(w,u) not in ('owner','admin'): err(403,'forbidden')
def channel_access(ch,u):
    c=core.connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
    if not x: err(403,'forbidden')
def json_channel(c): return core.channel_to_json(c)
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
    d=await body(req); n=d.get('username'); p=d.get('password')
    if not isinstance(n,str) or not re.fullmatch(r'[A-Za-z0-9_]+',n) or not isinstance(p,str) or len(p)<8: err(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(n,)).fetchone(): err(409,'username exists')
        x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(n,core.hash_password(p),d.get('display_name') or n))
        u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    d=await body(req); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone(); c.close()
    if not u or not core.verify_password(d.get('password',''),u['password']): err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t})
async def me(req): return out({'user':core.user_to_json(user(req))})
async def workspaces(req):
    u=user(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    u=user(req); d=await body(req); s=d.get('slug'); n=d.get('name')
    if not isinstance(s,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',s) or not isinstance(n,str) or not n: err(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(s,)).fetchone(): err(409,'slug exists')
        w,g=core.init_user_workspace(c,u['id'],s,n)
    return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},201)
async def workspace(req):
    u=user(req); w=ws_or_404(req.match_info['slug'])
    if not role(w,u): err(403,'forbidden')
    wj,ch=core.workspace_detail(w['id'],u['id']); return out({'workspace':wj,'channels':ch,'read_state':{}})
async def members(req):
    u=user(req); w=ws_or_404(req.match_info['slug'])
    if not role(w,u): err(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out({'members':[dict(x) for x in rs]})
async def create_channel(req):
    u=user(req); w=ws_or_404(req.match_info['slug']); rr=role(w,u)
    if not rr: err(403,'forbidden')
    d=await body(req); n=d.get('name'); private=bool(d.get('is_private',False))
    if rr=='guest' or (private and rr not in ('owner','admin')): err(403,'forbidden')
    if not isinstance(n,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',n): err(400,'invalid channel')
    if d.get('topic') is not None and (not isinstance(d['topic'],str) or len(d['topic'])>250): err(400,'invalid topic')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone(): err(409,'channel exists')
        ch=core.create_channel_row(c,w['id'],n,private,d.get('topic',''),u['id'])
    return out({'channel':core.channel_to_json(ch)},201)
async def join(req):
    u=user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch: err(404,'channel not found')
    w=core.get_workspace(str(ch['workspace_id'])) if False else None
    c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); already=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
    if already:return out({'channel':core.channel_to_json(ch)})
    rr=role(w,u)
    if rr=='guest' or ch['is_private'] or (w['join_mode']=='invite_only' and not rr): err(403,'forbidden')
    with core.tx() as c:
        if not rr: core.add_workspace_member(c,w['id'],u['id'],'member')
        core.add_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def patch_workspace(req):
    u=user(req); w=ws_or_404(req.match_info['slug']); admin(w,u); d=await body(req)
    if 'name' in d and (not isinstance(d['name'],str) or not d['name']):err(400,'invalid name')
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):err(400,'invalid join mode')
    with core.tx() as c:
        vals=[]; args=[]
        for k in ('name','join_mode'):
            if k in d: vals.append(k+'=?');args.append(d[k])
        if vals:c.execute('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',args+[w['id']])
    return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def setrole(req):
    u=user(req); w=ws_or_404(req.match_info['slug']); rr=role(w,u); target=int(req.match_info['user_id'])
    if rr not in ('owner','admin'):err(403,'forbidden')
    d=await body(req); nr=d.get('role')
    if nr not in ('admin','member','guest'):err(400,'invalid role')
    c=core.connect(); tr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone(); c.close()
    if not tr:err(404,'member not found')
    if tr['role']=='owner' or (rr=='admin' and tr['role']=='admin'):err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(nr,w['id'],target))
    return out({'member':{'user_id':target,'role':nr}})
async def transfer(req):
    u=user(req); w=ws_or_404(req.match_info['slug'])
    if role(w,u)!='owner':err(403,'forbidden')
    d=await body(req); tid=d.get('new_owner_id'); c=core.connect(); t=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],tid)).fetchone(); c.close()
    if not t or t['role']!='admin':err(400,'target must be admin')
    with core.tx() as c:
        c.execute("UPDATE members SET role='admin' WHERE workspace_id=? AND user_id=?",(w['id'],u['id']))
        c.execute("UPDATE members SET role='owner' WHERE workspace_id=? AND user_id=?",(w['id'],tid))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(tid,w['id']))
    return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invitations(req):
    u=user(req); w=ws_or_404(req.match_info['slug']); admin(w,u)
    c=core.connect(); rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND (expires_at IS NULL OR expires_at>?) AND uses<max_uses',(w['id'],core.now_iso())).fetchall(); c.close()
    return out({'invitations':[core.invitation_to_json(x) for x in rs]})
async def create_invitation(req):
    u=user(req); w=ws_or_404(req.match_info['slug']); admin(w,u); d=await body(req)
    exp=d.get('expires_in_seconds',None); maxu=d.get('max_uses',1)
    if exp is not None and (not isinstance(exp,int) or isinstance(exp,bool) or exp<=0):err(400,'invalid expiry')
    if not isinstance(maxu,int) or isinstance(maxu,bool) or maxu<=0:err(400,'invalid max uses')
    target=None
    if d.get('invited_user_id') is not None:
        target=core.find_user(str(d['invited_user_id']))
        if not target:err(404,'user not found')
    elif d.get('invited_username') is not None:
        target=core.find_user(d['invited_username'])
        if not target:err(404,'user not found')
    if target:maxu=1
    code=__import__('secrets').token_urlsafe(18)
    created=core.now_iso()
    expires=None
    if exp is not None:
        expires=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=exp)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    with core.tx() as c:
        x=c.execute('INSERT INTO invitations(workspace_id,code,created_at,expires_at,max_uses,invited_user_id) VALUES(?,?,?,?,?,?)',(w['id'],code,created,expires,maxu,target['id'] if target else None))
        row=c.execute('SELECT * FROM invitations WHERE id=?',(x.lastrowid,)).fetchone()
    return out({'invitation':core.invitation_to_json(row)},201)
async def accept_invitation(req):
    u=user(req); code=req.match_info['code']
    with core.tx() as c:
        inv=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0',(code,)).fetchone()
        if not inv:err(404,'invitation not found')
        if inv['invited_user_id'] is not None and inv['invited_user_id']!=u['id']:err(403,'invitation targeted')
        if inv['expires_at'] and inv['expires_at']<=core.now_iso():err(404,'invitation expired')
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(inv['workspace_id'],)).fetchone()
        existing=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
        if not existing:
            if inv['uses']>=inv['max_uses']:err(404,'invitation exhausted')
            core.add_workspace_member(c,w['id'],u['id'],'member')
            c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(inv['id'],))
    return out({'workspace':core.workspace_to_json(w)})
async def revoke_invitation(req):
    u=user(req); w=ws_or_404(req.match_info['slug']); admin(w,u)
    with core.tx() as c:
        inv=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND code=?',(w['id'],req.match_info['code'])).fetchone()
        if not inv:err(404,'invitation not found')
        c.execute('UPDATE invitations SET revoked=1 WHERE id=?',(inv['id'],))
    return out({'deleted':True})
async def channel_members(req):
    user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:err(404,'channel not found')
    return out({'members':core.get_channel_members(ch['id'])})
async def ws_handler(req):
    token=req.query.get('token','')
    if token.startswith('Bearer '):token=token[7:]
    if not core.user_for_token(token):err(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(req)
    try:
        async for msg in ws:
            if msg.type==WSMsgType.TEXT:
                try:d=json.loads(msg.data)
                except:continue
                if d.get('type')=='subscribe':
                    cid=int(d['channel_id']); c=core.connect(); h=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close()
                    if h: await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':h['last_event_id']})
                elif d.get('type')=='resume':
                    cid=int(d['channel_id']); since=int(d.get('since_event_id',0)); c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); h=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close()
                    for r in rows: await ws.send_str(r['payload'])
                    await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':h['last_event_id'] if h else 0})
    finally:return ws
def app():
    a=web.Application(middlewares=[json_errors])
    a.router.add_get('/api/health',health)
    a.router.add_post('/api/auth/register',register); a.router.add_post('/api/auth/login',login); a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces); a.router.add_post('/api/workspaces',create_workspace)
    a.router.add_get('/api/workspaces/{slug}',workspace); a.router.add_patch('/api/workspaces/{slug}',patch_workspace)
    a.router.add_get('/api/workspaces/{slug}/members',members); a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',setrole)
    a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
    a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_get('/api/workspaces/{slug}/invitations',invitations); a.router.add_post('/api/workspaces/{slug}/invitations',create_invitation)
    a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_invitation)
    a.router.add_post('/api/invitations/{code}/accept',accept_invitation)
    a.router.add_post('/api/channels/{id}/join',join); a.router.add_get('/api/channels/{id}/members',channel_members)
    a.router.add_get('/api/ws',ws_handler)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0); args=p.parse_args();NODE=args.node
    core.init_db(); web.run_app(app(),host='127.0.0.1',port=8000+NODE)
