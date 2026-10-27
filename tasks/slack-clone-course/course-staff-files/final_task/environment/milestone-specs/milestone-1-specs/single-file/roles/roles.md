# Component A: roles and invitations

Rule groups **A0** to **A4**, 48 rules. Check: `python3 /spec/roles/checks/check_roles.py`
(append group ids, for example `A2 A3`, to run only those). The rule numbers below match its output.

Read `/spec/common/architecture.md` before writing code: it gives the design, `start.sh`, and the
function names to use. A0 and A1 come first because every later rule sets up its data with them.

<!-- Everything below this line is generated from staged/roles/roles-*.md by student-files/spec_tool.py build. Edit the staged files, not this part. -->

### A0. Baseline and conventions

A0.1 **Health.** `GET /api/health` on port `8000` returns `200` and `{"status": "ok", "node_id": 0}`.

A0.2 **Node ids.** `GET /api/health` on ports `8000`, `8001`, `8002` returns `node_id` `0`, `1`,
`2` respectively.

A0.3 **WebSocket rejects a bad token.** A WebSocket handshake to `/api/ws?token=<made-up token>`
gets `401` and is never upgraded (no `101`). Look the token up before calling `prepare()`.

A0.4 **Redis runs.** `127.0.0.1:6379` answers `PING` with `+PONG`. `start.sh` keeps
`redis-server` running and restarts it if it dies.

A0.5 **Boot files.** `/app/start.sh` exists and is executable. `/app/requirements.txt` exists and
lists every non-standard-library package you import.

A0.6 **Crash tolerance.** After `SIGKILL` of the process listening on `8001`, ports `8000` and
`8002` keep answering `GET /api/health` with `200`, and `8001` answers again within 15 seconds
with no outside help (`start.sh` respawns it). The check runs this rule last.

A0.7 **401 comes first.** Each of these returns `401` with no `Authorization` header, and also
with a made-up token, before anything checks whether the slug, id or code exists:
`GET /api/auth/me`, `GET /api/workspaces`, `POST /api/workspaces`,
`GET /api/workspaces/{slug}/members`, `PATCH /api/workspaces/{slug}`,
`PATCH /api/workspaces/{slug}/members/{user_id}`, `POST /api/workspaces/{slug}/transfer_ownership`,
`POST` and `GET /api/workspaces/{slug}/invitations`, `POST /api/invitations/{code}/accept`,
`POST /api/channels/{id}/join`.

A0.8 **JSON everywhere.** Every response, errors included, is a JSON object with
`Content-Type: application/json`. Errors look like `{"error": "<message>"}`.

### A1. Auth

A1.1 **Register.** `POST /api/auth/register` with `{username, password, display_name?}` returns
`201` and `{user, token}`. `token` is a string of at least 16 characters. `user` has every
`UserObj` field: `id`, `username`, `display_name`, `timezone`, `avatar_url`, `status_text`,
`status_emoji`. `display_name` is the value sent, or the username when none is sent; `timezone`
defaults to `"UTC"`; `avatar_url`, `status_text` and `status_emoji` default to `""`. `user` never
contains a password or hash field. The endpoint takes no email field.

A1.2 **Duplicate username.** Registering a username that already exists returns `409`.

A1.3 **Invalid username.** `username` must match `^[A-Za-z0-9_]+$`. `"bad name"`, `"bad-name"`,
`"bad!name"`, `""` or a missing username returns `400`.

A1.4 **Password length.** `password` must be a string of at least 8 characters. A 7-character or
missing password returns `400`; exactly 8 characters is accepted (`201`).

A1.5 **Login.** `POST /api/auth/login` with `{username, password}` and the right password returns
`200` and `{user, token}` with a new token of at least 16 characters.

A1.6 **Bad login.** A wrong password, or a username that does not exist, returns `401` (not `400`,
not `404`).

A1.7 **Me.** `GET /api/auth/me` with `Authorization: Bearer <token>` returns `200` and `{user}` for
the token's owner.

