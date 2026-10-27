# Component B: channels and message history

Rule groups **B0** to **B6**, 64 rules. Check: `python3 /spec/channels/checks/check_channels.py`
(append group ids, for example `B2 B4`, to run only those). The rule numbers below match its output.

Component B builds on Component A: the B checks create users, workspaces and channels and join
them through the A routes, so a broken A route shows up here as `setup: ...`. Read
`/spec/common/architecture.md` before writing code. `emit_event` (architecture rule 3) must be called
by every write listed in B4.4 from the moment that write exists.

**Access rule (all of Component B).** A user can read and write a channel if they are a member of
it, or if the channel is public and they are a workspace member whose role is not `guest`. Private
channels are for their members only.

<!-- Everything below this line is generated from staged/channels/channels-*.md by student-files/spec_tool.py build. Edit the staged files, not this part. -->

### B0. Baseline and conventions

B0.1 **Health.** `GET /api/health` on port `8000` returns `200` and `{"status": "ok", "node_id": 0}`.

B0.2 **Node ids.** `GET /api/health` on ports `8000`, `8001`, `8002` returns `node_id` `0`, `1`,
`2` respectively.

B0.3 **WebSocket rejects a bad token.** A WebSocket handshake to `/api/ws?token=<made-up token>`
gets `401` and is never upgraded (no `101`).

B0.4 **Redis runs.** `127.0.0.1:6379` answers `PING` with `+PONG`; `start.sh` restarts it if it dies.

B0.5 **Boot files.** `/app/start.sh` exists and is executable; `/app/requirements.txt` exists.

B0.6 **Crash tolerance.** After `SIGKILL` of the process listening on `8001`, ports `8000` and
`8002` keep answering `GET /api/health` with `200`, and `8001` answers again within 15 seconds.
The check runs this rule last.

B0.7 **401 comes first.** Each of these returns `401` with no `Authorization` header, and with a
made-up token, before anything checks whether the id or slug exists:
`GET /api/workspaces/{slug}`, `POST /api/workspaces/{slug}/channels`, `PATCH /api/channels/{id}`,
`POST /api/channels/{id}/leave`, `GET /api/channels/{id}/members`,
`POST /api/channels/{id}/archive`, `GET` and `POST /api/channels/{id}/messages`,
`GET /api/messages/{id}/replies`, `GET /api/search`, `GET /api/channels/{id}/pins`,
`POST /api/messages/{id}/pin`, `GET` and `POST /api/channels/{id}/read`.

B0.8 **404 for unknown ids.** With a valid token, an unknown channel id returns `404` on
`GET`/`POST /api/channels/{id}/messages`, `GET /api/channels/{id}/members`,
`PATCH /api/channels/{id}` and `GET /api/channels/{id}/read`; an unknown message id returns `404`
on `GET /api/messages/{id}/replies` and `POST /api/messages/{id}/pin`.

### B1. Channel management

B1.1 **ChannelObj.** `POST /api/workspaces/{slug}/channels` with `{name, is_private?, topic?}`
returns `201` and `{channel}` where `channel` has every field `id`, `workspace_id`, `name`,
`is_private`, `is_dm` (always `false` here), `topic`, `is_archived` (`false`). `topic` defaults to
`""`. Any other body field, such as `description`, is ignored and never fills `topic`.

B1.2 **Topic length.** `topic` is a string of at most 250 characters. 250 is accepted; 251 returns
`400`, both on create and on `PATCH /api/channels/{id}`.

B1.3 **Name rules.** `name` must match `^[a-z0-9-]{1,32}$`: `"bad name"`, `"UPPER"`, a
33-character name and `""` each return `400`. A name already used in the same workspace returns
`409` (another workspace may reuse it).

B1.4 **Workspace detail.** `GET /api/workspaces/{slug}` returns `200` and
`{workspace, channels, read_state}`. `channels` lists every non-archived channel of the workspace
as `ChannelObj`, except private channels the caller is not a member of. A caller who is not a
workspace member gets `403`; an unknown slug gets `404`. (`read_state` is defined in B5.7.)

B1.5 **Archived channels.** Archived channels are left out of `channels` unless the query has
`include_archived=1` or `include_archived=true`.

