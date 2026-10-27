import asyncio,json,argparse,re
from aiohttp import web
import core
NODE=0
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
async def body(req):
 try:
  x=await req.json()
  if not isinstance(x,dict): raise ValueError()
  return x
 except: raise web.HTTPBadRequest(text=json.dumps({'error':'invalid JSON'}),content_type='application/json')
def user(req):
 h=req.headers.get('Authorization','')
 if not h.startswith('Bearer '): raise web.HTTPUnauthorized(content_type='application/json',text=json.dumps({'error':'unauthorized'}))
 u=core.user_for_token(h[7:])
 if not u: raise web.HTTPUnauthorized(content_type='application/json',text=json.dumps({'error':'unauthorized'}))
 return u
def access(u,ch):
 if not core.can_read_channel(u['id'],ch): raise web.HTTPForbidden(content_type='application/json',text=json.dumps({'error':'forbidden'}))
def paging(req,kind):
 try:
  lim=int(req.query.get('limit','50'))
  if lim<1 or lim>200: raise ValueError()
  cur=core.decode_cursor(kind,req.query['cursor']) if req.query.get('cursor') else None
  if 'page' in req.query or 'offset' in req.query: raise ValueError()
  return lim,cur
 except: raise web.HTTPBadRequest(content_type='application/json',text=json.dumps({'error':'invalid paging'}))
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
 d=await body(req); n=d.get('username'); p=d.get('password')
 if not isinstance(n,str) or not re.fullmatch(r'[A-Za-z0-9_]+',n) or not isinstance(p,str) or len(p)<8:return err(400,'invalid registration')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(n,core.hash_password(p),d.get('display_name') or n))
   u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
  return out({'user':core.user_to_json(u),'token':t},201)
 except: return err(409,'username exists')
async def login(req):
 d=await body(req); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone(); c.close()
 if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']): return err(401,'invalid credentials')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return out({'user':core.user_to_json(u),'token':t})
async def me(req): return out({'user':core.user_to_json(user(req))})
async def workspaces(req):
 u=user(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
 return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(req):
 u=user(req);d=await body(req); slug=d.get('slug');name=d.get('name')
 if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not isinstance(name,str) or not name:return err(400,'invalid workspace')
 try:
  with core.tx() as c:
   c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id']));w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
   core.add_workspace_member(c,w['id'],u['id'],'owner');ch=core.create_channel_row(c,w['id'],'general',0,'',u['id'])
  return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
 except: return err(409,'workspace exists')
async def ws_detail(req):
 u=user(req);w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 _,chs,states=core.workspace_detail(w,u['id'],req.query.get('include_archived') in ('1','true'))
 return out({'workspace':core.workspace_to_json(w),'channels':chs,'read_state':states})
async def ws_members(req):
 u=user(req);w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect();rs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
 return out({'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in rs]})
async def create_channel(req):
 u=user(req);w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 role=core.member_role(w['id'],u['id']);d=await body(req);n=d.get('name');pr=d.get('is_private',False)
 if role not in ('owner','admin','member') or (pr and role not in ('owner','admin')):return err(403,'forbidden')
 if not isinstance(n,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',n) or not isinstance(pr,bool) or not isinstance(d.get('topic',''),str) or len(d.get('topic',''))>250:return err(400,'invalid channel')
 try:
  with core.tx() as c: ch=core.create_channel_row(c,w['id'],n,pr,d.get('topic',''),u['id'])
  return out({'channel':core.channel_to_json(ch)},201)
 except:return err(409,'channel exists')
async def join(req):
 u=user(req); ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 try: core.join_channel(u,ch['id']); return out({'channel':core.channel_to_json(ch)})
 except: return err(403,'forbidden')
async def messages(req):
 u=user(req);ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 access(u,ch);lim,cur=paging(req,'msg');c=core.connect();q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL';args=[ch['id']]
 if cur is not None:q+=' AND id<?';args.append(cur)
 q+=' ORDER BY id DESC LIMIT ?';args.append(lim+1);rs=c.execute(q,args).fetchall();c.close();more=len(rs)>lim;rs=rs[:lim]
 return out({'messages':[core.msg_json(x) for x in rs],'next_cursor':core.encode_cursor('msg',rs[-1]['id']) if more and rs else None})
async def post(req):
 u=user(req);ch=core.get_channel(int(req.match_info['id']))
 if not ch:return err(404,'not found')
 d=await body(req);b=d.get('body');parent=d.get('parent_id')
 if parent is not None and (isinstance(parent,bool) or not isinstance(parent,int)):return err(400,'invalid parent')
 try:r=core.create_message(u,ch['id'],b,parent);return out({'message':core.msg_json(r)},201)
 except KeyError:return err(404,'not found')
 except PermissionError:return err(403,'forbidden')
 except RuntimeError:return err(423,'archived')
 except:return err(400,'invalid message')
async def replies(req):
 u=user(req);p=core.get_channel(0);c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id']);access(u,ch);lim,cur=paging(req,'rep');c=core.connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';a=[m['id']]
 if cur is not None:q+=' AND id>?';a.append(cur)
 q+=' ORDER BY id ASC LIMIT ?';a.append(lim+1);rs=c.execute(q,a).fetchall();c.close();more=len(rs)>lim;rs=rs[:lim]
 return out({'replies':[core.msg_json(x) for x in rs],'next_cursor':core.encode_cursor('rep',rs[-1]['id']) if more and rs else None})
async def react(req):
 u=user(req);d=await body(req);mid=int(req.match_info['id']);emoji=d.get('emoji')
 if not isinstance(emoji,str) or not emoji:return err(400,'invalid emoji')
 c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
 if not m:return err(404,'not found')
 ch=core.get_channel(m['channel_id']);access(u,ch)
 with core.tx() as c:
        z=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
        if z.rowcount: core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
 c=core.connect();rs=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall();c.close()
 return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]})
async def ws(req):
 tok=req.query.get('token');u=core.user_for_token(tok)
 if not u:return web.Response(status=401)
 w=web.WebSocketResponse();await w.prepare(req);subs={};last={}
 async def poll():
  while not w.closed:
   for ch,since in list(last.items()):
    for e in core.events_after(ch,since):
     await w.send_json(json.loads(e['payload']));last[ch]=e['event_id']
   await asyncio.sleep(.05)
 task=asyncio.create_task(poll())
 try:
  async for msg in w:
   if msg.type!=1:continue
   d=json.loads(msg.data);ch=int(d.get('channel_id',0))
   if d.get('type') in ('subscribe','resume'):
    head=core.head_event_id(ch)
    if d['type']=='resume':
     since=int(d.get('since_event_id',0))
     for e in core.events_after(ch,since):
      await w.send_json(json.loads(e['payload']))
     head=core.head_event_id(ch)
    last[ch]=head
    await w.send_json({'type':'subscribed' if d['type']=='subscribe' else 'resumed','channel_id':ch,'head_event_id':head})
 finally:task.cancel()
 return w

async def set_role_route(req):
    u=user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    target=int(req.match_info['user_id']); d=await body(req); role=d.get('role')
    current=core.member_role(w['id'],u['id']); tr=core.member_role(w['id'],target)
    if tr is None:return err(404,'not found')
    if not isinstance(role,str) or role not in ('owner','admin','member','guest'):return err(400,'invalid role')
    if role=='owner':return err(400,'invalid role')
    if target==w['owner_id']:return err(403,'forbidden')
    if current not in ('owner','admin'):return err(403,'forbidden')
    if current=='admin' and tr in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target))
    return out({'member':{'user_id':target,'role':role}})

