import sqlite3,argparse,asyncio,json,re
from aiohttp import web,WSMsgType
import core
NODE=0
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
def require_user(req):
    h=req.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
@web.middleware
async def errors(req,handler):
    try:return await handler(req)
    except web.HTTPException as e:
        if e.status==401:return err(401,'unauthorized')
        raise
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
    except RuntimeError:return err(423,'archived')
    except ValueError as e:return err(400,str(e))
    except sqlite3.IntegrityError:return err(409,'conflict')
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
    b=await req.json()
    un=b.get('username',''); pw=b.get('password','')
    if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return err(400,'invalid')
    with core.tx() as c:
        try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,core.hash_password(pw),b.get('display_name') or un))
        except Exception:return err(409,'duplicate')
        u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    b=await req.json(); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone(); c.close()
    if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t})
async def me(req): return out({'user':core.user_to_json(require_user(req))})
async def create_ws(req):
    u=require_user(req); b=await req.json(); slug=b.get('slug','')
    if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not b.get('name'):return err(400,'invalid')
    with core.tx() as c:
        try:
            c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,b['name'],u['id']))
            w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
            core.add_workspace_member(c,w['id'],u['id'],'owner')
            g=core.create_channel_row(c,w['id'],'general',0,'',u['id'])
        except Exception:return err(409,'duplicate')
    return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},201)