B1.6 **Topic updates.** `PATCH /api/channels/{id}` with `{topic}` returns `200` and `{channel}`
with the new topic when the caller is a member of the channel or the workspace owner. Anyone else
gets `403`. It emits `channel.updated`.

B1.7 **Channel members.** `GET /api/channels/{id}/members` returns `200` and
`{members: [UserObj, ...]}` for every member of the channel. The caller must pass the access rule
(else `403`).

B1.8 **Leave.** `POST /api/channels/{id}/leave` returns `200` and `{channel}`, and the caller is no
longer listed in the channel's members. Leaving a channel you are not in also returns `200` and
changes nothing. It emits `member.left` only when a member was removed.

B1.9 **New workspace members join #general.** Whenever a user becomes a workspace member (creating
the workspace, open join of any public channel, or accepting an invitation) they are also added to
`#general`.

B1.10 **Archive.** `POST /api/channels/{id}/archive` and `POST /api/channels/{id}/unarchive` are for
the workspace owner only (anyone else, admins included, gets `403`). Each returns `200` and
`{channel}` with `is_archived` `true` or `false`; archiving an archived channel (or unarchiving an
active one) returns `200` and emits nothing. While a channel is archived, posting a message or
reply, editing a message, adding or removing a reaction, and `PATCH /api/channels/{id}` each return
`423`. Unarchiving restores write access.

### B2. Messages and paging

B2.1 **Post.** `POST /api/channels/{id}/messages` with `{body, parent_id?}` returns `201` and
`{message}` with every `MessageObj` field: `id`, `channel_id`, `author_id`, `author` (`UserObj`),
`body`, `parent_id` (`null` for a top-level message), `reply_count` (`0`), `created_at` (UTC,
ending in `Z`), `edited_at` (`null`), `files` (`[]`), `reactions` (`[]`), `mentions` (list of user
ids, `[]` when none). Do not put `event_id` in message objects. `file_ids` and `blocks` may be
sent; ignore them.

B2.2 **Empty body.** A `body` that is empty, only whitespace, or missing returns `400`.

B2.3 **Who may post.** Use the access rule from B.1: a non-guest workspace member may post in a
public channel without joining it (`201`); a user outside the workspace gets `403`; a non-member of
a private channel gets `403` on both `POST` and `GET /api/channels/{id}/messages`.

B2.4 **History.** `GET /api/channels/{id}/messages?limit=&cursor=` returns `200` and
`{messages, next_cursor}`: top-level, non-deleted messages, newest first. `next_cursor` is an
opaque string when more messages exist and `null` on the last page; the key is always present.

B2.5 **Cursors work on every node.** A cursor from one node returns the same page on the other two.

B2.6 **Cursors are stable.** New messages posted after a cursor was issued do not change the page
that cursor returns.

B2.7 **Default limit.** Without `limit`, a page holds 50 messages.

B2.8 **Limit range.** `limit` must be an integer from 1 to 200. `0`, `201`, `-1` and `abc` return
`400`; `200` is accepted.

B2.9 **Bad cursors.** A cursor that was not issued by this list for this endpoint returns `400`,
for example `not-a-cursor`, `AAAA`, or base64 of some other JSON object.

B2.10 **No offset paging.** A request with an `offset` or `page` query parameter returns `400`.

B2.11 **Threads.** A post with `parent_id` must name a top-level, non-deleted message in the same
channel (else `400`); it returns `201` with that `parent_id`. `GET /api/messages/{id}/replies`
returns `200` and `{replies, next_cursor}`, oldest first, with the same limit and cursor rules.
The parent's `reply_count` is its number of non-deleted replies. Replies never appear in
`GET /api/channels/{id}/messages`. An unknown parent id returns `404`.

B2.12 **Edit.** `PATCH /api/messages/{id}` with `{body}` returns `200` and `{message}` with the new
body and `edited_at` set (UTC, ending in `Z`). Only the author may edit (`403`). Editing a deleted
message returns `409`; an empty body returns `400`.

B2.13 **Delete.** `DELETE /api/messages/{id}` by the author or the workspace owner returns `200` and
`{"deleted": true}`; deleting it again also returns `200`. Anyone else gets `403`. A deleted
message disappears from history, replies and search, and stops counting as a reply.

