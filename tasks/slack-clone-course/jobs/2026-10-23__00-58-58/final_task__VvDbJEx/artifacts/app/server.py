import asyncio, argparse, json, re, base64
from aiohttp import web, WSMsgType
class ArchivedError(web.HTTPException):
    status_code=423
    reason='Archived'
import core
NODE=0
def err(status,msg): return web.json_response({'error':msg},status=status)
async def body(req):
    try:
        x=await req.json()
        if not isinstance(x,dict): raise ValueError()
        return x
    except: raise web.HTTPBadRequest(text=json.dumps({'error':'invalid JSON'}),content_type='application/json')
def token(req):
    a=req.headers.get('Authorization','')
    return a[7:] if a.startswith('Bearer ') else None
def user(req):
    u=core.user_for_token(token(req))
    if not u: raise web.HTTPUnauthorized(content_type='application/json',text=json.dumps({'error':'unauthorized'}))
    return u
def ok(x,status=200): return web.json_response(x,status=status)
def getc(cid):
    c=core.get_channel(cid)
    if not c: raise web.HTTPNotFound(content_type='application/json',text=json.dumps({'error':'not found'}))
    return c
def access(u,ch,write=False):
    r=core.member_role(ch['workspace_id'],u['id'])
    c=core.connect(); m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
    if not r or (ch['is_private'] and not m): raise web.HTTPForbidden(content_type='application/json',text=json.dumps({'error':'forbidden'}))
    if write and r=='guest': raise web.HTTPForbidden(content_type='application/json',text=json.dumps({'error':'forbidden'}))
    if write and ch['is_archived']: raise ArchivedError(text=json.dumps({'error':'archived'}),content_type='application/json')
    return r
def msgjson(r):
    c=core.connect(); a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rs=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    files=[]; c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':core.user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':_replies(r['id']),'created_at':r['created_at'],'edited_at':r['edited_at'],'files':files,'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rs],'mentions':json.loads(r['mentions'] or '[]')}
def _replies(mid):
    c=core.connect(); n=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(mid,)).fetchone()['n']; c.close(); return n
def mentions(c,body,ch,uid):
    out=[]
    if '@channel' in body:
        for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(ch['id'],uid)):
            out.append(x['user_id'])
    for name in re.findall(r'(?<![\w@.])@(\w+)',body):
        x=c.execute('''SELECT u.id FROM users u JOIN workspace_members m ON m.user_id=u.id
          WHERE m.workspace_id=? AND u.username=?''',(ch['workspace_id'],name)).fetchone()
        if x and x['id']!=uid and x['id'] not in out: out.append(x['id'])
    return out
def paging(q,kind):
    if 'page' in q or 'offset' in q: raise web.HTTPBadRequest(text=json.dumps({'error':'offset unsupported'}),content_type='application/json')
    try: n=int(q.get('limit','50'))
    except: raise web.HTTPBadRequest(text=json.dumps({'error':'bad limit'}),content_type='application/json')
    if n<1 or n>200: raise web.HTTPBadRequest(text=json.dumps({'error':'bad limit'}),content_type='application/json')
    cur=None
    if q.get('cursor'):
        try: cur=core.decode_cursor(kind,q['cursor'])
        except: raise web.HTTPBadRequest(text=json.dumps({'error':'bad cursor'}),content_type='application/json')
    return n,cur
async def health(req): return ok({'status':'ok','node_id':NODE})
async def register(req):
    try:
        d=await body(req); un=d['username']; pw=d['password']
        if not isinstance(un,str) or not re.fullmatch(r'[A-Za-z0-9_]+',un) or not isinstance(pw,str) or len(pw)<8: return err(400,'invalid')
    except: return err(400,'invalid')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(un,)).fetchone(): return err(409,'duplicate')
        cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(un,core.hash_password(pw),d.get('display_name') or un,core.now_iso()))
        uid=cur.lastrowid; t=core.issue_token(c,uid); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    return ok({'user':core.user_to_json(r),'token':t},201)
async def login(req):
    d=await body(req); c=core.connect(); r=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone(); c.close()
    if not r or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],r['password']): return err(401,'invalid')
    with core.tx() as c:t=core.issue_token(c,r['id'])
    return ok({'user':core.user_to_json(r),'token':t})