async def workspaces(req):
    u=require_user(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def ws_detail(req):
    u=require_user(req); w,ch,rs=core.workspace_detail(u,req.match_info['slug'],req.query.get('include_archived') in ('1','true'))
    return out({'workspace':core.workspace_to_json(w),'channels':ch,'read_state':rs})
async def members(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w: raise KeyError()
    if not core.member_role(w['id'],u['id']):raise PermissionError()
    c=core.connect(); rows=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out({'members':[dict(x) for x in rows]})
async def create_channel(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:raise KeyError()
    role=core.member_role(w['id'],u['id'])
    if not role or role=='guest':raise PermissionError()
    b=await req.json(); name=b.get('name',''); topic=b.get('topic','')
    if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return err(400,'invalid name')
    core.validate_topic(topic)
    if b.get('is_private') and role not in ('owner','admin'):raise PermissionError()
    with core.tx() as c:
        try:r=core.create_channel_row(c,w['id'],name,bool(b.get('is_private')),topic,u['id'])
        except Exception:return err(409,'duplicate')
    return out({'channel':core.channel_to_json(r)},201)
async def leave(req):
    u=require_user(req); cid=int(req.match_info['id']); ch=core.get_channel(cid)
    if not ch: raise KeyError()
    with core.tx() as c:
        x=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id']))
        if x.rowcount: core.emit_event(c,cid,'member.left',{'user_id':u['id']})
    return out({'channel':core.channel_to_json(ch)})
async def join(req):
    u=require_user(req); r=core.join_channel(u,int(req.match_info['id'])); return out({'channel':core.channel_to_json(r)})
async def channel(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:raise KeyError()
    if req.method=='PATCH':
        core.require_writable(ch)
        c0=core.connect(); cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
        w0=core.get_workspace_by_id(ch['workspace_id']) if hasattr(core,'get_workspace_by_id') else None
        if not cm and (not w0 or w0['owner_id']!=u['id']): raise PermissionError()
        b=await req.json(); core.validate_topic(b.get('topic',''))
        with core.tx() as c:
            if b['topic'] != ch['topic']:
                c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id'])); core.emit_event(c,ch['id'],'channel.updated',{})
        return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
    if not core.can_read_channel(u['id'],ch):raise PermissionError()
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return out({'members':[core.user_to_json(x) for x in rows]})
async def archive(req):
    u=require_user(req); r=core.set_archived(u,int(req.match_info['id']),not req.path.endswith('/unarchive')); return out({'channel':core.channel_to_json(r)})
async def read_state(req):
    u=require_user(req); cid=int(req.match_info['id']); ch=core.get_channel(cid)
    if not ch: raise KeyError()
    if not core.can_read_channel(u['id'],ch): raise PermissionError()
    if req.method=='POST':
        b=await req.json()
        try:v=int(b.get('last_read_event_id'))
        except: raise ValueError('invalid read state')
        with core.tx() as c:
            old=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
            cur=old['last_read_event_id'] if old else 0
            if v>cur:
                c.execute('INSERT INTO reads(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],cid,v))
    return out({'read_state':core.read_state(u['id'],cid)})
async def messages(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:raise KeyError()
    if not core.can_read_channel(u['id'],ch):raise PermissionError()
    if req.method=='POST':
        b=await req.json(); body=b.get('body','')
        if not isinstance(body,str) or not body.strip(): raise ValueError('empty')
        m=core.create_message(u['id'],ch['id'],body,b.get('parent_id')); return out({'message':m},201)
    n,cur=core.parse_paging(req.query,'msg'); ms,nx=core.list_messages(ch['id'],n,cur); return out({'messages':ms,'next_cursor':nx})
async def replies(req):
    u=require_user(req); c=core.connect(); p=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not p:raise KeyError()
    ch=core.get_channel(p['channel_id'])
    if not core.can_read_channel(u['id'],ch):raise PermissionError()
    n,cur=core.parse_paging(req.query,'rep'); ms,nx=core.list_messages(p['id'],n,cur,True); return out({'replies':ms,'next_cursor':nx})
async def edit(req):
    u=require_user(req); mid=int(req.match_info['id']); b=await req.json()
    cpre=core.connect(); mpre=cpre.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); cpre.close()
    if not mpre: raise KeyError()
    core.require_writable(core.get_channel(mpre['channel_id']))
    with core.tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:raise KeyError()
        if m['author_id']!=u['id']:raise PermissionError()
        if m['deleted']:return err(409,'deleted')
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b.get('body',''),core.now_iso(),mid)); core.emit_event(c,m['channel_id'],'message.updated',{'message_id':mid})
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close(); return out({'message':core.message_to_json(m)})
async def delete_message(req):
    u=require_user(req); mid=int(req.match_info['id'])
    with core.tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m: raise KeyError()
        ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
        if m['author_id']!=u['id'] and w['owner_id']!=u['id']: raise PermissionError()
        if not m['deleted']:
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return out({'deleted':True})
async def delete_reaction(req):
    u=require_user(req); mid=int(req.match_info['id']); emoji=req.match_info['emoji']
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m: raise KeyError()
    if not core.can_read_channel(u['id'],core.get_channel(m['channel_id'])): raise PermissionError()
    core.require_writable(core.get_channel(m['channel_id']))
    with core.tx() as c:
        x=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
        if x.rowcount: core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji})
    c=core.connect(); rr=core.message_to_json(m)['reactions']; c.close()
    return out({'reactions':rr})
async def react(req):
    u=require_user(req); mid=int(req.match_info['id']); b=await req.json(); emoji=b.get('emoji','')
    cpre=core.connect(); mpre=cpre.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); cpre.close()
    if not mpre: raise KeyError()
    core.require_writable(core.get_channel(mpre['channel_id']))
    with core.tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:raise KeyError()
        x=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
        if x.rowcount: core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':mid,'emoji':emoji})
    return out({'reactions':core.message_to_json(m)['reactions']})
