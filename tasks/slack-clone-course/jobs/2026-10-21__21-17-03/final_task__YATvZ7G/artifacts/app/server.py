import argparse, asyncio, json, re
from aiohttp import web, WSMsgType
import core

def json_error(status,msg): return web.json_response({'error':msg},status=status)
def token_of(req):
    h=req.headers.get('Authorization','')
    return h[7:] if h.startswith('Bearer ') else req.query.get('token')
def require_user(req):
    u=core.user_for_token(token_of(req))
    if not u: raise web.HTTPUnauthorized()
    return u
def body(req):
    return req.json()
def access(uid,ch):
    return core.can_read_channel(uid,ch)

async def health(req): return web.json_response({'status':'ok','node_id':req.app['node']})
async def register(req):
    try:
        b=await body(req); name=b.get('username',''); pw=b.get('password','')
        if not re.fullmatch(r'[A-Za-z0-9_]+',name) or len(pw)<8: return json_error(400,'invalid')
        with core.tx() as c:
            if c.execute('SELECT 1 FROM users WHERE username=?',(name,)).fetchone(): return json_error(409,'duplicate')
            c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(name,core.hash_password(pw),b.get('display_name') or name))
            u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
        return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
    except Exception as e: return json_error(400,str(e))
async def login(req):
    try:
        b=await body(req); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username'),)).fetchone(); c.close()
        if not u or not core.verify_password(b.get('password',''),u['password']): return json_error(401,'invalid credentials')
        with core.tx() as c: t=core.issue_token(c,u['id'])
        return web.json_response({'user':core.user_to_json(u),'token':t})
    except: return json_error(401,'invalid credentials')
async def me(req):
    try: return web.json_response({'user':core.user_to_json(require_user(req))})
    except web.HTTPUnauthorized: return json_error(401,'unauthorized')

async def workspaces(req):
    try: u=require_user(req)
    except web.HTTPUnauthorized: return json_error(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    try: u=require_user(req); b=await body(req); slug=b.get('slug',''); name=b.get('name','')
    except web.HTTPUnauthorized: return json_error(401,'unauthorized')
    if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not name: return json_error(400,'invalid')
    try:
        with core.tx() as c:
            if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(slug,)).fetchone(): return json_error(409,'duplicate')
            c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id']))
            w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
            ch=core.create_channel_row(c,w['id'],'general',0,'',u['id']); core.add_workspace_member(c,w['id'],u['id'],'owner')
        return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
    except Exception as e: return json_error(400,str(e))