async def me(req): return ok({'user':core.user_to_json(user(req))})
async def workspaces(req):
    u=user(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close(); return ok({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    u=user(req); d=await body(req); slug,name=d.get('slug'),d.get('name')
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not isinstance(name,str): return err(400,'invalid')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(slug,)).fetchone(): return err(409,'duplicate')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id'])); wid=cur.lastrowid
        ch=core.create_channel_row(c,wid,'general',0,'',u['id']); core.add_workspace_member(c,wid,u['id'],'owner')
    return ok({'workspace':core.workspace_to_json(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()) if False else core.workspace_to_json(core.get_workspace(slug)),'general_channel':core.channel_to_json(ch)},201)
async def wdetail(req):
    u=user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); include=req.query.get('include_archived') in ('1','true')
    q='SELECT DISTINCT c.* FROM channels c LEFT JOIN channel_members cm ON cm.channel_id=c.id AND cm.user_id=? WHERE c.workspace_id=? AND (c.is_private=0 OR cm.user_id IS NOT NULL)'+('' if include else ' AND c.is_archived=0')
    ch=c.execute(q,(u['id'],w['id'])).fetchall()
    c.close()
    return ok({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in ch],'read_state':[read_obj(u,x) for x in ch]})
async def members(req):
    u=user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT m.*,u.username,u.display_name FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return ok({'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in rows]})
async def create_channel(req):
    u=user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin','member'):return err(403,'forbidden')
    d=await body(req); name=d.get('name'); private=d.get('is_private',False); topic=d.get('topic','')
    if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',name) or not isinstance(private,bool) or not isinstance(topic,str) or len(topic)>250:return err(400,'invalid')
    if private and role not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():return err(409,'duplicate')
        ch=core.create_channel_row(c,w['id'],name,private,topic,u['id'])
    return ok({'channel':core.channel_to_json(ch)},201)
async def join(req):
    u=user(req); ch=getc(int(req.match_info['id'])); c0=core.connect(); w=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c0.close(); role=core.member_role(w['id'],u['id'])
    if ch['is_private'] and not role:return err(403,'forbidden')
    with core.tx() as c:
        if not role: core.add_workspace_member(c,w['id'],u['id'],'member')
        added=core.add_channel_member(c,ch['id'],u['id'])
        if added: core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return ok({'channel':core.channel_to_json(ch)})
async def channel_members(req):
    u=user(req); ch=getc(int(req.match_info['id'])); access(u,ch); c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close(); return ok({'members':[core.user_to_json(x) for x in rows]})
async def patch_channel(req):
    u=user(req); ch=getc(int(req.match_info['id'])); w0=core.connect(); wr=w0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); w0.close(); role=core.member_role(ch['workspace_id'],u['id']); c0=core.connect(); cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close();
    if not cm and wr['owner_id']!=u['id']: return err(403,'forbidden')
    if ch['is_archived']: return err(423,'archived')
    d=await body(req); topic=d.get('topic')
    if not isinstance(topic,str) or len(topic)>250:return err(400,'invalid')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
    return ok({'channel':core.channel_to_json(ch)})
async def leave(req):
    u=user(req); ch=getc(int(req.match_info['id']));
    if not core.member_role(ch['workspace_id'],u['id']): return err(403,'forbidden')
    with core.tx() as c:
        cur=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if cur.rowcount: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return ok({'channel':core.channel_to_json(ch)})
async def archive(req):
    u=user(req); ch=getc(int(req.match_info['id'])); c0=core.connect(); w=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c0.close()
    if w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:
        target=0 if req.path.endswith('/unarchive') else 1
        old=bool(ch['is_archived'])
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(target,ch['id']))
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        if old != bool(target):
            core.emit_event(c,ch['id'],'channel.archived' if target else 'channel.unarchived',{'channel':core.channel_to_json(ch)})
    return ok({'channel':core.channel_to_json(ch)})
async def messages(req):
    u=user(req); ch=getc(int(req.match_info['id'])); access(u,ch,req.method=='POST'); n,cur=paging(req.query,'msg'); c=core.connect()
    if req.method=='GET':
        sql='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'+(' AND id<?' if cur else '')+' ORDER BY id DESC LIMIT ?'; rows=c.execute(sql,(ch['id'],cur,n) if cur else (ch['id'],n)).fetchall(); c.close(); nxt=core.encode_cursor('msg',rows[-1]['id']) if len(rows)==n else None; return ok({'messages':[msgjson(x) for x in rows],'next_cursor':nxt})
    d=await body(req); b=d.get('body'); pid=d.get('parent_id')
    if not isinstance(b,str) or not b.strip() or (pid is not None and not isinstance(pid,int)):return err(400,'invalid')
    if b.startswith('/shrug'):b=(b[6:].strip()+' ¯\\_(ツ)_/¯').strip()
    with core.tx() as c:
        if pid and not c.execute('SELECT 1 FROM messages WHERE id=? AND channel_id=? AND parent_id IS NULL AND deleted=0',(pid,ch['id'])).fetchone():return err(404,'parent')
        ment=mentions(c,b,ch,u['id']); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],b,pid,core.now_iso(),json.dumps(ment))); mid=cur.lastrowid; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); n=core.emit_event(c,ch['id'],'message.reply' if pid else 'message.created',{'message':msgjson(r)})
    return ok({'message':msgjson(r)},201)
