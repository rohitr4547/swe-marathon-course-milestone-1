# Read this first: what to build, in what order, and when you may submit

`/app` starts empty. In this run you build the cluster plus two components of the Slack clone,
written as ten specs that build on each other:

- **Component A, roles and invitations** (`/spec/roles/`, steps A.1 to A.4): auth, workspaces,
  joining, roles, ownership transfer, invitations, join mode.
- **Component B, channels and message history** (`/spec/channels/`, steps B.1 to B.6): channel
  management, history and paging, pins, search, WebSocket event ordering, mentions, read state,
  and the IRC bridge.

The task description you were given is still the grading contract. The files in `/spec` do not
change it; they say what to build now, in what order, and how to prove it works.

## Files in /spec

| File | What it is | Builds on |
|---|---|---|
| `README.md` | This file: scope, order, time plan, how to verify, when to submit | |
| `common/architecture.md` | **Read second.** The design, `start.sh`, and the function names every step uses | |
| `common/restart.sh` | Restarts the cluster and waits for all three nodes | |
| `common/checklib.py` | HTTP, WebSocket and IRC helpers used by the checks (no need to read) | |
| `roles/roles-1.md` | A.1: baseline and auth (A0, A1) | nothing |
| `roles/roles-2.md` | A.2: workspaces and joining (A2) | A.1 |
| `roles/roles-3.md` | A.3: roles and ownership transfer (A3) | A.1, A.2 |
| `roles/roles-4.md` | A.4: invitations and join mode (A4) | A.1 to A.3 |
| `channels/channels-1.md` | B.1: baseline and channel management (B0, B1) | A.1 to A.4 |
| `channels/channels-2.md` | B.2: messages, threads, paging (B2) | B.1 |
| `channels/channels-3.md` | B.3: pins and search (B3) | B.1, B.2 |
| `channels/channels-4.md` | B.4: WebSocket events and ordering (B4) | B.1 to B.3 |
| `channels/channels-5.md` | B.5: mentions and read state (B5) | B.1 to B.4 |
| `channels/channels-6.md` | B.6: IRC bridge (B6), last | B.1 to B.5 |
| `roles/checks/check_roles_1.py` to `check_roles_4.py` | One check per A step | |
| `roles/checks/check_roles_all.py` | Runs every A step's rules | |
| `roles/checks/roles_rules.py` | The rule code those checks run (read it only to see exactly what a failing rule sent) | |
| `channels/checks/check_channels_1.py` to `check_channels_6.py` | One check per B step | |
| `channels/checks/check_channels_all.py` | Runs every B step's rules | |
| `channels/checks/channels_rules.py` | The rule code those checks run | |

Each spec file has three parts: **Uses** (functions from earlier steps that you call, never
rewrite), **Adds** (functions this step introduces, with the names to use), and **Rules** (what
must be true over HTTP, WebSocket or IRC, numbered to match the check output).

## Scope

Build only the cluster and the ten steps above. Do **not** build the browser UI, files, DMs, user
groups, slash commands, profile editing, `@here` or group mentions in this run; they are not graded
here. Never stop or skip work to "make the UI first".

## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)

| Minutes | Work |
|---|---|
| 0 to 4 | Read this file and `common/architecture.md`. Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt` |
| 4 to 12 | A.1: `core.py` and `server.py` with health, auth, the WebSocket token check |
| 12 to 22 | A.2, A.3, A.4 |
| 22 to 40 | B.1 to B.5 |
| 40 to 45 | B.6, only if every earlier check passes |
| last 5 | Stop adding features: restart, run every check, submit (see "Before you submit") |

If you fall more than 5 minutes behind this plan, move to the next step once the current step's
check shows no more than 2 failures, and come back to them at the end.

## Order of work

1. Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`),
   `/app/requirements.txt`, `core.py` and `server.py`.
2. For each step in order (A.1, A.2, A.3, A.4, B.1, B.2, B.3, B.4, B.5, B.6):
   1. Read the step's spec file.
   2. Implement it, calling the functions listed under **Uses**.
   3. Run `bash /spec/common/restart.sh`, then the step's check.
   4. Fix failures one at a time, lowest rule number first, restarting before each re-check.
   5. Run the component's `_all` check. If a rule from an earlier step now fails, fix it before
      starting the next step.
3. Do not start a step while a rule from an earlier step fails (except under the time rule above).

## How to verify

Run each of these as a single command:

```bash
bash /spec/common/restart.sh                          # after every code change (--fresh after a schema change)
python3 /spec/roles/checks/check_roles_1.py           # likewise _2, _3, _4
python3 /spec/roles/checks/check_roles_all.py         # every A rule
python3 /spec/channels/checks/check_channels_1.py     # likewise _2 ... _6
python3 /spec/channels/checks/check_channels_all.py   # every B rule
```

Each check prints one line per rule and a final summary:

```text
PASS A2.1 POST /api/workspaces returns 201 {workspace, general_channel}; join_mode open; caller is owner
FAIL A2.2 a duplicate slug returns 409, even from another user: expected 409, got 201 {"workspace": ...}
RESULT: 8 passed, 1 failed
```

- A check finishes in under 30 seconds. If it prints `NOTE ... hit its 26s limit`, run the groups
  that NOTE names in a separate command (the NOTE shows the exact command).
- `setup: ...` means a route from an earlier step is broken. Fix that step first.
- The checks start the cluster if it is down, but they do not restart it after a code change. Use
  `restart.sh`, which stops processes by process group and by port PID. Never kill by name.
- The baseline rules A0 and B0 kill node `8001` once (last) and B4.7 kills Redis once, to prove
  `start.sh` respawns them.

## Mistakes to avoid

- One command block per reply. A reply with two blocks, or one cut off mid-file, is rejected.
  Write big files in parts of about 250 lines (see the end of `common/architecture.md`).
- Do not hand-roll a WebSocket server or use Redis for fan-out: follow architecture rules 3 and 4.
- Do not put `event_id` on REST message objects; only WebSocket frames carry it.
- Mentions are resolved against workspace members (B5.1 to B5.3), not just regex matches, and
  are resolved again on every edit (B5.9).
- Check the JSON type of every body field before using it: bad input is `400`, never `500`
  (A4.11, B5.10). Wrap `request.json()` so a malformed body is also a `400`.
- Idempotent writes (repeat reaction, re-join, second leave, second delete) must not emit events.
- Keep `/app` bootable at every moment: the grader runs `pip install -r /app/requirements.txt`
  and `/app/start.sh` in a fresh container. Anything installed by hand is not there.

## Before you submit

You may run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` only when all of these are true, from
output you actually saw after your last code change and a `restart.sh`:

- `check_roles_all.py` ends with `RESULT: 48 passed, 0 failed`;
- `check_channels_all.py` ends with `RESULT: 64 passed, 0 failed` (if it hits the time limit, run
  the groups its NOTE names; each run must end with `0 failed`);
- `GET /api/health` returns `200` on ports `8000`, `8001` and `8002`.

**Time-out rule:** when `bash /app/timer.sh` shows `0:04` or less, stop adding features. Run
`restart.sh`, confirm all three nodes answer `/api/health`, run `check_roles_all.py` once to be sure
nothing regressed, and submit. Never submit while the cluster does not boot.

These 112 rules are necessary, not sufficient. The full task in the grading contract still applies.
