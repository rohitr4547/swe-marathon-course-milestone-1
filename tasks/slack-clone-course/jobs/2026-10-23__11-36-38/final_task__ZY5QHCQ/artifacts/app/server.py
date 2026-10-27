import asyncio,json,argparse,re
from aiohttp import web,WSMsgType
import core
NODE=0
def j(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return j({'error':msg},status)
async def body(req):
    try:
        raw=await req.read()
        x=json.loads(raw.decode('utf-8'))
        if not isinstance(x,dict): raise ValueError()
        return x
    except Exception:
        raise ValueError('bad json')
def token(req):
    a=req.headers.get('Authorization','')
    return a[7:] if a.startswith('Bearer ') else None
def user(req):
    u=core.user_for_token(token(req))
    if not u: raise web.HTTPUnauthorized()
    return u
async def auth(req):
    try:d=await body(req)
    except:web.HTTPException
    if req.path.endswith('/register'):
        un=d.get('username');pw=d.get('password')
        if not isinstance(un,str) or not re.fullmatch(r'[A-Za-z0-9_]+',un) or not isinstance(pw,str) or len(pw)<8:return err(400,'invalid registration')
        try:
            with core.tx() as c:
                cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,core.hash_password(pw),d.get('display_name') or un));uid=cur.lastrowid;t=core.issue_token(c,uid);r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            return j({'user':core.user_to_json(r),'token':t},201)
        except: return err(409,'duplicate username')
    un=d.get('username');pw=d.get('password')
    c=core.connect();r=c.execute('SELECT * FROM users WHERE username=?',(un,)).fetchone();c.close()
    if not r or not isinstance(pw,str) or not core.verify_password(pw,r['password']):return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,r['id'])
    return j({'user':core.user_to_json(r),'token':t})
async def me(req): return j({'user':core.user_to_json(user(req))})
async def health(req): return j({'status':'ok','node_id':NODE})
async def workspaces(req):
    u=user(req);c=core.connect()
    if req.method=='GET':
        rs=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
        return j({'workspaces':[core.workspace_to_json(x) for x in rs]})
    try:d=await body(req)
    except:return err(400,'bad json')
    slug=d.get('slug');name=d.get('name')
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not isinstance(name,str) or not name:return err(400,'invalid workspace')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id']));wid=cur.lastrowid
            ch=core.create_channel_row(c,wid,'general',False,'',u['id']);core.add_workspace_member(c,wid,u['id'],'owner')
            return j({'workspace':core.workspace_to_json(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':core.channel_to_json(ch)},201)
    except:return err(409,'duplicate slug')
async def workspace(req):
    u=user(req);slug=req.match_info['slug'];w=core.get_workspace(slug)
    if not w:return err(404,'workspace not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    if req.method=='GET':
        c=core.connect();include=req.query.get('include_archived') in ('1','true')
        q='SELECT * FROM channels WHERE workspace_id=?'+('' if include else ' AND is_archived=0')
        rs=c.execute(q,(w['id'],)).fetchall();c.close()
        chans=[x for x in rs if not x['is_private'] or core.can_read_channel(u['id'],x)]
        return j({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chans],'read_state':[core.read_state(u['id'],x['id']) for x in chans]})
    if req.method=='POST' and req.path.endswith('/channels'):
        try:d=await body(req)
        except:return err(400,'bad json')
        n=d.get('name');private=d.get('is_private',False);topic=d.get('topic','')
        if not isinstance(n,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',n) or not isinstance(private,bool) or not isinstance(topic,str) or len(topic)>250:return err(400,'invalid channel')
        if role=='guest' or (private and role not in ('owner','admin')):return err(403,'forbidden')
        try:
            with core.tx() as c: ch=core.create_channel_row(c,w['id'],n,private,topic,u['id'])
            return j({'channel':core.channel_to_json(ch)},201)
        except:return err(409,'duplicate channel')
    if req.method=='GET' and req.path.endswith('/members'):
        c=core.connect();rs=c.execute('SELECT u.*,m.role FROM users u JOIN memberships m ON m.user_id=u.id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
        return j({'members':[{**core.user_to_json(x),'user_id':x['id'],'role':x['role']} for x in rs]})
    return err(405,'method')
async def channels(req):
    u=user(req);cid=int(req.match_info['id']);ch=core.get_channel(cid)
    if not ch:return err(404,'channel not found')
    wid=ch['workspace_id'];role=core.member_role(wid,u['id'])
    if req.method=='POST' and req.path.endswith('/join'):
        if ch['is_private']:return err(403,'private channel')
        if not role:
            w=core.get_workspace_by_id(wid) if hasattr(core,'get_workspace_by_id') else None
            c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();c.close()
            if not w or w['join_mode']!='open':return err(403,'forbidden')
            with core.tx() as c:
                core.add_workspace_member(c,wid,u['id'],'member');core.add_channel_member(c,cid,u['id']);core.emit_event(c,cid,'member.joined',{'user_id':u['id']})
        else:
            with core.tx() as c:
                if core.add_channel_member(c,cid,u['id']):core.emit_event(c,cid,'member.joined',{'user_id':u['id']})
        return j({'channel':core.channel_to_json(core.get_channel(cid))})
    if req.method=='POST' and req.path.endswith('/leave'):
        if not role:return err(403,'forbidden')
        with core.tx() as c:
            n=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).rowcount
            if n:core.emit_event(c,cid,'member.left',{'user_id':u['id']})
        return j({'channel':core.channel_to_json(ch)})
    if req.method=='PATCH':
        if u['id']!=core.get_workspace_by_id(wid)['owner_id'] and not core.connect().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return err(403,'forbidden')
        try:d=await body(req)
        except:return err(400,'bad json')
        topic=d.get('topic')
        if not isinstance(topic,str) or len(topic)>250:return err(400,'invalid topic')
        if ch['is_archived']:return err(423,'archived')
        with core.tx() as c:
            c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,cid));core.emit_event(c,cid,'channel.updated',{'channel':{'id':cid,'topic':topic}})
        return j({'channel':core.channel_to_json(core.get_channel(cid))})
    if req.method=='POST' and (req.path.endswith('/archive') or req.path.endswith('/unarchive')):
        w=core.get_workspace_by_id(wid)
        if u['id']!=w['owner_id']:return err(403,'forbidden')
        val=0 if req.path.endswith('unarchive') else 1
        with core.tx() as c:
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid));core.emit_event(c,cid,'channel.unarchived' if val==0 else 'channel.archived',{'channel_id':cid})
        return j({'channel':core.channel_to_json(core.get_channel(cid))})
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if req.method=='GET' and req.path.endswith('/members'):
        c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,)).fetchall();c.close();return j({'members':[core.user_to_json(x) for x in rs]})
    return err(405,'method')