async def replies(req):
    u=user(req); c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone()
    if not r:return err(404,'not found')
    ch=getc(r['channel_id']); access(u,ch); n,cur=paging(req.query,'rep')
    sql='SELECT * FROM messages WHERE parent_id=? AND deleted=0'+(' AND id>?' if cur else '')+' ORDER BY id ASC LIMIT ?'
    rows=c.execute(sql,(r['id'],cur,n) if cur else (r['id'],n)).fetchall(); c.close()
    return ok({'replies':[msgjson(x) for x in rows],'next_cursor':core.encode_cursor('rep',rows[-1]['id']) if len(rows)==n else None})
async def edit(req):
    u=user(req); d=await body(req); b=d.get('body')
    if not isinstance(b,str) or not b.strip():return err(400,'invalid')
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone()
    if not r:return err(404,'not found')
    if r['deleted']:return err(409,'deleted')
    if r['author_id']!=u['id']:return err(403,'forbidden')
    ch=getc(r['channel_id']); access(u,ch,True)
    ment=mentions(c,b,ch,u['id']); c.close()
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(b,core.now_iso(),json.dumps(ment),r['id']))
        x=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone(); core.emit_event(c,ch['id'],'message.updated',{'message':msgjson(x)})
    return ok({'message':msgjson(x)})
async def delete(req):
    u=user(req); c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return err(404,'not found')
    ch=getc(r['channel_id']); c0=core.connect(); w=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c0.close()
    if r['author_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],))
        if not r['deleted']:core.emit_event(c,ch['id'],'message.deleted',{'message_id':r['id']})
    return ok({'deleted':True})
async def reaction(req):
    u=user(req); d=await body(req); emoji=d.get('emoji')
    if not isinstance(emoji,str) or not emoji:return err(400,'invalid')
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return err(404,'not found')
    ch=getc(r['channel_id']); access(u,ch,True)
    with core.tx() as c:
        before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji)).fetchone()
        c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r['id'],u['id'],emoji))
        if not before:core.emit_event(c,ch['id'],'reaction.added',{'message_id':r['id'],'emoji':emoji})
    fresh=core.connect().execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone(); return ok({'reactions':msgjson(fresh)['reactions']})
async def remove_reaction(req):
    u=user(req); emoji=req.match_info['emoji']
    c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return err(404,'not found')
    ch=getc(r['channel_id']); access(u,ch,True)
    with core.tx() as c:
        cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji))
        if cur.rowcount: core.emit_event(c,ch['id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji})
        fresh=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone()
    return ok({'reactions':msgjson(fresh)['reactions']})

async def pin(req):
    u=user(req); c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return err(404,'not found')
    ch=getc(r['channel_id']); access(u,ch,True)
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(r['id'],u['id'],core.now_iso()))
        p=c.execute('SELECT * FROM pins WHERE message_id=?',(r['id'],)).fetchone()
    return ok({'pin':{'message_id':p['message_id'],'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']}})
