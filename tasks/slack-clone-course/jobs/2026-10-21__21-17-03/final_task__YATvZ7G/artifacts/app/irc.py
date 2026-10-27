import asyncio, json, re
import core

connections=[]
nicks={}
irc_created=set()

def line(w,s):
    w.write((s+'\r\n').encode())

async def deliver(conn):
    while not conn['closed']:
        await asyncio.sleep(.05)
        if not conn['registered'] or not conn['joined']: continue
        c=core.connect()
        for cid in list(conn['joined']):
            rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,conn['seen'].get(cid,0))).fetchall()
            for row in rows:
                conn['seen'][cid]=row['event_id']
                try: f=json.loads(row['payload'])
                except: continue
                if f.get('type') not in ('message.created','message.reply'): continue
                m=f.get('message') or {}
                if m.get('id') in irc_created: continue
                ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                u=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone()
                if ch and u:
                    w=conn['writer']; target='#'+c.execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()['slug']+'/'+ch['name']
                    line(w,f":{u['username']}!{u['username']}@huddle PRIVMSG {target} :{m.get('body','')}")
        c.close()

async def register(conn):
    if conn['registered']: return
    if not conn['token'] or not core.user_for_token(conn['token']):
        line(conn['writer'],f":huddle 464 {conn.get('nick','*')} :Password incorrect")
        conn['closed']=True; conn['writer'].close(); return
    u=core.user_for_token(conn['token'])
    nick=conn.get('nick')
    if not nick or nick in nicks and nicks[nick] is not conn:
        line(conn['writer'],f":huddle 433 * {nick or '*'} :Nickname is already in use"); return
    conn['user']=u; conn['registered']=True; nicks[nick]=conn
    for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1'),('005','CHANTYPES=# PREFIX=(ov)@+'),('422','MOTD File is missing')]:
        line(conn['writer'],f":huddle {code} {nick} :{text}")

async def handle(reader,writer):
    conn={'reader':reader,'writer':writer,'token':None,'nick':None,'registered':False,'closed':False,'joined':set(),'seen':{}}
    connections.append(conn); task=asyncio.create_task(deliver(conn))
    try:
        while not reader.at_eof() and not conn['closed']:
            raw=await reader.readline()
            if not raw: break
            s=raw.decode(errors='ignore').strip('\r\n')
            if not s: continue
            parts=s.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
            if cmd=='PASS':
                conn['token']=rest[0] if rest else None; await register(conn)
            elif cmd=='NICK':
                nick=rest[0] if rest else ''
                if nick in nicks and nicks[nick] is not conn:
                    line(writer,f":huddle 433 * {nick} :Nickname is already in use")
                else:
                    if conn['nick'] in nicks: del nicks[conn['nick']]
                    conn['nick']=nick; await register(conn)
            elif cmd=='USER':
                conn['usercmd']=True; await register(conn)
            elif cmd=='PING':
                tok=parts[2] if len(parts)>2 else (rest[0] if rest else '')
                tok=tok[1:] if tok.startswith(':') else tok
                line(writer,f":huddle PONG huddle :{tok}")
            elif cmd=='JOIN':
                if not conn['registered']: continue
                target=rest[0] if rest else ''
                if not target.startswith('#') or '/' not in target[1:]:
                    line(writer,f":huddle 403 {conn['nick']} {target} :No such channel"); continue
                slug,name=target[1:].split('/',1); w=core.get_workspace(slug)
                ch=None
                if w:
                    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone(); c.close()
                if not ch or ch['is_archived'] or not core.can_read_channel(conn['user']['id'],ch):
                    line(writer,f":huddle 403 {conn['nick']} {target} :No such channel"); continue
                with core.tx() as c:
                    core.add_channel_member(c,ch['id'],conn['user']['id'])
                conn['joined'].add(ch['id']); c=core.connect(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch['id'],)).fetchone()[0]; c.close(); conn['seen'][ch['id']]=head
                line(writer,f":{conn['nick']}!{conn['user']['username']}@huddle JOIN {target}")
                c=core.connect(); names=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
                line(writer,f":huddle 353 {conn['nick']} = {target} :"+' '.join(x[0] for x in names))
                line(writer,f":huddle 366 {conn['nick']} {target} :End of /NAMES list")
            elif cmd=='PRIVMSG':
                target=rest[0] if rest else ''; text=parts[2] if len(parts)>2 else ''; text=text[1:] if text.startswith(':') else text
                if target.startswith('#') and '/' in target[1:]:
                    slug,name=target[1:].split('/',1); w=core.get_workspace(slug)
                    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone() if w else None;c.close()
                    if ch and ch['id'] in conn['joined']:
                        m=core.create_message(conn['user']['id'],ch['id'],text); irc_created.add(m['id'])
            elif cmd=='QUIT':
                break
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
                pass
            else:
                line(writer,f":huddle 421 {conn.get('nick','*')} {cmd} :Unknown command")
    finally:
        conn['closed']=True
        if conn in connections: connections.remove(conn)
        if conn.get('nick') in nicks and nicks[conn['nick']] is conn: del nicks[conn['nick']]
        task.cancel()
        try: writer.close(); await writer.wait_closed()
        except: pass

async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