async def messages(req):
    u=user(req);cid=int(req.match_info['id']);ch=core.get_channel(cid)
    if not ch:return err(404,'channel not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if req.method=='POST':
        if ch['is_archived']:return err(423,'archived')
        try:d=await body(req)
        except:return err(400,'bad json')
        b=d.get('body');p=d.get('parent_id')
        if p is not None and (not isinstance(p,int) or isinstance(p,bool)):return err(400,'bad parent')
        if not isinstance(b,str) or not b.strip():return err(400,'empty body')
        if b.startswith('/') and not b.startswith('//'):
            if b.startswith('/shrug'):b=b[6:].lstrip()+' ¯\\_(ツ)_/¯'
            elif b.startswith('/me') and len(b)<=4:return err(400,'missing text')
            elif not b.startswith('/me'):return err(400,'unknown command')
        try:r=core.create_message(u['id'],cid,b,p);return j({'message':core.message_to_json(r)},201)
        except PermissionError:return err(403,'forbidden')
        except RuntimeError:return err(423,'archived')
        except:return err(400,'invalid message')
    if 'offset' in req.query or 'page' in req.query:return err(400,'offset pagination unsupported')
    try:lim,cur=core.parse_paging(req.query,'msg')
    except:return err(400,'bad paging')
    c=core.connect();q='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0';args=[cid]
    if cur is not None:q+=' AND id<?';args.append(cur)
    q+=' ORDER BY id DESC LIMIT ?';args.append(lim+1);rs=c.execute(q,args).fetchall();c.close();more=len(rs)>lim;rs=rs[:lim]
    return j({'messages':[core.message_to_json(x) for x in rs],'next_cursor':core.encode_cursor('msg',rs[-1]['id']) if more and rs else None})
async def replies(req):
    u=user(req);mid=int(req.match_info['id']);c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not p:return err(404,'message not found')
    ch=core.get_channel(p['channel_id'])
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if 'offset' in req.query or 'page' in req.query:return err(400,'offset pagination unsupported')
    try:lim,cur=core.parse_paging(req.query,'rep')
    except:return err(400,'bad paging')
    c=core.connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[mid]
    if cur is not None:q+=' AND id>?';args.append(cur)
    q+=' ORDER BY id ASC LIMIT ?';args.append(lim+1);rs=c.execute(q,args).fetchall();c.close()
    more=len(rs)>lim;rs=rs[:lim]
    return j({'replies':[core.message_to_json(x) for x in rs],'next_cursor':core.encode_cursor('rep',rs[-1]['id']) if more and rs else None})
async def delete_message(req):
    u=user(req);mid=int(req.match_info['id']);m=core.get_message(mid)
    if not m:return err(404,'message not found')
    ch=core.get_channel(m['channel_id']);w=core.get_workspace_by_id(ch['workspace_id'])
    if m['author_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:
        old=c.execute('SELECT deleted FROM messages WHERE id=?',(mid,)).fetchone()
        if not old['deleted']:
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return j({'deleted':True})
async def edit(req):
    if req.method=='DELETE': return await delete_message(req)
    u=user(req);mid=int(req.match_info['id']);c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not r:return err(404,'message not found')
    if r['deleted']:return err(409,'deleted message')
    if r['author_id']!=u['id']:return err(403,'forbidden')
    try:d=await body(req)
    except:return err(400,'bad json')
    if not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid body')
    with core.tx() as c:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
        mentions=core.resolve_mentions(c,d['body'],ch,u['id'])
        edited=core.now_iso();c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(d['body'],edited,json.dumps(mentions),mid));core.emit_event(c,r['channel_id'],'message.updated',{'message_id':mid,'body':d['body'],'edited_at':edited})
    return j({'message':core.message_to_json(core.get_message(mid))})
async def reactions(req):
    u=user(req);mid=int(req.match_info['id']);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return err(404,'message not found')
    if not core.can_read_channel(u['id'],core.get_channel(m['channel_id'])):return err(403,'forbidden')
    if req.method=='POST':
        try:d=await body(req);e=d.get('emoji')
        except:return err(400,'bad json')
        if not isinstance(e,str) or not e:return err(400,'invalid emoji')
        with core.tx() as c:
            n=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],e)).rowcount
            if n:core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':mid,'emoji':e,'user_id':u['id']})
        return j({'reactions':core.reactions_for(mid)})
    e=req.match_info.get('emoji','')
    with core.tx() as c:
        n=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],e)).rowcount
        if n:core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':mid,'emoji':e,'user_id':u['id']})
    return j({'reactions':core.reactions_for(mid)})