async def transfer_route(req):
    u=user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if u['id']!=w['owner_id']:return err(403,'forbidden')
    d=await body(req); target=d.get('new_owner_id')
    if not isinstance(target,int):return err(400,'invalid target')
    if core.member_role(w['id'],target)!='admin':return err(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
    return out({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})


async def workspace_patch(req):
    u=user(req)
    try:
        w=core.update_workspace(u,req.match_info['slug'],await body(req))
        return out({'workspace':core.workspace_to_json(w)})
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
    except:return err(400,'invalid workspace')
async def inv_create(req):
    u=user(req)
    try:return out({'invitation':core.invitation_to_json(core.create_invitation(u,req.match_info['slug'],await body(req)))},201)
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
    except:return err(400,'invalid invitation')
async def inv_list(req):
    u=user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(w['id'],core.now_iso())).fetchall();c.close()
    return out({'invitations':[core.invitation_to_json(x) for x in rs]})
async def inv_accept(req):
    u=user(req)
    try:return out({'workspace':core.workspace_to_json(core.accept_invitation(u,req.match_info['code']))})
    except PermissionError:return err(403,'forbidden')
    except:return err(404,'not found')
async def inv_delete(req):
    u=user(req)
    try:core.revoke_invitation(u,req.match_info['slug'],req.match_info['code']);return out({'deleted':True})
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')


async def channel_patch(req):
    u=user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    d=await body(req); topic=d.get('topic')
    if not isinstance(topic,str) or len(topic)>250:return err(400,'invalid topic')
    c0=core.connect(); ismember=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if not ismember and core.get_workspace_by_id(ch['workspace_id'])['owner_id']!=u['id']:return err(403,'forbidden')
    if ch['is_archived']:return err(423,'archived')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def channel_members(req):
    u=user(req);ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return out({'members':[core.user_to_json(x) for x in rs]})
async def channel_leave(req):
    u=user(req);ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch) and core.member_role(ch['workspace_id'],u['id']) is None:return err(403,'forbidden')
    with core.tx() as c:
        z=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if z.rowcount: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return out({'channel':core.channel_to_json(ch)})