B2.14 **Reactions.** `POST /api/messages/{id}/reactions` with `{emoji}` returns `200` (not `201`,
even the first time) and `{reactions: [{emoji, count, user_ids}, ...]}`. The same user adding the
same emoji again changes nothing. `DELETE /api/messages/{id}/reactions/{emoji}` (URL-encoded emoji)
removes only the caller's reaction and returns `200` and `{reactions}`; an emoji with no users left
is dropped from the list. List emojis in the order they were first added and `user_ids` in the
order users reacted. An empty emoji returns `400`.

### B3. Pins and search

B3.1 **Pin.** `POST /api/messages/{id}/pin` returns `200` and
`{pin: {message_id, pinned_by, pinned_at}}` (`pinned_at` UTC, ending in `Z`). Pinning an already
pinned message returns `200` and the original pin unchanged (same `pinned_by` and `pinned_at`).
The caller must pass the access rule.

B3.2 **Pin list.** `GET /api/channels/{id}/pins?limit=&cursor=` returns `200` and
`{pins: [{message, pinned_by, pinned_at}, ...], next_cursor}` with `message` a `MessageObj`, most
recently pinned first. Same limit and cursor rules as B2.7 to B2.10 (a bad cursor returns `400`).

B3.3 **Unpin.** `DELETE /api/messages/{id}/pin` returns `200` and `{"unpinned": true}` (also when
the message was not pinned), and the message leaves the pin list.

B3.4 **Private pins.** A user who is not a member of a private channel gets `403` on its pin list.

B3.5 **Search.** `GET /api/search?q=<text>&limit=&cursor=` returns `200` and
`{results: [MessageObj, ...], next_cursor}`: non-deleted messages (top-level and replies) whose
body contains `q`, case-insensitively, newest first, only from channels the caller can read under
the access rule. A user with access to nothing gets `results: []`.

B3.6 **Workspace filter.** `workspace_id=<id>` limits the results to that workspace.

B3.7 **Search paging and validation.** Search pages with the same limit and cursor rules as B2.
A missing or empty `q` returns `400`; `limit=0` returns `400`.

### B4. WebSocket events

B4.1 **Subscribe.** `ws://127.0.0.1:800N/api/ws?token=<token>` upgrades (`101`) on every node.
After the client sends `{"type": "subscribe", "channel_id": <id>}`, the server immediately replies
`{"type": "subscribed", "channel_id": <id>, "head_event_id": <int>}`, where `head_event_id` is the
channel's newest `event_id` (`0` if none) and is the same on every node. A caller who fails the
access rule gets `{"type": "error", ...}` and no subscription. Subscribing again to the same
channel only sends another `subscribed` frame; it must not duplicate later frames.

B4.2 **Live delivery across nodes.** After `subscribed`, every durable event of that channel,
committed on any node, arrives within 1 second as one JSON frame
`{"type": <kind>, "event_id": <int>, "channel_id": <id>, ...payload}`. For a post on `8001`,
subscribers on `8000` and `8002` receive `message.created` with `event_id` = head + 1 and
`message.id` equal to the new message.

B4.3 **Dense ordering.** Every committed durable event advances its channel's `event_id` by exactly
1: no gaps, no duplicates, frames in `event_id` order, and the same sequence on all three nodes
even when consecutive writes go to different nodes.

B4.4 **Event kinds.** Each of these writes commits exactly one event, in the same transaction:

| Write | `type` | Payload keys |
|---|---|---|
| post without `parent_id` | `message.created` | `message` (MessageObj) |
| post with `parent_id` | `message.reply` | `message` |
| `PATCH /api/messages/{id}` | `message.updated` | `message` |
| `DELETE /api/messages/{id}` (first time) | `message.deleted` | `message_id` |
| add a reaction (new) | `reaction.added` | `message_id`, `emoji`, `user_id` |
| remove a reaction (existing) | `reaction.removed` | `message_id`, `emoji`, `user_id` |
| a user is added to the channel (join, creator, `#general` auto-join) | `member.joined` | `user_id` |
| a member leaves | `member.left` | `user_id` |
| `PATCH /api/channels/{id}` | `channel.updated` | `channel` (ChannelObj) |
| archive (was active) | `channel.archived` | `channel` |
| unarchive (was archived) | `channel.unarchived` | `channel` |