A1.8 **Me without a valid token.** `GET /api/auth/me` with no header, with a made-up token, or with
a scheme other than `Bearer` (for example `Authorization: Token <token>`) returns `401`.

A1.9 **Every node.** A user registered on `8000` can log in on `8002`, and every token works on
`8000`, `8001` and `8002`.

### A2. Workspaces and joining

A2.1 **Create a workspace.** `POST /api/workspaces` with `{slug, name}` returns `201` and
`{workspace, general_channel}`. `workspace` is `{id, slug, name, owner_id, join_mode}` with
`owner_id` = the caller's id and `join_mode` = `"open"`. `general_channel` is a `ChannelObj` named
`"general"` with `is_private: false` and `workspace_id` = `workspace.id`. The caller becomes a
workspace member with role `owner` and a member of `#general`.

A2.2 **Duplicate slug.** A slug that is already taken returns `409`, whoever sends it.

A2.3 **Validation.** `slug` must match `^[a-z0-9-]{2,32}$`: `"Bad-Slug"`, `"a"`, a 33-character
slug, `"has space"` and `"under_score"` each return `400`. `name` must be a non-empty string; a
missing name returns `400`.

A2.4 **List workspaces.** `GET /api/workspaces` returns `200` and `{workspaces: [WorkspaceObj,
...]}` containing only workspaces where the caller is a member (any role, owner included).

A2.5 **List members.** `GET /api/workspaces/{slug}/members` returns `200` and
`{members: [{user_id, role, user}, ...]}`: one entry per workspace member, where `user` is a
`UserObj`. The creator has role `owner`. A caller who is not a member gets `403`; an unknown slug
gets `404`.

A2.6 **Create a channel.** `POST /api/workspaces/{slug}/channels` with `{name, is_private?}` by the
owner returns `201` and `{channel}`. `is_private` defaults to `false`. The creator becomes a member
of the channel. (Name and topic validation are rules B1.1 to B1.3; role limits are A3.8 and A3.9.)

A2.7 **Open join.** In a workspace whose `join_mode` is `"open"`, `POST /api/channels/{id}/join` on
a *public* channel by a user who is not a workspace member returns `200` and `{channel}`. The
caller becomes a workspace member with role `member` (which also adds them to `#general`), joins
the channel, and now sees the workspace in `GET /api/workspaces`.

A2.8 **Join is idempotent.** Joining a channel you are already in returns `200` and changes
nothing: no duplicate member entry, same role.

A2.9 **Join errors.** An unknown channel id returns `404`. A private channel returns `403` for
anyone who is not already a member of it (there is no channel-invite flow in this run); an
existing member gets `200`.

### A3. Roles

Roles are per workspace: `owner`, `admin`, `member`, `guest`. Exactly one member is the owner.

A3.1 **Promote.** `PATCH /api/workspaces/{slug}/members/{user_id}` with `{role}` returns `200` and
`{member: {user_id, role}}`. The owner may set `admin`, `member` or `guest` on any member who is
not the owner. `GET /api/workspaces/{slug}/members` then shows the new role.

A3.2 **Admins manage non-admins.** An admin may set `member`, `guest` or `admin` on a member or a
guest.

A3.3 **Admins cannot touch admins or the owner.** An admin targeting another admin, or the owner,
gets `403` and nothing changes.

A3.4 **Bad role.** `role: "owner"` returns `400` (ownership moves only through
`transfer_ownership`). A role that is not `admin`, `member` or `guest`, or a missing role, also
returns `400`.

A3.5 **The owner's role is fixed.** Any PATCH whose target is the current owner returns `403`,
including the owner targeting themself.

A3.6 **Only owners and admins.** A caller whose role is `member` or `guest` (or who is not in the
workspace) gets `403`. Check order: `401` → unknown slug `404` → caller not owner/admin `403` →
bad role `400` → target not a member `404` → target is the owner, or an admin targeting another
admin `403`.