async def channel_archive(req):
    u=user(req);ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    w=core.get_workspace_by_id(ch['workspace_id'])
    if w['owner_id']!=u['id']:return err(403,'forbidden')
    archived=req.path.endswith('/archive')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(archived),ch['id']))
        core.emit_event(c,ch['id'],'channel.archived' if archived else 'channel.unarchived',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})


async def message_patch(req):
    u=user(req); mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id']); w=core.get_workspace_by_id(ch['workspace_id'])
    if m['deleted']:return err(409,'deleted')
    if m['author_id']!=u['id']:return err(403,'forbidden')
    d=await body(req); b=d.get('body')
    if not isinstance(b,str) or not b.strip():return err(400,'invalid body')
    men=core.resolve_mentions(core.connect(),b,ch,u['id'])
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(b,core.now_iso(),json.dumps(men),mid))
        r2=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); core.emit_event(c,ch['id'],'message.updated',{'message':core.msg_json(r2)})
    return out({'message':core.msg_json(core.connect().execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
async def message_delete(req):
    u=user(req); mid=int(req.match_info['id']); c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id']);w=core.get_workspace_by_id(ch['workspace_id'])
    if m['author_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); core.emit_event(c,ch['id'],'message.deleted',{'message_id':mid})
    return out({'deleted':True})
async def reaction_delete(req):
    u=user(req); mid=int(req.match_info['id']); emoji=req.match_info['emoji']; c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id']);access(u,ch)
    with core.tx() as c:
        z=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
        if z.rowcount: core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    c=core.connect();rs=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall();c.close()
    return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]})


async def pin_message(req):
    u=user(req)
    try:
        r=core.pin(u,int(req.match_info['id']))
        return out({'pin':{'message_id':r['message_id'],'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']}})
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
async def unpin_message(req):
    u=user(req)
    try: core.unpin(u,int(req.match_info['id'])); return out({'unpinned':True})
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
async def pins(req):
    u=user(req)
    try:
        lim,cur=paging(req,'pin'); vals,n=core.list_pins(u,int(req.match_info['id']),lim,cur)
        return out({'pins':vals,'next_cursor':n})
    except KeyError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
async def search_route(req):
    u=user(req); q=req.query.get('q')
    if not isinstance(q,str) or not q:return err(400,'missing q')
    try: lim,cur=paging(req,'srch')
    except: return err(400,'invalid paging')
    wid=req.query.get('workspace_id')
    if wid is not None:
        try: wid=int(wid)
        except: return err(400,'invalid workspace')
    vals,n=core.search(u,q,lim,cur,wid)
    return out({'results':vals,'next_cursor':n})


async def read_get(req):
    u=user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    access(u,ch)
    return out({'read_state':core.read_state(u['id'],ch['id'])})
async def read_post(req):
    u=user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    access(u,ch); d=await body(req)
    if 'last_read_event_id' not in d:return err(400,'invalid read state')
    try: state=core.mark_read(u['id'],ch['id'],d['last_read_event_id'])
    except: return err(400,'invalid read state')
    return out({'read_state':state})

async def authenticated_not_found(req):
    user(req)
    return err(404,'not found')

async def auth_placeholder(req):
 user(req)
 return err(404,'not found')
async def invitation_placeholder(req):
 user(req)
 return err(404,'not found')
def init():
 app=web.Application();app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me);app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_ws);app.router.add_get('/api/workspaces/{slug}',ws_detail);app.router.add_get('/api/workspaces/{slug}/members',ws_members);app.router.add_patch('/api/workspaces/{slug}',workspace_patch);app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',set_role_route);app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer_route);app.router.add_post('/api/workspaces/{slug}/invitations',inv_create);app.router.add_get('/api/workspaces/{slug}/invitations',inv_list);app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',inv_delete);app.router.add_post('/api/invitations/{code}/accept',inv_accept);app.router.add_post('/api/workspaces/{slug}/channels',create_channel);app.router.add_post('/api/channels/{id}/join',join);app.router.add_patch('/api/channels/{id}',channel_patch);app.router.add_get('/api/channels/{id}/members',channel_members);app.router.add_post('/api/channels/{id}/leave',channel_leave);app.router.add_post('/api/channels/{id}/archive',lambda r: channel_archive(r));app.router.add_post('/api/channels/{id}/unarchive',lambda r: channel_archive(r));app.router.add_get('/api/search',search_route);app.router.add_get('/api/channels/{id}/pins',pins);app.router.add_post('/api/messages/{id}/pin',pin_message);app.router.add_delete('/api/messages/{id}/pin',unpin_message);app.router.add_get('/api/channels/{id}/read',read_get);app.router.add_post('/api/channels/{id}/read',read_post);app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_post('/api/channels/{id}/messages',post);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_post('/api/messages/{id}/reactions',react);app.router.add_patch('/api/messages/{id}',message_patch);app.router.add_delete('/api/messages/{id}',message_delete);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction_delete);app.router.add_get('/api/ws',ws);return app
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);a=p.parse_args();NODE=a.node;core.init_db();web.run_app(init(),host='127.0.0.1',port=8000+NODE)