async def pins(req):
    u=user(req)
    if req.method=='POST':
        mid=int(req.match_info['mid']);m=core.get_message(mid)
        if not m:return err(404,'message not found')
        cid=m['channel_id']
    else: cid=int(req.match_info['id'])
    ch=core.get_channel(cid)
    if not ch:return err(404,'channel not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if req.method=='GET':
        if 'offset' in req.query or 'page' in req.query:return err(400,'offset pagination unsupported')
        try:lim,cur=core.parse_paging(req.query,'pin')
        except:return err(400,'bad paging')
        c=core.connect();q='SELECT p.message_id,p.pinned_by,p.pinned_at,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';args=[cid]
        if cur is not None:q+=' AND p.message_id<?';args.append(cur)
        q+=' ORDER BY p.message_id DESC LIMIT ?';args.append(lim+1)
        rs=c.execute(q,args).fetchall();c.close();more=len(rs)>lim;rs=rs[:lim]
        return j({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':core.encode_cursor('pin',rs[-1]['message_id']) if more and rs else None})
    with core.tx() as c:
        old=c.execute('SELECT message_id,pinned_by,pinned_at FROM pins WHERE message_id=?',(mid,)).fetchone()
        if not old:
            at=core.now_iso();c.execute('INSERT INTO pins VALUES(?,?,?)',(mid,u['id'],at));old=c.execute('SELECT message_id,pinned_by,pinned_at FROM pins WHERE message_id=?',(mid,)).fetchone()
    return j({'pin':{'message_id':old['message_id'],'pinned_by':old['pinned_by'],'pinned_at':old['pinned_at']}})

async def pin_delete(req):
    u=user(req);mid=int(req.match_info['mid']);m=core.get_message(mid)
    if not m:return err(404,'message not found')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return j({'unpinned':True})
async def search(req):
    u=user(req);qtext=req.query.get('q')
    if not isinstance(qtext,str) or not qtext:return err(400,'bad query')
    if 'offset' in req.query or 'page' in req.query:return err(400,'offset pagination unsupported')
    try:lim,cur=core.parse_paging(req.query,'srch')
    except:return err(400,'bad paging')
    wid=req.query.get('workspace_id')
    if wid is not None:
        try:wid=int(wid)
        except:return err(400,'bad workspace')
    c=core.connect()
    sql='SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN memberships mem ON mem.workspace_id=ch.workspace_id AND mem.user_id=? WHERE m.deleted=0 AND LOWER(m.body) LIKE LOWER(?) AND (ch.is_private=0 OR EXISTS(SELECT 1 FROM channel_members cm WHERE cm.channel_id=ch.id AND cm.user_id=?))'
    args=[u['id'],'%'+qtext+'%',u['id']]
    if wid is not None:sql+=' AND ch.workspace_id=?';args.append(wid)
    if cur is not None:sql+=' AND m.id<?';args.append(cur)
    sql+=' ORDER BY m.id DESC LIMIT ?';args.append(lim+1)
    rs=c.execute(sql,args).fetchall();c.close();more=len(rs)>lim;rs=rs[:lim]
    return j({'results':[core.message_to_json(x) for x in rs],'next_cursor':core.encode_cursor('srch',rs[-1]['id']) if more and rs else None})

async def read(req):
    u=user(req);cid=int(req.match_info['id']);ch=core.get_channel(cid)
    if not ch:return err(404,'channel not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if req.method=='POST':
        try:d=await body(req);v=d.get('last_read_event_id')
        except:return err(400,'bad json')
        if not isinstance(v,int) or isinstance(v,bool) or v<0:return err(400,'invalid event id')
        old=core.read_state(u['id'],cid)['last_read_event_id']
        with core.tx() as c:c.execute('INSERT INTO read_state VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=? WHERE last_read_event_id<?',(u['id'],cid,max(old,v),max(old,v),max(old,v)))
    return j({'read_state':core.read_state(u['id'],cid)})
async def ws_handler(req):
    t=req.query.get('token');u=core.user_for_token(t)
    if not u:return err(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(req);subs={};alive=True
    async def poll():
        while alive:
            for cid,last in list(subs.items()):
                for e in core.events_after(cid,last):
                    await ws.send_json(e);subs[cid]=e['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for msg in ws:
            if msg.type!=WSMsgType.TEXT:continue
            try:d=json.loads(msg.data);cid=int(d['channel_id'])
            except:continue
            if d.get('type')=='subscribe':
                subs[cid]=core.head_event_id(cid);await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
            elif d.get('type')=='resume':
                since=int(d.get('since_event_id',0))
                for e in core.events_after(cid,since):await ws.send_json(e)
                subs[cid]=core.head_event_id(cid);await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
    finally:
        alive=False;task.cancel()
    return ws
def application():
    a=web.Application()
    a.router.add_get('/api/health',health)
    a.router.add_post('/api/auth/register',auth);a.router.add_post('/api/auth/login',auth);a.router.add_get('/api/auth/me',me)
    a.router.add_route('*','/api/workspaces',workspaces);a.router.add_route('*','/api/workspaces/{slug}',workspace);a.router.add_route('*','/api/workspaces/{slug}/channels',workspace);a.router.add_route('*','/api/workspaces/{slug}/members',workspace)
    a.router.add_route('*','/api/channels/{id}/join',channels);a.router.add_route('*','/api/channels/{id}/leave',channels);a.router.add_route('*','/api/channels/{id}/members',channels);a.router.add_route('*','/api/channels/{id}/archive',channels);a.router.add_route('*','/api/channels/{id}/unarchive',channels);a.router.add_route('*','/api/channels/{id}',channels)
    a.router.add_route('*','/api/channels/{id}/messages',messages);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_route('*','/api/messages/{id}',edit)
    a.router.add_post('/api/messages/{id}/reactions',reactions);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reactions)
    a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_post('/api/messages/{mid}/pin',pins);a.router.add_delete('/api/messages/{mid}/pin',pin_delete)
    a.router.add_get('/api/search',search);a.router.add_route('*','/api/channels/{id}/read',read);a.router.add_get('/api/ws',ws_handler)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
    web.run_app(application(),host='127.0.0.1',port=8000+NODE)
