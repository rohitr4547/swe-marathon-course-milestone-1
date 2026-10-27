# B.2: Messages, threads and paging

Rule group: **B2**.
Check: `python3 /spec/channels/checks/check_channels_2.py`. The rule numbers below match its output.

Do not start this step until `check_channels_1.py` prints `RESULT: 18 passed, 0 failed`.

## Uses

Call these from earlier steps. Do not write a second version of any of them.

| Function | Used here for |
|---|---|
| `get_channel(id)`, `can_read_channel(user_id, channel)` | `404` and `403` on every route here |
| `require_writable(channel)` | `423` on post, edit and reactions |
| `emit_event(c, channel_id, kind, payload)` | One event per message write (kinds in B4.4); store the returned `event_id` on the message row |
| `member_role(...)` | The workspace owner may delete any message |
| `user_to_json(row)`, `now_iso()`, `tx()` | `author`, timestamps, transactions |

## Adds

| Function | What it does |
|---|---|
| `parse_paging(query, kind)` | Rules B2.7 to B2.10; returns `(limit, decoded cursor or None)` |
| `encode_cursor(kind, v)`, `decode_cursor(kind, s)` | Architecture rule 5 |
| `create_message(user, channel_id, body)` | B2.1 to B2.3, B2.11; stores `mentions` as `[]` until B.5 |
| `message_to_json(row)` | The `MessageObj` |
| `list_messages(...)`, `list_replies(...)` | B2.4 to B2.11 |
| `edit_message(...)`, `delete_message(...)` | B2.12, B2.13 |
| `react(user, message_id, emoji, add)`, `reactions_for(message_id)` | B2.14 |

## Rules

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

Every request must answer within 5 seconds.