async def workspace_detail(req):
    try: u=require_user(req)
    except web.HTTPUnauthorized: return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],req.query.get('include_archived') in ('1','true'))).fetchall(); c.close()
    rows=[x for x in rows if not x['is_private'] or core.can_read_channel(u['id'],x)]
    rs=[read_state(u['id'],x['id']) for x in rows]
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':rs})
async def members(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT user_id,role FROM workspace_members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return web.json_response({'members':[dict(x) for x in rows]})
async def create_channel(req):
    try:u=require_user(req); slug=req.match_info['slug']; b=await body(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    w=core.get_workspace(slug)
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    name=b.get('name',''); private=bool(b.get('is_private',False)); topic=b.get('topic','')
    if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or not isinstance(topic,str) or len(topic)>250:return json_error(400,'invalid')
    if role=='guest' or (private and role not in ('owner','admin')):return json_error(403,'forbidden')
    try:
        with core.tx() as c:
            ch=core.create_channel_row(c,w['id'],name,private,topic,u['id'])
        return web.json_response({'channel':core.channel_to_json(ch)},status=201)
    except: return json_error(409,'duplicate')
async def channel_join(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    role=core.member_role(ch['workspace_id'],u['id'])
    w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if ch['is_private'] or role=='guest' or (not role and w['join_mode']!='open'):return json_error(403,'forbidden')
    with core.tx() as c:
        if not role: core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
        added=core.add_channel_member(c,ch['id'],u['id'])
        if added: core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_members(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not access(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return web.json_response({'members':[core.user_to_json(x) for x in rows]})
async def channel_patch(req):
    try:u=require_user(req); b=await body(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    c=core.connect(); is_member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
    if not is_member and core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    topic=b.get('topic')
    if not isinstance(topic,str) or len(topic)>250:return json_error(400,'invalid')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone(); core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
    return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_messages(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not access(u['id'],ch):return json_error(403,'forbidden')
    try: lim,cur=core.parse_paging(req.query,'msg')
    except:return json_error(400,'paging')
    c=core.connect(); q='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'; args=[ch['id']]
    if cur is not None:q+=' AND id<?';args.append(cur)
    rows=c.execute(q+' ORDER BY id DESC LIMIT ?',args+[lim+1]).fetchall(); c.close()
    more=len(rows)>lim; rows=rows[:lim]
    return web.json_response({'messages':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('msg',rows[-1]['id']) if more else None})
async def post_message(req):
    try:u=require_user(req); b=await body(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not access(u['id'],ch):return json_error(403,'forbidden')
    text=b.get('body')
    if not isinstance(text,str) or not text.strip():return json_error(400,'empty')
    try:return web.json_response({'message':core.create_message(u['id'],ch['id'],text,b.get('parent_id'))},status=201)
    except LookupError:return json_error(404,'not found')
    except ValueError as e:return json_error(423 if str(e)=='archived' else 400,str(e))
async def replies(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect(); p=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
    if not p:return json_error(404,'not found')
    ch=core.get_channel(p['channel_id'])
    if not access(u['id'],ch):return json_error(403,'forbidden')
    try:lim,cur=core.parse_paging(req.query,'rep')
    except:return json_error(400,'paging')
    c=core.connect(); q='SELECT * FROM messages WHERE parent_id=? AND deleted=0'; a=[p['id']]
    if cur is not None:q+=' AND id>?';a.append(cur)
    rows=c.execute(q+' ORDER BY id ASC LIMIT ?',a+[lim+1]).fetchall();c.close();more=len(rows)>lim;rows=rows[:lim]
    return web.json_response({'replies':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('rep',rows[-1]['id']) if more else None})
async def edit_message(req):
    try:u=require_user(req); b=await body(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    ch=core.get_channel(r['channel_id'])
    if r['deleted']:return json_error(409,'deleted')
    if r['author_id']!=u['id']:return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    if not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty')
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],core.now_iso(),r['id']))
        r=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone()
        core.emit_event(c,ch['id'],'message.updated',{'message':core.message_to_json(r)})
    return web.json_response({'message':core.message_to_json(r)})
async def delete_message(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    ch=core.get_channel(r['channel_id']); role=core.member_role(ch['workspace_id'],u['id'])
    if r['author_id']!=u['id'] and role!='owner':return json_error(403,'forbidden')
    if not r['deleted']:
        with core.tx() as c:
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],))
            core.emit_event(c,ch['id'],'message.deleted',{'message_id':r['id']})
    return web.json_response({'deleted':True})
async def react(req):
    try:u=require_user(req); b=await body(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    ch=core.get_channel(r['channel_id'])
    if not access(u['id'],ch):return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    emoji=b.get('emoji')
    if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid')
    with core.tx() as c:
        x=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?,?)',(r['id'],u['id'],emoji,core.now_iso()))
        if x.rowcount: core.emit_event(c,ch['id'],'reaction.added',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']})
    return web.json_response({'reactions':core.message_to_json(r)['reactions']})
async def unreact(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    ch=core.get_channel(r['channel_id'])
    if not access(u['id'],ch):return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    emoji=req.match_info['emoji']
    with core.tx() as c:
        x=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji))
        if x.rowcount: core.emit_event(c,ch['id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']})
    return web.json_response({'reactions':core.message_to_json(r)['reactions']})
async def pin(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    ch=core.get_channel(r['channel_id'])
    if not access(u['id'],ch):return json_error(403,'forbidden')
    with core.tx() as c:
        x=c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(r['id'],u['id'],core.now_iso()))
        if x.rowcount: core.emit_event(c,ch['id'],'message.pinned',{'message_id':r['id'],'pinned_by':u['id']})
        p=c.execute('SELECT * FROM pins WHERE message_id=?',(r['id'],)).fetchone()
    return web.json_response({'pin':{'message_id':p['message_id'],'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']}})
async def unpin(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    ch=core.get_channel(r['channel_id'])
    if not access(u['id'],ch):return json_error(403,'forbidden')
    with core.tx() as c:
        x=c.execute('DELETE FROM pins WHERE message_id=?',(r['id'],))
        if x.rowcount: core.emit_event(c,ch['id'],'message.unpinned',{'message_id':r['id']})
    return web.json_response({'unpinned':True})
async def pins(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not access(u['id'],ch):return json_error(403,'forbidden')
    try:lim,cur=core.parse_paging(req.query,'pin')
    except:return json_error(400,'paging')
    c=core.connect();q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.deleted=0 AND m.channel_id=?';a=[ch['id']]
    if cur is not None:q+=' AND p.message_id<?';a.append(cur)
    rows=c.execute(q+' ORDER BY p.message_id DESC LIMIT ?',a+[lim+1]).fetchall();c.close();more=len(rows)>lim;rows=rows[:lim]
    return web.json_response({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':core.encode_cursor('pin',rows[-1]['message_id']) if more else None})
async def search(req):
    try:
        u=require_user(req)
    except web.HTTPUnauthorized:
        return json_error(401,'unauthorized')
    qtext=req.query.get('q')
    if not isinstance(qtext,str) or not qtext.strip():
        return json_error(400,'q required')
    try:
        lim,cur=core.parse_paging(req.query,'srch')
    except:
        return json_error(400,'paging')
    c=core.connect()
    sql="""SELECT DISTINCT m.* FROM messages m
        JOIN channels ch ON ch.id=m.channel_id
        JOIN workspace_members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=?
        LEFT JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=?
        WHERE m.deleted=0 AND wm.role!='guest'
        AND (ch.is_private=0 OR cm.user_id IS NOT NULL)
        AND lower(m.body) LIKE lower(?)"""
    args=[u['id'],u['id'],'%'+qtext+'%']
    wid=req.query.get('workspace_id')
    if wid is not None:
        try: wid=int(wid)
        except: c.close(); return json_error(400,'workspace_id')
        sql+=' AND ch.workspace_id=?'; args.append(wid)
    if cur is not None:
        sql+=' AND m.id<?'; args.append(cur)
    sql+=' ORDER BY m.id DESC LIMIT ?'; args.append(lim+1)
    rows=c.execute(sql,args).fetchall(); c.close()
    more=len(rows)>lim
    rows=rows[:lim]
    return web.json_response({'results':[core.message_to_json(x) for x in rows],
                              'next_cursor':core.encode_cursor('srch',rows[-1]['id']) if more else None})
def read_state(uid,cid):
    c=core.connect()
    x=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone()
    last=x[0] if x else 0
    rows=c.execute("""SELECT m.*,e.event_id FROM messages m
        JOIN events e ON e.channel_id=m.channel_id
        AND json_extract(e.payload,'$.message.id')=m.id
        WHERE m.channel_id=? AND m.deleted=0 AND m.author_id!=?
        AND e.event_id>? ORDER BY e.event_id""",(cid,uid,last)).fetchall()
    unread=len(rows)
    mention=0
    for r in rows:
        try:
            if uid in json.loads(r['mentions'] or '[]'):
                mention+=1
        except:
            pass
    c.close()
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}

async def get_read(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not access(u['id'],ch):return json_error(403,'forbidden')
    return web.json_response({'read_state':read_state(u['id'],ch['id'])})
async def post_read(req):
    try:u=require_user(req);b=await body(req);v=b.get('last_read_event_id')
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    if not isinstance(v,int) or v<0:return json_error(400,'invalid')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not access(u['id'],ch):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('INSERT INTO read_state VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=MAX(last_read_event_id,excluded.last_read_event_id)',(u['id'],ch['id'],v))
    return web.json_response({'read_state':read_state(u['id'],ch['id'])})
async def leave(req):
    try:u=require_user(req)
    except web.HTTPUnauthorized:return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    with core.tx() as c:
        x=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if x.rowcount:core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return web.json_response({'channel':core.channel_to_json(ch)})
async def archive(req):
    try:
        u=require_user(req)
    except web.HTTPUnauthorized:
        return json_error(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:
        return json_error(404,'not found')
    if core.member_role(ch['workspace_id'],u['id'])!='owner':
        return json_error(403,'forbidden')
    target=1 if req.path.endswith('/archive') else 0
    old=int(ch['is_archived'])
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(target,ch['id']))
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        if old != target:
            kind='channel.archived' if target else 'channel.unarchived'
            core.emit_event(c,ch['id'],kind,{'channel':core.channel_to_json(ch)})
    return web.json_response({'channel':core.channel_to_json(ch)})

async def ws_handler(req):
    if not core.user_for_token(req.query.get('token')): return web.Response(status=401)
    ws=web.WebSocketResponse(); await ws.prepare(req); subs={}
    async def poll():
        while not ws.closed:
            for cid,last in list(subs.items()):
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall(); c.close()
                for x in rows:
                    await ws.send_str(x['payload']); subs[cid]=x['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for msg in ws:
            if msg.type!=WSMsgType.TEXT: continue
            try:
                x=json.loads(msg.data); cid=int(x.get('channel_id'))
                c=core.connect(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close()
                if not head: continue
                if x.get('type')=='subscribe':
                    subs[cid]=head[0]; await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head[0]})
                elif x.get('type')=='resume':
                    since=int(x.get('since_event_id',0)); c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close()
                    for r in rows: await ws.send_str(r['payload'])
                    subs[cid]=head[0]; await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':head[0]})
            except: pass
    finally: task.cancel()
    return ws

def app_factory(node):
    a=web.Application();a['node']=node
    a.router.add_get('/api/health',health)
    a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace)
    a.router.add_get('/api/workspaces/{slug}',workspace_detail);a.router.add_get('/api/workspaces/{slug}/members',members)
    a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',channel_join);a.router.add_post('/api/channels/{id}/leave',leave)
    a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_patch('/api/channels/{id}',channel_patch)
    a.router.add_get('/api/channels/{id}/messages',channel_messages);a.router.add_post('/api/channels/{id}/messages',post_message)
    a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_patch('/api/messages/{id}',edit_message);a.router.add_delete('/api/messages/{id}',delete_message)
    a.router.add_post('/api/messages/{id}/reactions',react);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreact)
    a.router.add_post('/api/messages/{id}/pin',pin);a.router.add_delete('/api/messages/{id}/pin',unpin)
    a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_get('/api/search',search)
    a.router.add_get('/api/channels/{id}/read',get_read);a.router.add_post('/api/channels/{id}/read',post_read)
    a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive)
    a.router.add_get('/api/ws',ws_handler)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);args=p.parse_args()
    core.init_db();web.run_app(app_factory(args.node),host='127.0.0.1',port=8000+args.node)