async def pins(req):
    u=require_user(req); cid=int(req.match_info['id']); ch=core.get_channel(cid)
    if not ch: raise KeyError()
    if not core.can_read_channel(u['id'],ch): raise PermissionError()
    n,cur=core.parse_paging(req.query,'pin')
    c=core.connect()
    if cur is None:
        rows=c.execute('SELECT p.message_id,p.pinned_by,p.pinned_at,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.message_id DESC LIMIT ?',(cid,n+1)).fetchall()
    else:
        rows=c.execute('SELECT p.message_id,p.pinned_by,p.pinned_at,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND p.message_id<? ORDER BY p.message_id DESC LIMIT ?',(cid,cur,n+1)).fetchall()
    c.close(); more=len(rows)>n; rows=rows[:n]
    return out({'pins':[{'message':core.message_to_json(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rows],'next_cursor':core.encode_cursor('pin',rows[-1]['message_id']) if more else None})
async def pin(req):
    u=require_user(req); mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m: raise KeyError()
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch): raise PermissionError()
    core.require_writable(ch)
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
        r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return out({'pin':dict(r)})
async def unpin(req):
    u=require_user(req); mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m: raise KeyError()
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch): raise PermissionError()
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return out({'unpinned':True})
async def search(req):
    u=require_user(req); q=req.query.get('q')
    if not q: return err(400,'missing q')
    n,cur=core.parse_paging(req.query,'srch')
    c=core.connect()
    rows=c.execute('SELECT m.* FROM messages m WHERE m.deleted=0 AND lower(m.body) LIKE lower(?) ORDER BY m.id DESC',('%'+q+'%',)).fetchall()
    c.close()
    filtered=[r for r in rows if core.can_read_channel(u['id'],core.get_channel(r['channel_id']))]
    if req.query.get('workspace_id'):
        c=core.connect(); filtered=[r for r in filtered if c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()['workspace_id']==int(req.query['workspace_id'])]; c.close()
    if cur is not None: filtered=[r for r in filtered if r['id']<cur]
    page=filtered[:n]; more=len(filtered)>n
    return out({'results':[core.message_to_json(x) for x in page],'next_cursor':core.encode_cursor('srch',page[-1]['id']) if more and page else None})
async def ws(req):
    token=req.query.get('token','')
    if token.startswith('Bearer '): token=token[7:]
    if not token or not core.user_for_token(token):
        raise web.HTTPUnauthorized()
    w=web.WebSocketResponse(); await w.prepare(req); active={}
    async def poll():
        while not w.closed:
            for cid,last in list(active.items()):
                for e in core.events_after(cid,last): await w.send_json(e); active[cid]=e['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for x in w:
            if x.type!=WSMsgType.TEXT:continue
            b=json.loads(x.data); cid=int(b['channel_id'])
            if b['type']=='subscribe':
                active[cid]=core.head_event_id(cid); await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':active[cid]})
            elif b['type']=='resume':
                active[cid]=int(b.get('since_event_id',0))
                for e in core.events_after(cid,active[cid]): await w.send_json(e); active[cid]=e['event_id']
                await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':active[cid]})
    finally:task.cancel()
    return w
async def main():
    global NODE
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    app=web.Application(middlewares=[errors])
    app.router.add_get('/api/health',health); app.router.add_post('/api/auth/register',register); app.router.add_post('/api/auth/login',login); app.router.add_get('/api/auth/me',me)
    app.router.add_post('/api/workspaces',create_ws); app.router.add_get('/api/workspaces',workspaces); app.router.add_get('/api/workspaces/{slug}',ws_detail); app.router.add_get('/api/workspaces/{slug}/members',members); app.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    app.router.add_post('/api/channels/{id}/join',join); app.router.add_get('/api/channels/{id}/pins',pins); app.router.add_post('/api/channels/{id}/leave',leave); app.router.add_patch('/api/channels/{id}',channel); app.router.add_get('/api/channels/{id}/members',channel); app.router.add_post('/api/channels/{id}/archive',archive); app.router.add_post('/api/channels/{id}/unarchive',archive)
    app.router.add_route('*','/api/channels/{id}/read',read_state); app.router.add_route('*','/api/channels/{id}/messages',messages); app.router.add_get('/api/messages/{id}/replies',replies); app.router.add_patch('/api/messages/{id}',edit); app.router.add_delete('/api/messages/{id}',delete_message); app.router.add_post('/api/messages/{id}/pin',pin); app.router.add_delete('/api/messages/{id}/pin',unpin); app.router.add_post('/api/messages/{id}/reactions',react); app.router.add_delete('/api/messages/{id}/reactions/{emoji}',delete_reaction); app.router.add_get('/api/search',search); app.router.add_get('/api/ws',ws)
    await web._run_app(app,host='127.0.0.1',port=8000+NODE)
if __name__=='__main__': asyncio.run(main())