async def pins(req):
    u=user(req); ch=getc(int(req.match_info['id'])); access(u,ch)
    n,cur=paging(req.query,'pin'); c=core.connect()
    sql='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND m.deleted=0'+(' AND p.message_id<?' if cur else '')+' ORDER BY p.message_id DESC LIMIT ?'
    rows=c.execute(sql,(ch['id'],cur,n) if cur else (ch['id'],n)).fetchall(); c.close()
    out=[]
    for r in rows:
        out.append({'message':msgjson(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']})
    return ok({'pins':out,'next_cursor':core.encode_cursor('pin',rows[-1]['message_id']) if len(rows)==n else None})
async def unpin(req):
    u=user(req); c=core.connect(); r=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return err(404,'not found')
    ch=getc(r['channel_id']); access(u,ch,True)
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(r['id'],))
    return ok({'unpinned':True})

async def search(req):
    u=user(req)
    qtext=req.query.get('q')
    if not isinstance(qtext,str) or not qtext:
        return err(400,'missing q')
    n,cur=paging(req.query,'srch')
    wsid=req.query.get('workspace_id')
    if wsid is not None:
        try: wsid=int(wsid)
        except: return err(400,'invalid workspace_id')
    c=core.connect()
    sql='SELECT DISTINCT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN workspace_members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? LEFT JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? WHERE m.deleted=0 AND (ch.is_private=0 OR cm.user_id IS NOT NULL) AND lower(m.body) LIKE lower(?)'
    args=[u['id'],u['id'],'%'+qtext+'%']
    if wsid is not None:
        sql+=' AND ch.workspace_id=?'
        args.append(wsid)
    if cur is not None:
        sql+=' AND m.id<?'
        args.append(cur)
    sql+=' ORDER BY m.id DESC LIMIT ?'
    args.append(n)
    rows=c.execute(sql,args).fetchall()
    c.close()
    return ok({'results':[msgjson(x) for x in rows],'next_cursor':core.encode_cursor('srch',rows[-1]['id']) if len(rows)==n else None})
def read_obj(u,ch):
    c=core.connect()
    x=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
    last=x['last_read_event_id'] if x else 0
    rows=c.execute("""SELECT m.author_id,m.mentions,MAX(e.event_id) AS latest_event
        FROM messages m JOIN events e
        ON e.channel_id=m.channel_id
        AND (json_extract(e.payload,'$.message.id')=m.id
             OR json_extract(e.payload,'$.message_id')=m.id)
        WHERE m.channel_id=? AND m.deleted=0
        GROUP BY m.id
        HAVING latest_event>?""",(ch['id'],last)).fetchall()
    c.close()
    unread=[r for r in rows if r['author_id']!=u['id']]
    return {'channel_id':ch['id'],'last_read_event_id':last,
            'unread_count':len(unread),
            'mention_count':sum(u['id'] in json.loads(r['mentions'] or '[]') for r in unread)}
async def readstate(req):
    u=user(req); ch=getc(int(req.match_info['id'])); access(u,ch)
    if req.method=='POST':
        d=await body(req); v=d.get('last_read_event_id')
        if type(v) is not int or v<0:return err(400,'invalid')
        with core.tx() as c:c.execute('INSERT INTO reads VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=max(last_read_event_id,excluded.last_read_event_id)',(u['id'],ch['id'],v))
    return ok({'read_state':read_obj(u,ch)})
async def ws(req):
    u=core.user_for_token(req.query.get('token'))
    if not u:return web.Response(status=401)
    w=web.WebSocketResponse(); await w.prepare(req); subs={}
    async def poll():
        while not w.closed:
            for cid,last in list(subs.items()):
                c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall(); c.close()
                for x in rows:
                    f=json.loads(x['payload']); await w.send_json(f); subs[cid]=f['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    async for m in w:
        if m.type==WSMsgType.TEXT:
            try:
                d=json.loads(m.data); cid=int(d['channel_id'])
                if d['type']=='subscribe':
                    c=core.connect(); h=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); subs[cid]=h['last_event_id'] if h else 0; await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
                elif d['type']=='resume':
                    since=int(d.get('since_event_id',0))
                    c=core.connect()
                    rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
                    h=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()
                    c.close()
                    for x in rows: await w.send_json(json.loads(x['payload']))
                    head=h['last_event_id'] if h else since
                    subs[cid]=head
                    await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
            except: pass
    task.cancel(); return w
app=web.Application()
app.router.add_get('/api/health',health); app.router.add_post('/api/auth/register',register); app.router.add_post('/api/auth/login',login); app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces); app.router.add_post('/api/workspaces',create_workspace); app.router.add_get('/api/workspaces/{slug}',wdetail); app.router.add_get('/api/workspaces/{slug}/members',members); app.router.add_post('/api/workspaces/{slug}/channels',create_channel)
app.router.add_post('/api/channels/{id}/join',join); app.router.add_get('/api/channels/{id}/members',channel_members); app.router.add_patch('/api/channels/{id}',patch_channel); app.router.add_post('/api/channels/{id}/leave',leave); app.router.add_post('/api/channels/{id}/archive',archive); app.router.add_post('/api/channels/{id}/unarchive',archive)
app.router.add_route('*','/api/channels/{id}/messages',messages); app.router.add_get('/api/messages/{id}/replies',replies); app.router.add_patch('/api/messages/{id}',edit); app.router.add_delete('/api/messages/{id}',delete); app.router.add_post('/api/messages/{id}/reactions',reaction); app.router.add_delete('/api/messages/{id}/reactions/{emoji}',remove_reaction); app.router.add_post('/api/messages/{id}/pin',pin); app.router.add_delete('/api/messages/{id}/pin',unpin); app.router.add_get('/api/channels/{id}/pins',pins); app.router.add_get('/api/search',search); app.router.add_route('*','/api/channels/{id}/read',readstate); app.router.add_get('/api/ws',ws)
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    core.init_db(); web.run_app(app,host='127.0.0.1',port=8000+NODE)
