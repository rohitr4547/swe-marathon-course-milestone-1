# B.6: IRC bridge (do this last)

Rule group: **B6**.
Check: `python3 /spec/channels/checks/check_channels_6.py`. The rule numbers below match its output.

Do not start this step until `check_channels_5.py` prints `RESULT: 10 passed, 0 failed` and every
earlier check still passes. IRC writes join the same per-channel `event_id` sequence as web writes.

## Uses

Call these from `core.py`. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `user_for_token(token)` | `PASS <token>` |
| `join_channel(user, channel_id)` | `JOIN` follows the same rules as the web join |
| `create_message(user, channel_id, body)` | `PRIVMSG` commits a normal message (same event, same validation) |
| `head_event_id(...)`, `events_after(...)` | Delivering web messages to IRC by polling, like the WebSocket |

## Adds

| Function | What it does |
|---|---|
| `irc.py: handle(reader, writer)` | One asyncio connection: registration, commands, numerics |
| `irc.py: deliver(conn)` | Every 0.05 s, sends new `message.created` / `message.reply` events of joined channels |

The server prefix is `huddle`. Every reply line ends with `\r\n`. A numeric reply looks like
`:huddle <code> <nick> <params> :<text>`.

## Rules

### B6. IRC gateway

B6.1 **Bad password.** The gateway listens on `0.0.0.0:6667`. The client sends `PASS <token>`,
`NICK <nick>`, `USER <user> 0 * :<real name>`. Once both `NICK` and `USER` have arrived, a missing
or unknown token gets `464` and the connection is closed.

B6.2 **Registration.** With a valid token, the server sends numerics `001`, `002`, `003`, `004`,
`005`, then `422` (no MOTD).

B6.3 **Join.** `JOIN #<workspace-slug>/<channel-name>` (a literal `/`) runs the web join rules and
replies `:<nick>!<user>@huddle JOIN #slug/name`, then `353 <nick> = #slug/name :<usernames>`, then
`366 <nick> #slug/name :End of /NAMES list`. A hyphenated name such as `#slug-general`, an unknown
channel, or a join the rules forbid gets `403 <nick> <name> :No such channel`.

B6.4 **IRC to web.** `PRIVMSG #slug/name :<text>` on a joined channel creates a message by the
token's user through `create_message`: it shows in `GET /api/channels/{id}/messages` and reaches
WebSocket subscribers as `message.created` with the next `event_id`.

B6.5 **Web to IRC.** A message posted on the web (or by another IRC client) to a joined channel
reaches the IRC client as `:<author-username>!<author-username>@huddle PRIVMSG #slug/name :<body>`.
Do not echo a message back to the connection that sent it. Web and IRC writes share one dense
`event_id` sequence.

B6.6 **Other commands.** `PING :<token>` gets `:huddle PONG huddle :<token>`. An unknown command
gets `421 <nick> <command> :Unknown command`. A `NICK` already used by another connection gets
`433 * <nick> :Nickname is already in use` and the connection stays open, so the client can send
another `NICK`. `QUIT` closes the connection without an error line. `PONG`, `NAMES`, `WHO`, `LIST`,
`TOPIC` and `MODE` are accepted (minimal or no reply is fine).