A3.7 **Unknown target.** A `user_id` that is not a member of the workspace returns `404`.

A3.8 **Guests are limited.** A `guest` gets `403` on `POST /api/channels/{id}/join` for a public
channel, and `403` on `POST /api/workspaces/{slug}/channels`.

A3.9 **Who creates which channel.** A `member` creating a public channel gets `201`; a `member`
sending `{is_private: true}` gets `403`. An `admin` or the `owner` may create private channels
(`201`).

A3.10 **Transfer errors.** `POST /api/workspaces/{slug}/transfer_ownership` with `{new_owner_id}`
from anyone but the owner (admins included) returns `403`. If the target is not currently an
`admin` (a member, a guest, a non-member, or the owner themself) it returns `400` and nothing
changes.

A3.11 **Transfer.** When the owner transfers to an admin, the response is `200` and
`{workspace}`. The target's role becomes `owner`, `workspace.owner_id` becomes the target's id
(also in `GET /api/workspaces`), and the previous owner's role becomes `admin`. The previous owner
now gets `403` on `transfer_ownership`.

### A4. Invitations and join mode

A4.1 **Create.** `POST /api/workspaces/{slug}/invitations` by an owner or admin, with body
`{expires_in_seconds?, max_uses?, invited_username?, invited_user_id?}`, returns `201` and
`{invitation}` holding a non-empty URL-safe `code`. `max_uses` defaults to `1`. A non-positive or
non-integer `expires_in_seconds` or `max_uses` returns `400`; an `invited_username` or
`invited_user_id` that matches no user returns `404`.

A4.2 **Members cannot manage invitations.** A `member` or `guest` gets `403` creating, listing or
revoking invitations.

A4.3 **List.** `GET /api/workspaces/{slug}/invitations` by an owner or admin returns `200` and
`{invitations: [...]}` with every *active* invitation of the workspace. Admins may create them
too (`201`).

A4.4 **Accept.** `POST /api/invitations/{code}/accept` returns `200` and `{workspace}`, consumes
one use, and adds the caller as a workspace `member` (which also joins `#general`). A caller who
is already a member gets `200` without consuming a use. Once all uses are consumed the code returns
`404` on accept and is no longer listed.

A4.5 **Several uses.** With `max_uses: 2`, exactly two different users get `200`; the third gets
`404`.

A4.6 **Targeted invitations.** With `invited_user_id` or `invited_username`, the invitation is
single-use: the response shows `max_uses: 1` whatever the body said. Any other user accepting it
gets `403` (no use consumed); the target gets `200`.

A4.7 **Expiry.** An invitation created with `expires_in_seconds: 1` returns `404` on accept once
that second has passed, and is no longer listed.

A4.8 **Revoke.** `DELETE /api/workspaces/{slug}/invitations/{code}` by an owner or admin returns
`200` and `{"deleted": true}`; an unknown code returns `404`. A revoked code returns `404` on accept
and is not listed. Accepting a code that never existed returns `404`.

A4.9 **Workspace settings.** `PATCH /api/workspaces/{slug}` with any subset of `{name, join_mode}`
by an owner or admin returns `200` and `{workspace}` with the change applied. A `join_mode` other
than `"open"` or `"invite_only"`, or an empty name, returns `400`. A `member` gets `403`.

A4.10 **Invite-only.** When `join_mode` is `"invite_only"`, a user who is not a workspace member
gets `403` on `POST /api/channels/{id}/join` for a public channel and is not added. After accepting
an invitation, the same join returns `200`.

A4.11 **Bad input is a 400, never a 500.** Every Spec A route checks the JSON type of each field it
reads before using it. A wrong type (for example a number for `username`, `slug`, `name` or
`role`, a string for `max_uses`, `expires_in_seconds` or `new_owner_id`, `null` for `join_mode`)
returns `400`, and so does a body that is not valid JSON. Login with non-string fields returns
`400` or `401`. No route answers `500` to bad input.

Every request must answer within 5 seconds.
