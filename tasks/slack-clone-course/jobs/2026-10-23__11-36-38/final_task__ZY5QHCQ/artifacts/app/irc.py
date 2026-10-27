import asyncio,core
connections=set()
nicks=set()
def line(c,x): c.writer.write((x+'\r\n').encode())
def channel_name(ch):
    w=core.get_workspace_by_id(ch['workspace_id'])
    return '#'+w['slug']+'/'+ch['name']
def find_channel(name):
    if not name.startswith('#') or '/' not in name[1:]: return None
    slug,n=name[1:].split('/',1);w=core.get_workspace(slug)
    if not w:return None
    c=core.connect();r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone();c.close();return r
async def deliver(c):
    while not c.closed:
        for cid in list(c.joined):
            for e in core.events_after(cid,c.last_events.get(cid,0)):
                c.last_events[cid]=e['event_id']
                if e.get('type') not in ('message.created','message.reply'):continue
                m=e.get('message') or {}; author=core.user_for_token(c.token)
                if m.get('author_id')==author['id'] and e.get('event_id')==c.own_events.get(cid):continue
                u=core.user_for_token(c.token)
                au=core.connect().execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone()
                ch=core.get_channel(cid)
                if au and ch: line(c,':%s!%s@huddle PRIVMSG %s :%s'%(au['username'],au['username'],channel_name(ch),m.get('body','')))
        await asyncio.sleep(.05)
async def handle(reader,writer):
    class C:pass
    c=C();c.reader=reader;c.writer=writer;c.token=None;c.user=None;c.nick=None;c.registered=False;c.closed=False;c.joined=set();c.last_events={};c.own_events={}
    connections.add(c);task=asyncio.create_task(deliver(c))
    try:
        while not reader.at_eof():
            raw=await reader.readline()
            if not raw:break
            text=raw.decode(errors='replace').strip('\r\n')
            if not text:continue
            parts=text.split(' ',1);cmd=parts[0].upper();arg=parts[1] if len(parts)>1 else ''
            if cmd=='PASS':c.token=arg.strip()
            elif cmd=='NICK':
                nick=arg.strip().split()[0] if arg.strip() else ''
                if nick in nicks and nick!=c.nick:line(c,':huddle 433 %s %s :Nickname is already in use'%(c.nick or '*',nick));continue
                if c.nick:nicks.discard(c.nick)
                c.nick=nick;nicks.add(nick)
            elif cmd=='USER': pass
            elif cmd=='PING':line(c,':huddle PONG huddle '+arg.lstrip(':'))
            elif cmd=='QUIT':break
            if not c.registered and c.nick and c.token:
                c.user=core.user_for_token(c.token)
                if c.user:
                    c.registered=True
                    for code,msg in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created now'),('004','huddle'),('005','CASEMAPPING=rfc1459')]:
                        line(c,':huddle %s %s :%s'%(code,c.nick,msg))
                    line(c,':huddle 422 %s :MOTD File is missing'%(c.nick))
                elif cmd in ('NICK','USER'):line(c,':huddle 464 %s :Password incorrect'%(c.nick or '*'));break
            if not c.registered:continue
            if cmd=='JOIN':
                name=arg.split()[0] if arg else '';ch=find_channel(name)
                if not ch or not core.can_read_channel(c.user['id'],ch):
                    line(c,':huddle 403 %s %s :No such channel'%(c.nick,name));continue
                try: core.join_channel(c.user,ch['id']) if hasattr(core,'join_channel') else None
                except: pass
                c.joined.add(ch['id']);c.last_events[ch['id']]=core.head_event_id(ch['id'])
                line(c,':%s!%s@huddle JOIN %s'%(c.nick,c.user['username'],name))
                cc=core.connect();rs=cc.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall();cc.close()
                line(c,':huddle 353 %s = %s :%s'%(c.nick,name,' '.join(x['username'] for x in rs)))
                line(c,':huddle 366 %s %s :End of /NAMES list'%(c.nick,name))
            elif cmd=='PRIVMSG':
                if ' :' not in arg:continue
                target,body=arg.split(' :',1);ch=find_channel(target.strip())
                if not ch or ch['id'] not in c.joined:line(c,':huddle 403 %s %s :No such channel'%(c.nick,target));continue
                try:
                    m=core.create_message(c.user['id'],ch['id'],body)
                    c.own_events[ch['id']]=core.head_event_id(ch['id'])
                except: line(c,':huddle 403 %s %s :No such channel'%(c.nick,target))
            elif cmd=='FOOBAR' or cmd not in ('JOIN','PRIVMSG','PONG','NAMES','WHO','LIST','TOPIC','MODE'):
                line(c,':huddle 421 %s %s :Unknown command'%(c.nick,cmd))
    finally:
        c.closed=True;connections.discard(c)
        if c.nick:nicks.discard(c.nick)
        task.cancel()
        writer.close()
        try:await writer.wait_closed()
        except:pass
async def main():
    core.init_db()
    srv=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with srv:await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
