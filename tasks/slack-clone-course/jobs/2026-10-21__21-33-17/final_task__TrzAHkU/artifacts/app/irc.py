import asyncio,json,re
import core
HOST='0.0.0.0'; PORT=6667
connections=set()
class Conn:
 def __init__(self,r,w):
  self.r=r;self.w=w;self.token=None;self.nick=None;self.user=None;self.registered=False;self.joined={};self.last={}
 def send(self,s):
  try:self.w.write((s+'\r\n').encode())
  except:pass
 def numeric(self,n,text):
  self.send(':huddle '+n+' '+(self.nick or '*')+' '+text)
 async def register(self):
  if self.registered or not self.nick or self.user is None:return
  if not core.user_for_token(self.token):
   self.numeric('464',':Password incorrect');self.w.close();return
  self.registered=True
  for n,msg in [('001',':Welcome to Huddle'),('002',':Your host is huddle'),('003',':This server was created today'),('004',':huddle huddle 1.0 oi'),('005',':CHANTYPES=# NETWORK=huddle')]:self.numeric(n,msg)
  self.numeric('422',':MOTD File is missing')
 async def handle(self,line):
  if not line:return
  parts=line.split(' ',2);cmd=parts[0].upper();rest=parts[1:]
  if cmd=='PASS' and rest:self.token=rest[0]
  elif cmd=='NICK' and rest:
   n=rest[0]
   if any(x is not self and x.nick==n for x in connections):self.send(':huddle 433 * '+n+' :Nickname is already in use');return
   self.nick=n
  elif cmd=='USER' and rest:self.user=rest[0]
  elif cmd=='PING':
   x=rest[-1].lstrip(':') if rest else '';self.send(':huddle PONG huddle :'+x)
  elif cmd in ('PONG','MODE','WHO','LIST','TOPIC'):pass
  elif cmd=='QUIT':
   self.w.close();return
  await self.register()
  if not self.registered:return
  if cmd=='JOIN' and rest:
   name=rest[0]
   if not name.startswith('#') or '/' not in name:
    self.numeric('403',name+ ' :No such channel');return
   slug,chname=name[1:].split('/',1); w=core.get_workspace(slug)
   c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],chname)).fetchone() if w else None;c.close()
   u=core.user_for_token(self.token)
   if not ch or not u or (ch['is_private'] and not core.channel_access(u['id'],ch)) or (not core.member_role(w['id'],u['id']) and w['join_mode']=='invite_only'):
    self.numeric('403',name+' :No such channel');return
   with core.tx() as c:
    if not core.member_role(w['id'],u['id']):core.add_workspace_member(c,w['id'],u['id'],'member')
    if core.add_channel_member(c,ch['id'],u['id']):core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
   self.joined[ch['id']]=name;self.last.setdefault(ch['id'],core.head_event_id(ch['id']))
   self.send(':'+self.nick+'!'+self.user+'@huddle JOIN '+name)
   c=core.connect();ns=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
   self.numeric('353','= '+name+' :'+ ' '.join(x['username'] for x in ns));self.numeric('366',name+' :End of /NAMES list')
  elif cmd=='PRIVMSG' and len(parts)>=3:
   target=parts[1];body=parts[2].lstrip(':')
   if target.startswith('#') and '/' in target:
    slug,chname=target[1:].split('/',1);w=core.get_workspace(slug);c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],chname)).fetchone() if w else None;c.close()
    if ch and ch['id'] in self.joined:
     u=core.user_for_token(self.token);core.create_message(ch['id'],u['id'],body)
  elif cmd=='NAMES' and rest:pass
  elif cmd not in ('PASS','USER','NICK'):
   self.numeric('421',cmd+' :Unknown command')
async def deliver():
 while True:
  for x in list(connections):
   for cid,name in list(x.joined.items()):
    for e in core.events_after(cid,x.last.get(cid,0)):
     d=json.loads(e['payload']);x.last[cid]=e['event_id']
     if d.get('type') in ('message.created','message.reply'):
      m=d.get('message') or {};a=m.get('author') or {}
      x.send(':'+a.get('username','huddle')+'!'+a.get('username','huddle')+'@huddle PRIVMSG '+name+' :'+m.get('body',''))
  await asyncio.sleep(.05)
async def handle(r,w):
 x=Conn(r,w);connections.add(x)
 try:
  while not w.is_closing():
   line=await r.readline()
   if not line:break
   await x.handle(line.decode(errors='ignore').strip())
 finally:connections.discard(x);w.close()
async def main():
 core.init_db()
 asyncio.create_task(deliver())
 srv=await asyncio.start_server(handle,HOST,PORT)
 async with srv:
  await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
