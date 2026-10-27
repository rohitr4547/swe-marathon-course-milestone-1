# Read this first: what to build, in what order, and when you may submit

`/app` starts empty. This run is about one component: **Component B, channels and message
history** (`/spec/channels/channels-1.md` to `channels-6.md`, steps B.1 to B.6): channel management, history and paging, pins, search, WebSocket event
ordering, mentions, read state, and the IRC bridge.

The task description you were given is still the grading contract. The files in `/spec` do not
change it; they say what to build now and how to prove it works.

## Files in /spec

| File | What it is |
|---|---|
| `README.md` | This file: scope, order, time plan, how to verify, when to submit |
| `common/architecture.md` | **Read second.** The design, `start.sh`, and the function names to use |
| `common/restart.sh` | Restarts the cluster and waits for all three nodes |
| `common/checklib.py` | HTTP, WebSocket and IRC helpers used by the checks (no need to read) |
| `channels/channels-1.md` | B.1: baseline and channel management (B0, B1); builds on the prerequisites |
| `channels/channels-2.md` | B.2: messages, threads, paging (B2); builds on B.1 |
| `channels/channels-3.md` | B.3: pins and search (B3); builds on B.1, B.2 |
| `channels/channels-4.md` | B.4: WebSocket events and ordering (B4); builds on B.1 to B.3 |
| `channels/channels-5.md` | B.5: mentions and read state (B5); builds on B.1 to B.4 |
| `channels/channels-6.md` | B.6: IRC bridge (B6), last; builds on B.1 to B.5 |
| `channels/checks/check_channels_1.py` to `check_channels_6.py` | One check per step: PASS or FAIL for each rule |
| `channels/checks/check_channels_all.py` | Runs every step's rules |
| `channels/checks/channels_rules.py` | The rule code those checks run (read it only to see exactly what a failing rule sent) |

## Scope and prerequisites

Component B needs a few Component A routes, because the B checks create their users, workspaces
and channels through them. `/app` is empty, so build these first, exactly as the grading contract
describes them (the function names are in `architecture.md`):

- `POST /api/auth/register` (`201 {user, token}`, `409` duplicate, `400` invalid), `POST /api/auth/login`
  (`200`, `401`), `GET /api/auth/me`;
- `POST /api/workspaces` (`201 {workspace, general_channel}`; the creator is `owner` and joins
  `#general`), `GET /api/workspaces`, `GET /api/workspaces/{slug}/members`;
- `POST /api/workspaces/{slug}/channels` (members create public channels; only owner/admin create
  private ones; guests none) and `POST /api/channels/{id}/join` (open workspace: a non-member
  becomes a `member` and also joins `#general`; private channel `403`; unknown `404`);
- `PATCH /api/workspaces/{slug}/members/{user_id}` with `{role}` (owner/admin only).

Do not build invitations, ownership transfer or the UI in this run.

## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)

| Minutes | Work |
|---|---|
| 0 to 4 | Read this file and `common/architecture.md`. Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt` |
| 4 to 12 | The prerequisites above, plus health and the WebSocket token check |
| 12 to 40 | B.1 to B.5 |
| 40 to 45 | B.6 (IRC), only if every earlier check passes |
| last 5 | Stop adding features: restart, run the checks, submit |

## Order of work

Each step file has **Uses** (functions from earlier steps that you call, never rewrite), **Adds**
(functions this step introduces) and **Rules** (numbered to match the check output).

1. Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`),
   `/app/requirements.txt`, `core.py` and `server.py`, then the prerequisites.
2. For each step in order (B.1 to B.6): read its spec file, implement it, run
   `bash /spec/common/restart.sh`, then its check (`check_channels_1.py` for B.1, and so on), and
   fix failures lowest rule number first. `setup: ...` means a prerequisite route is broken.
3. After each step, run `check_channels_all.py`. Do not start a step while a rule from an earlier
   step fails.

## How to verify

```bash
bash /spec/common/restart.sh                          # after every code change (--fresh after a schema change)
python3 /spec/channels/checks/check_channels_1.py     # likewise _2 ... _6
python3 /spec/channels/checks/check_channels_all.py   # every B rule
```

Each check prints one line per rule and ends with `RESULT: <passed> passed, <failed> failed`. It
finishes in under 30 seconds; if it prints `NOTE ... hit its 26s limit`, run the groups the NOTE
names in a separate command. It starts the cluster if it is down, kills node `8001` once (last), and
B4.7 kills Redis once, to prove `start.sh` respawns them. Never kill processes by name; use
`restart.sh`.

## Mistakes to avoid

- One command block per reply; write big files in parts of about 250 lines.
- Follow architecture rules 3 and 4 for events; do not use Redis for fan-out.
- Do not put `event_id` on REST message objects; only WebSocket frames carry it.
- Idempotent writes (repeat reaction, re-join, second leave, second delete) must not emit events.
- Resolve mentions again on every edit (B5.9). Check the JSON type of every body field: bad input is
  `400`, never `500` (B5.10).
- Keep `/app` bootable at every moment: the grader runs `pip install -r /app/requirements.txt`
  and `/app/start.sh` in a fresh container.

## Before you submit

Run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` only when, after your last code change and a
`restart.sh`, `check_channels_all.py` ends with `RESULT: 64 passed, 0 failed` (or every group run its NOTE
names ends with `0 failed`) and `GET /api/health` returns `200` on ports `8000`, `8001` and `8002`.
When `bash /app/timer.sh` shows `0:04` or less, stop, restart, confirm health on all three nodes,
and submit. Never submit while the cluster does not boot.
