import asyncio, core, json, re
clients=[]
nicks=set()
async def line(w,s):
    w.write((s+'\r\n').encode())
    await w.drain()
def find_channel(name):
    if not name.startswith('#') or '/' not in name[1:]: return None
    slug,cname=name[1:].split('/',1)
    w=core.get_workspace(slug)
    if not w:return None
    c=core.connect(); r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone();c.close()
    return r
async def deliver(conn):
    while not conn['closed']:
        for name,ch in list(conn['joined'].items()):
            for e in core.events_after(ch['id'],conn['last'].get(ch['id'],0)):
                conn['last'][ch['id']]=e['event_id']
                try:f=json.loads(e['payload'])
                except:continue
                if f.get('type') not in ('message.created','message.reply'):continue
                m=f.get('message') or {}; a=m.get('author') or {}; nick=a.get('username','user')
                if m.get('id') in conn.get('sent',set()): continue
                await line(conn['writer'],f":{nick}!{nick}@huddle PRIVMSG {name} :{m.get('body','')}")
        await asyncio.sleep(.05)
async def handle(reader,writer):
    conn={'reader':reader,'writer':writer,'nick':None,'user':None,'joined':{},'last':{},'sent':set(),'registered':False,'closed':False}
    clients.append(conn); task=asyncio.create_task(deliver(conn))
    try:
        while True:
            raw=await reader.readline()
            if not raw:break
            text=raw.decode(errors='ignore').strip('\r\n')
            if not text:continue
            parts=text.split(' ',2); cmd=parts[0].upper(); args=parts[1:]
            if cmd=='PASS':
                conn['token']=args[0] if args else ''
            elif cmd=='NICK':
                nick=args[0] if args else ''
                if nick in nicks and nick!=conn['nick']:
                    await line(writer,f":huddle 433 {conn['nick'] or '*'} {nick} :Nickname is already in use");continue
                if conn['nick']:nicks.discard(conn['nick'])
                conn['nick']=nick;nicks.add(nick)
            elif cmd=='USER':
                conn['user']=args[0] if args else 'user'
            if conn['nick'] and conn['user'] and not conn['registered']:
                u=core.user_for_token(conn.get('token',''))
                if not u:
                    await line(writer,f":huddle 464 {conn['nick']} :Password incorrect");break
                conn['dbuser']=u;conn['registered']=True
                for code,msg in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created now'),('004','huddle'),('005','CHANTYPES=#')]:
                    await line(writer,f":huddle {code} {conn['nick']} :{msg}")
                await line(writer,f":huddle 422 {conn['nick']} :MOTD File is missing")
                continue
            if not conn['registered']:continue
            if cmd=='PING':
                tok=args[-1].lstrip(':') if args else ''
                await line(writer,f":huddle PONG huddle :{tok}")
            elif cmd=='QUIT':break
            elif cmd=='JOIN':
                name=args[0] if args else ''; ch=find_channel(name)
                if not ch:
                    await line(writer,f":huddle 403 {conn['nick']} {name} :No such channel");continue
                try: core.join_channel(conn['dbuser'],ch['id'])
                except: await line(writer,f":huddle 403 {conn['nick']} {name} :No such channel");continue
                conn['joined'][name]=ch;conn['last'][ch['id']]=core.head_event_id(ch['id'])
                await line(writer,f":{conn['nick']}!{conn['user']}@huddle JOIN {name}")
                c=core.connect(); rows=c.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall();c.close()
                await line(writer,f":huddle 353 {conn['nick']} = {name} :{' '.join(x['username'] for x in rows)}")
                await line(writer,f":huddle 366 {conn['nick']} {name} :End of /NAMES list")
            elif cmd=='PRIVMSG':
                name=args[0] if args else ''; body=args[1][1:] if len(args)>1 and args[1].startswith(':') else (args[1] if len(args)>1 else '')
                ch=conn['joined'].get(name)
                if not ch: continue
                try:
                    m=core.create_message(conn['dbuser'],ch['id'],body); conn['sent'].add(m['id'])
                except: pass
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
                continue
            else:
                await line(writer,f":huddle 421 {conn['nick']} {cmd} :Unknown command")
    finally:
        conn['closed']=True;task.cancel()
        if conn in clients: clients.remove(conn)
        if conn.get('nick'):nicks.discard(conn['nick'])
        writer.close();await writer.wait_closed()
async def main():
    core.init_db()
    server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server: await server.serve_forever()
if __name__=='__main__': asyncio.run(main())
