import argparse, asyncio, json, re
from aiohttp import web, WSMsgType
import core

NODE=0
def out(data,status=200):
    return web.json_response(data,status=status)
def err(status,msg):
    return out({'error':msg},status)
async def body(req):
    try:
        x=await req.json()
        if not isinstance(x,dict): raise ValueError()
        return x
    except: raise web.HTTPException(reason='bad json')
def token(req):
    h=req.headers.get('Authorization','')
    return h[7:] if h.startswith('Bearer ') else None
def who(req):
    u=core.user_for_token(token(req))
    if not u: raise PermissionError('auth')
    return u
def auth(req):
    try:return who(req)
    except: return None
def validate_role(req,wid,uid,admin=False):
    r=core.member_role(wid,uid)
    if r not in (('owner','admin') if admin else ('owner','admin','member','guest')): return False
    return r
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
    try:
        d=await body(req); un=d.get('username'); pw=d.get('password'); dn=d.get('display_name',un)
        if not isinstance(un,str) or not isinstance(pw,str) or not isinstance(dn,str): return err(400,'invalid input')
        if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return err(400,'invalid input')
        with core.tx() as c:
            try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,core.hash_password(pw),dn))
            except: return err(409,'username exists')
            uid=c.execute("SELECT last_insert_rowid()").fetchone()[0]; t=core.issue_token(c,uid); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        return out({'user':core.user_to_json(u),'token':t},201)
    except:return err(400,'invalid input')
async def login(req):
    try:
        d=await body(req); un=d.get('username'); pw=d.get('password')
        if not isinstance(un,str) or not isinstance(pw,str): return err(400,'invalid input')
        c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(un,)).fetchone()
        if not u or not core.verify_password(pw,u['password']): c.close(); return err(401,'invalid credentials')
        t=core.issue_token(c,u['id']); c.close(); return out({'user':core.user_to_json(u),'token':t})
    except:return err(400,'invalid input')
async def me(req):
    u=auth(req)
    return err(401,'unauthorized') if not u else out({'user':core.user_to_json(u)})
async def workspaces(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    c=core.connect(); rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return out({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_ws(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:
        d=await body(req); s=d.get('slug'); n=d.get('name')
        if not isinstance(s,str) or not isinstance(n,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',s) or not n:return err(400,'invalid input')
        try:w,ch=core.create_workspace(u['id'],s,n)
        except: return err(409,'workspace exists')
        return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
    except:return err(400,'invalid input')
def getws(slug): return core.get_workspace(slug)
async def members(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out({'members':[dict(x) for x in rs]})
async def ws_detail(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],req.query.get('include_archived') in ('1','true'))).fetchall(); c.close()
    return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs],'read_state':{}})
async def create_channel(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin','member'):return err(403,'forbidden')
    try:
        d=await body(req); n=d.get('name'); pr=d.get('is_private',False); topic=d.get('topic','')
        if not isinstance(n,str) or not isinstance(pr,bool) or not isinstance(topic,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',n) or len(topic)>250:return err(400,'invalid input')
        if pr and role not in ('owner','admin'):return err(403,'forbidden')
        with core.tx() as c:
            try:ch=core.create_channel_row(c,w['id'],n,pr,topic,u['id'])
            except:return err(409,'channel exists')
        return out({'channel':core.channel_to_json(ch)},201)
    except:return err(400,'invalid input')
async def join(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:return out({'channel':core.channel_to_json(core.join_channel(u['id'],int(req.match_info['id'])))})
    except KeyError:return err(404,'not found')
    except:return err(403,'forbidden')
async def patch_member(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return err(403,'forbidden')
    try:
        d=await body(req); r=d.get('role'); tid=int(req.match_info['user_id'])
        if not isinstance(r,str):return err(400,'invalid input')
        return out({'member':core.set_member_role(w['slug'],u['id'],tid,r)})
    except KeyError:return err(404,'not found')
    except ValueError:return err(400,'invalid role')
    except:return err(403,'forbidden')
async def transfer(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    try:
        d=await body(req); tid=d.get('new_owner_id')
        if not isinstance(tid,int):return err(400,'invalid input')
        return out({'workspace':core.workspace_to_json(core.transfer(w['slug'],u['id'],tid))})
    except ValueError:return err(400,'target must be admin')
    except:return err(403,'forbidden')
async def patch_ws(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    try:
        d=await body(req)
        if 'name' in d and (not isinstance(d['name'],str) or not d['name']):return err(400,'invalid name')
        if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return err(400,'invalid join mode')
        with core.tx() as c:
            sets=[]; vals=[]
            for k in ('name','join_mode'):
                if k in d:sets.append(k+'=?');vals.append(d[k])
            if sets:c.execute('UPDATE workspaces SET '+','.join(sets)+' WHERE id=?',(*vals,w['id']))
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
        return out({'workspace':core.workspace_to_json(w)})
    except:return err(400,'invalid input')
async def invitations(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND (expires_at IS NULL OR expires_at>?) AND uses<max_uses',(w['id'],__import__('time').time())).fetchall(); c.close()
    if req.method=='GET':return out({'invitations':[core.invitation_json(x) for x in rs]})
    try:return out({'invitation':core.invitation_json(core.create_invite(w['slug'],u['id'],await body(req)))},201)
    except KeyError:return err(404,'user not found')
    except ValueError:return err(400,'invalid input')
    except:return err(400,'invalid input')
async def accept(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:return out({'workspace':core.workspace_to_json(core.accept_invite(u['id'],req.match_info['code']))})
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
async def revoke(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=getws(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:
        x=c.execute('SELECT id FROM invitations WHERE workspace_id=? AND code=?',(w['id'],req.match_info['code'])).fetchone()
        if not x:return err(404,'not found')
        c.execute('UPDATE invitations SET revoked=1 WHERE id=?',(x['id'],))
    return out({'deleted':True})
async def ws(req):
    if not auth(req):return web.Response(status=401)
    x=web.WebSocketResponse(); await x.prepare(req)
    await x.send_json({'type':'ready'})
    async for m in x:
        if m.type==WSMsgType.TEXT:
            try:
                d=json.loads(m.data)
                if d.get('type')=='subscribe': await x.send_json({'type':'subscribed','channel_id':d.get('channel_id'),'head_event_id':0})
            except: pass
    return x
app=web.Application()
app.router.add_get('/api/health',health); app.router.add_post('/api/auth/register',register); app.router.add_post('/api/auth/login',login); app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces); app.router.add_post('/api/workspaces',create_ws)
app.router.add_get('/api/workspaces/{slug}',ws_detail); app.router.add_patch('/api/workspaces/{slug}',patch_ws); app.router.add_get('/api/workspaces/{slug}/members',members)
app.router.add_post('/api/workspaces/{slug}/channels',create_channel); app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member); app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
app.router.add_get('/api/workspaces/{slug}/invitations',invitations); app.router.add_post('/api/workspaces/{slug}/invitations',invitations); app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke)
app.router.add_post('/api/invitations/{code}/accept',accept); app.router.add_post('/api/channels/{id}/join',join); app.router.add_get('/api/ws',ws)
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    core.init_db(); web.run_app(app,host='127.0.0.1',port=8000+NODE)
