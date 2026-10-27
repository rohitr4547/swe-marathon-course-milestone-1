import asyncio,core
CONNS=[]
NICKS={}
def line(w,s):
 w.write((s+'\r\n').encode())
async def find_channel(name):
 if not name.startswith('#') or '/' not in name[1:]: return None
 slug,cname=name[1:].split('/',1);w=core.get_workspace(slug)
 if not w:return None
 c=core.connect();x=c.execute('select * from channels where workspace_id=? and name=?',(w['id'],cname)).fetchone();c.close()
 return x
async def deliver(conn):
 while not conn['closed']:
  for cid in list(conn['joined']):
   for ev in core.events_after(cid,conn['last'].get(cid,0)):
    conn['last'][cid]=ev['event_id']
    if ev.get('type') not in ('message.created','message.reply'):continue
    if ev['event_id'] in conn['skip']:conn['skip'].discard(ev['event_id']);continue
    m=ev.get('message') or {};a=m.get('author') or {};body=m.get('body','')
    x=core.get_channel(cid);w=core.get_workspace_by_id(x['workspace_id'])
    target=f"#{w['slug']}/{x['name']}"
    try:line(conn['writer'],f":{a.get('username','user')}!{a.get('username','user')}@huddle PRIVMSG {target} :{body}");await conn['writer'].drain()
    except:conn['closed']=True
  await asyncio.sleep(.05)
async def handle(reader,writer):
 conn={'reader':reader,'writer':writer,'user':None,'nick':None,'joined':set(),'last':{},'skip':set(),'closed':False}
 CONNS.append(conn);task=None
 try:
  while not reader.at_eof() and not conn['closed']:
   raw=await reader.readline()
   if not raw:break
   z=raw.decode(errors='ignore').rstrip('\r\n')
   if not z:continue
   p=z.split(' ',1);cmd=p[0].upper();rest=p[1] if len(p)>1 else ''
   if cmd=='PASS':conn['user']=core.user_for_token(rest.strip())
   elif cmd=='NICK':
    nick=rest.strip()
    if nick in NICKS and NICKS[nick] is not conn:line(writer,f":huddle 433 * {nick} :Nickname is already in use");continue
    if conn['nick']:NICKS.pop(conn['nick'],None)
    conn['nick']=nick;NICKS[nick]=conn
   elif cmd=='USER':conn['userline']=True
   elif cmd=='QUIT':break
   elif cmd=='PING':
    t=rest[1:] if rest.startswith(':') else rest;line(writer,f":huddle PONG huddle :{t}")
   elif cmd=='JOIN':
    if not conn['user'] or not conn.get('userline') or not conn['nick']:
     line(writer,f":huddle 464 {conn.get('nick') or '*'} :Password incorrect");break
    name=rest.split()[0] if rest.split() else ''
    x=await find_channel(name)
    if not x:
     line(writer,f":huddle 403 {conn['nick']} {name} :No such channel");continue
    try:core.join_channel(conn['user'],x['id'])
    except:line(writer,f":huddle 403 {conn['nick']} {name} :No such channel");continue
    conn['joined'].add(x['id']);conn['last'][x['id']]=core.head_event_id(x['id'])
    c=core.connect();ns=c.execute('select u.username from channel_members m join users u on u.id=m.user_id where m.channel_id=?',(x['id'],)).fetchall();c.close()
    line(writer,f":{conn['nick']}!{conn['user']['username']}@huddle JOIN {name}")
    line(writer,f":huddle 353 {conn['nick']} = {name} :"+' '.join(n['username'] for n in ns))
    line(writer,f":huddle 366 {conn['nick']} {name} :End of /NAMES list")
   elif cmd=='PRIVMSG':
    q=rest.split(' ',1)
    if len(q)<2:continue
    target=q[0];body=q[1][1:] if q[1].startswith(':') else q[1]
    x=await find_channel(target)
    if not x or x['id'] not in conn['joined']:continue
    try:
     m=core.create_message(conn['user'],x['id'],body);conn['skip'].add(m.get('event_id',core.head_event_id(x['id'])))
    except:pass
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):continue
   else:line(writer,f":huddle 421 {conn.get('nick') or '*'} {cmd} :Unknown command")
   if conn.get('userline') and conn['nick'] and not conn.get('registered'):
    if not conn.get('user'):
     line(writer,f":huddle 464 {conn['nick']} :Password incorrect")
     await writer.drain()
     break
    conn['registered']=True
    for n in ('001','002','003','004','005'):line(writer,f":huddle {n} {conn['nick']} :Welcome")
    line(writer,f":huddle 422 {conn['nick']} :MOTD File is missing")
    await writer.drain()
    task=asyncio.create_task(deliver(conn))
 finally:
  conn['closed']=True
  if task:task.cancel()
  if conn.get('nick'):NICKS.pop(conn['nick'],None)
  
  if conn in CONNS: CONNS.remove(conn)
  writer.close()
  try:await writer.wait_closed()
  except:pass
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s:await s.serve_forever()
if __name__=='__main__':asyncio.run(main())
