import asyncio,json,re,core
clients=[]
def line(w,s):
    return w.write((s+'\r\n').encode())
def channel_name(ch):
    c=core.connect(); w=c.execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c.close()
    return '#'+w['slug']+'/'+ch['name'] if w else ''
def find_channel(name):
    if not name.startswith('#') or '/' not in name[1:]: return None
    slug,n=name[1:].split('/',1); c=core.connect()
    r=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(slug,n)).fetchone(); c.close(); return r
async def deliver(cl):
    last={}
    while not cl['closed']:
        try:
            for cid in list(cl['joined']):
                c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last.get(cid,0))).fetchall(); last[cid]=last.get(cid,0); c.close()
                for x in rows:
                    f=json.loads(x['payload']); last[cid]=f['event_id']
                    if f.get('type')!='message.created' or not f.get('message'): continue
                    m=f['message']
                    if m.get('author_id')==cl['user']['id']: continue
                    ac=core.connect(); a=ac.execute('SELECT username FROM users WHERE id=?',(m['author_id'],)).fetchone(); ac.close()
                    ch=core.get_channel(cid); line(cl['writer'],':%s!%s@huddle PRIVMSG %s :%s'%(a['username'],a['username'],channel_name(ch),m['body']))
            await asyncio.sleep(.05)
        except: await asyncio.sleep(.1)
async def handle(reader,writer):
    cl={'writer':writer,'token':None,'nick':None,'userline':None,'user':None,'joined':set(),'closed':False}
    clients.append(cl); task=None
    try:
        while True:
            raw=await reader.readline()
            if not raw: break
            z=raw.decode(errors='ignore').strip()
            if not z: continue
            parts=z.split(' ',1); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''
            if cmd=='PASS': cl['token']=arg.lstrip(':')
            elif cmd=='NICK':
                nick=arg.split()[0] if arg else ''
                if any(x.get('nick')==nick and not x['closed'] for x in clients):
                    line(writer,':huddle 433 * %s :Nickname is already in use'%nick); continue
                cl['nick']=nick
            elif cmd=='USER':
                cl['userline']=arg
            elif cmd=='PING':
                tok=arg[1:] if arg.startswith(':') else arg; line(writer,':huddle PONG huddle :'+tok)
            elif cmd=='QUIT': break
            if cl['nick'] and cl['userline'] and cl['user'] is None:
                cl['user']=core.user_for_token(cl['token'])
                if not cl['user']:
                    line(writer,':huddle 464 * :Password incorrect'); break
                n=cl['nick']
                for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1 0'),('005','CHANTYPES=# :are supported by this server'),('422','No MOTD')]:
                    line(writer,':huddle %s %s :%s'%(code,n,text))
                task=asyncio.create_task(deliver(cl))
            elif cmd=='JOIN' and cl['user']:
                name=arg.split()[0] if arg else ''; ch=find_channel(name)
                if not ch:
                    line(writer,':huddle 403 %s %s :No such channel'%(cl['nick'],name)); continue
                with core.tx() as c:
                    core.add_channel_member(c,ch['id'],cl['user']['id'])
                cl['joined'].add(ch['id'])
                prefix='%s!%s@huddle'%(cl['nick'],cl['nick'])
                line(writer,':%s JOIN %s'%(prefix,name))
                c=core.connect(); rows=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
                line(writer,':huddle 353 %s = %s :%s'%(cl['nick'],name,' '.join(x['username'] for x in rows)))
                line(writer,':huddle 366 %s %s :End of /NAMES list'%(cl['nick'],name))
            elif cmd=='PRIVMSG' and cl['user']:
                bits=arg.split(' ',1); name=bits[0] if bits else ''; text=bits[1][1:] if len(bits)>1 and bits[1].startswith(':') else (bits[1] if len(bits)>1 else '')
                ch=find_channel(name)
                if ch and ch['id'] in cl['joined'] and text:
                    with core.tx() as c:
                        c.execute('INSERT INTO messages(channel_id,author_id,body,created_at,mentions) VALUES(?,?,?,?,?)',(ch['id'],cl['user']['id'],text,core.now_iso(),'[]'))
                        mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                        core.emit_event(c,ch['id'],'message.created',{'message':{'id':mid,'channel_id':ch['id'],'author_id':cl['user']['id'],'body':text}})
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): pass
            elif cl['user'] and cmd not in ('PASS','NICK','USER'): line(writer,':huddle 421 %s %s :Unknown command'%(cl['nick'],cmd))
    finally:
        cl['closed']=True
        if task: task.cancel()
        if cl in clients: clients.remove(cl)
        writer.close()
        try: await writer.wait_closed()
        except: pass
async def main():
    core.init_db(); s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
