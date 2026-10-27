import asyncio, core, re
clients=[]
def send(w,s):
    try:w.write((s+'\r\n').encode())
    except:pass
async def deliver(st):
    while not st['closed']:
        for cid in list(st['joined']):
            for e in core.events_after(cid,st['last'].get(cid,0)):
                st['last'][cid]=e['event_id']
                if e['type'] in ('message.created','message.reply'):
                    m=e.get('message') or {}; c=core.get_channel(cid)
                    if c and m.get('author_id')!=st.get('uid'):
                        x=core.connect(); u=x.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone(); w=x.execute('SELECT slug FROM workspaces WHERE id=?',(c['workspace_id'],)).fetchone(); x.close()
                        if u and w: send(st['writer'],f":{u['username']}!{u['username']}@localhost PRIVMSG #{w['slug']}/{c['name']} :{m.get('body','')}")
        await asyncio.sleep(.05)
async def lookup(name):
    if not name.startswith('#') or '/' not in name:return None
    slug,cn=name[1:].split('/',1)
    c=core.connect(); r=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(slug,cn)).fetchone(); c.close(); return r
async def registration(st):
    if st.get('registered'):return
    if st.get('token') and st.get('nick') and st.get('user'):
        u=core.user_for_token(st['token'])
        if not u:
            send(st['writer'],':huddle 464 * :Password incorrect'); st['bad']=True; return
        st['uid']=u['id']; st['registered']=True; n=st['nick']
        for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:
            send(st['writer'],f':huddle {code} {n} :{text}')
async def handle(reader,writer):
    st={'writer':writer,'token':None,'nick':None,'user':None,'registered':False,'uid':None,'joined':set(),'last':{},'closed':False}
    clients.append(st); task=asyncio.create_task(deliver(st))
    try:
        while True:
            line=await reader.readline()
            if not line:break
            line=line.decode(errors='ignore').rstrip('\r\n')
            if not line:continue
            parts=line.split(' ',2); cmd=parts[0].upper(); args=parts[1:]
            if cmd=='PASS':
                st['token']=args[0] if args else ''
                if not core.user_for_token(st['token']): send(writer,':huddle 464 * :Password incorrect'); st['bad']=True
                await registration(st)
            elif cmd=='NICK':
                nick=args[0] if args else ''
                c=core.connect(); taken=c.execute('SELECT 1 FROM users WHERE username=?',(nick,)).fetchone(); c.close()
                if taken and st.get('nick') and nick!=st['nick']: send(writer,f':huddle 433 * {nick} :Nickname in use')
                elif taken and not st.get('nick') and core.user_for_token(st.get('token','')) and core.user_for_token(st.get('token',''))['username']!=nick: send(writer,f':huddle 433 * {nick} :Nickname in use')
                else: st['nick']=nick; await registration(st)
            elif cmd=='USER':
                st['user']=args[0] if args else ''; await registration(st)
            elif cmd=='PING': send(writer,':huddle PONG huddle '+(line.split(':',1)[1] if ':' in line else (args[0] if args else '')))
            elif cmd=='PONG': pass
            elif cmd=='JOIN':
                if not st.get('registered'): continue
                target=args[0] if args else ''; ch=await lookup(target)
                if not ch: send(writer,f':huddle 403 {st["nick"]} {target} :No such channel'); continue
                st['joined'].add(ch['id']); st['last'][ch['id']]=core.head_event_id(ch['id'])
                send(writer,f':{st["nick"]}!{st["nick"]}@localhost JOIN {target}')
                c=core.connect(); rows=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
                send(writer,f':huddle 353 {st["nick"]} = {target} :{" ".join(x["username"] for x in rows)}')
                send(writer,f':huddle 366 {st["nick"]} {target} :End of /NAMES list.')
            elif cmd=='NAMES':
                target=args[0] if args else ''; ch=await lookup(target)
                if ch:
                    c=core.connect(); rows=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
                    send(writer,f':huddle 353 {st["nick"]} = {target} :{" ".join(x["username"] for x in rows)}'); send(writer,f':huddle 366 {st["nick"]} {target} :End of /NAMES list.')
            elif cmd=='PRIVMSG':
                if len(args)<2:continue
                target=args[0]; body=args[1][1:] if args[1].startswith(':') else args[1]
                ch=await lookup(target)
                if not ch: send(writer,f':huddle 403 {st["nick"]} {target} :No such channel'); continue
                try: core.create_message(st['uid'],ch['id'],body)
                except: pass
            elif cmd=='QUIT':
                break
            elif cmd in ('WHO','LIST','TOPIC','MODE'): pass
            else: send(writer,f':huddle 421 {st.get("nick","*")} {cmd} :Unknown command')
    except Exception as e:
        import traceback; traceback.print_exc()
    finally:
        st['closed']=True; task.cancel(); clients.remove(st) if st in clients else None; writer.close(); await writer.wait_closed()
async def main():
    core.init_db()
    srv=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with srv: await srv.serve_forever()
if __name__=='__main__': asyncio.run(main())
