import asyncio,json,re,core
CONNS=[]
def send(w,s):
 try:w.write((s+'\r\n').encode())
 except:pass
def chan_name(ch):
 w=core.get_workspace_id(ch['workspace_id'])
 return '#'+w['slug']+'/'+ch['name']
async def deliver(c):
 while not c['closed']:
  for name,chid in list(c['joined'].items()):
   for e in core.events_after(chid,c['last'].get(chid,0)):
    c['last'][chid]=e['event_id']
    try:
     x=json.loads(e['payload'])
     if x.get('type') not in ('message.created','message.reply'): continue
     m=x.get('message') or {}; a=m.get('author') or {}; nick=a.get('username','user')
     if m.get('author_id')==c['user']['id']: continue
     send(c['writer'],':%s!%s@huddle PRIVMSG %s :%s'%(nick,nick,name,m.get('body','')))
    except Exception: pass
  await asyncio.sleep(.05)
async def handle(reader,writer):
 c={'reader':reader,'writer':writer,'token':None,'nick':None,'userline':'user','user':None,'joined':{},'last':{},'closed':False}
 CONNS.append(c); registered=False; task=asyncio.create_task(deliver(c))
 try:
  while True:
   line=await reader.readline()
   if not line: break
   line=line.decode(errors='ignore').strip('\r\n')
   if not line: continue
   z=line.split(' ',1);cmd=z[0].upper();rest=z[1] if len(z)>1 else ''
   args=rest.split(' ') if rest else []
   if cmd=='PASS': c['token']=rest.lstrip(':'); continue
   if cmd=='NICK':
    nick=args[0] if args else ''
    if any(x is not c and x.get('nick')==nick for x in CONNS):
     send(writer,':huddle 433 * %s :Nickname is already in use'%nick);continue
    c['nick']=nick
   elif cmd=='USER':
    c['userline']=args[0] if args else 'user'
   elif cmd=='PING':
    tok=rest[1:] if rest.startswith(':') else rest;send(writer,':huddle PONG huddle :%s'%tok);continue
   elif cmd=='QUIT':break
   if not registered and c['nick'] and c['userline'] and c['token']:
    c['user']=core.user_for_token(c['token'])
    if not c['user']:
     send(writer,':huddle 464 * :Password incorrect');break
    registered=True
    for n,msg in [('001','Welcome to huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1.0'),('005','CHANTYPES=# PREFIX=(o)~ :are supported'),('422','MOTD File is missing')]:
     send(writer,':huddle %s %s :%s'%(n,c['nick'],msg))
    continue
   if not registered:
    continue
   if cmd=='JOIN':
    target=args[0] if args else ''
    m=re.fullmatch(r'#([^/]+)/([^/]+)',target)
    ch=None
    if m:
     w=core.get_workspace(m.group(1)); 
     if w:
      cc=core.connect();ch=cc.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],m.group(2))).fetchone();cc.close()
    if not ch or ch['is_private'] or not core.can_read_channel(c['user']['id'],ch):
     send(writer,':huddle 403 %s %s :No such channel'%(c['nick'],target));continue
    with core.tx() as tx:
     core.add_channel_member(tx,ch['id'],c['user']['id'])
    c['joined'][target]=ch['id'];c['last'][ch['id']]=core.head_event_id(ch['id'])
    send(writer,':%s!%s@huddle JOIN %s'%(c['nick'],c['userline'],target))
    cc=core.connect();names=cc.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall();cc.close()
    send(writer,':huddle 353 %s = %s :%s'%(c['nick'],target,' '.join(x[0] for x in names)))
    send(writer,':huddle 366 %s %s :End of /NAMES list'%(c['nick'],target))
   elif cmd=='PRIVMSG':
    target=args[0] if args else '';body=rest.split(' :',1)[1] if ' :' in rest else ''
    if target in c['joined'] and body:
     core.create_message(c['user']['id'],c['joined'][target],body)
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
    continue
   else:
    send(writer,':huddle 421 %s %s :Unknown command'%(c['nick'],cmd))
 finally:
  c['closed']=True;task.cancel();CONNS.remove(c) if c in CONNS else None;writer.close();await writer.wait_closed()
async def main():
 core.init_db();srv=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with srv: await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