B4.5 **No-ops emit nothing.** A repeated reaction, a join by an existing member, a leave by a
non-member, a second delete, archiving an archived channel and unarchiving an active one commit
no event, so the next real event is exactly +1.

B4.6 **Resume.** On any node, `{"type": "resume", "channel_id": <id>, "since_event_id": <n>}` makes
the server send every event with `event_id > n` in order, then
`{"type": "resumed", "channel_id": <id>, "head_event_id": <last event_id sent, or n>}`, and then
keep delivering new events live, as after `subscribe`. Resuming from the current head sends only
`resumed`. Keep every event; if `since_event_id` is ever older than the oldest kept event minus 1,
send `{"type": "resume.gap", "channel_id": <id>, "earliest_event_id": <int>}` first and replay
from there.

B4.7 **Redis outage.** With `redis-server` killed, a post on `8001` still reaches a subscriber on
`8000` within 5 seconds with the next `event_id`, and `start.sh` has Redis listening again within
10 seconds.

B4.8 **Concurrent writes.** 60 posts sent at the same time (six parallel senders, two per node)
all return `201`, and a subscriber receives exactly 60 `message.created` frames whose `event_id`s
run from head + 1 to head + 60 with no gaps, duplicates or reordering. Allocate the `event_id`
inside the write transaction (architecture rule 3), never as `MAX(...) + 1` outside it.

B4.9 **Replay after a node dies.** With the process on `8001` killed, posts through `8000` and
`8002` still return `201`. A new WebSocket on `8002` that resumes from the head it saw before the
kill gets exactly those events, then `resumed`. Once `8001` is back (within 15 seconds), a post
through `8001` reaches that socket live with the next `event_id`.

### B5. Mentions and read state

B5.1 **Resolve @username.** Find mentions with the regex `(?<![\w@.])@(\w+)`. A name that is the
username of a member of the channel's workspace adds that user's id to `mentions`. Punctuation
around the mention is fine: `(@bob),` mentions bob.

B5.2 **What does not count.** `mentions` is deduplicated (first appearance order) and never holds
the author. Email-shaped text (`bob@example.com`), in-word `@` (`hey@bob`), users who are not in
the channel's workspace, and unknown names add nothing.

B5.3 **@channel.** `@channel` adds every member of the channel except the author, merged and
deduplicated with explicit mentions.

B5.4 **Read state.** `GET /api/channels/{id}/read` returns `200` and
`{read_state: {channel_id, last_read_event_id, unread_count, mention_count}}`. `last_read_event_id`
starts at `0`. `unread_count` counts non-deleted messages (top-level and replies) in the channel
that the caller did not write and whose `event_id` is greater than `last_read_event_id`.
`mention_count` counts those of them whose `mentions` contain the caller.

B5.5 **Mark read.** `POST /api/channels/{id}/read` with `{last_read_event_id}` stores the larger of
the old and new values (the cursor never moves back) and returns `200` and the recomputed
`{read_state}`.

B5.6 **Own messages never count.** The reader's own messages, including ones that mention
themself, never add to their `unread_count` or `mention_count`.

B5.7 **Read state in workspace detail.** In `GET /api/workspaces/{slug}`, `read_state` is a list
holding one read-state object (same shape and values as B5.4) for each channel in `channels`.

B5.8 **Validation and access.** A `last_read_event_id` that is not a non-negative integer (for
example `"abc"`) returns `400`. A caller who fails the access rule gets `403` on `GET` and `POST`;
an unknown channel id returns `404`.

B5.9 **Edits recompute mentions.** `PATCH /api/messages/{id}` resolves mentions again from the new
body and stores them: adding `@bob` puts bob's id in `mentions` and adds 1 to bob's
`mention_count` (while the message is unread); removing it empties `mentions` and the count drops
back.

B5.10 **Bad input is a 400, never a 500.** Every Spec B route checks the JSON type of each field it
reads before using it. A wrong type (for example a number for `topic`, `name` or `body`, `null` for
`topic` or `body`, a string `parent_id`, a number for `emoji`, `null` or `true` for
`last_read_event_id`) returns `400`, and so does a body that is not valid JSON. No route answers
`500` to bad input.

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

Every request must answer within 5 seconds.
