# Read this first: what to build, in what order, and when you may submit

`/app` starts empty. In this run you build the cluster plus two components of the Slack clone:

- **Component A, roles and invitations** (`/spec/roles/roles.md`, rule groups A0 to A4): auth,
  workspaces, joining, roles, ownership transfer, invitations, join mode.
- **Component B, channels and message history** (`/spec/channels/channels.md`, rule groups B0 to
  B6): channel management, history and paging, pins, search, WebSocket event ordering, mentions,
  read state, and the IRC bridge.

The task description you were given is still the grading contract. The files in `/spec` do not
change it; they say what to build now and how to prove it works.

## Files in /spec

| File | What it is |
|---|---|
| `README.md` | This file: scope, order, time plan, how to verify, when to submit |
| `common/architecture.md` | **Read second.** The design, `start.sh`, and the function names to use |
| `common/restart.sh` | Restarts the cluster and waits for all three nodes |
| `common/checklib.py` | HTTP, WebSocket and IRC helpers used by the checks (no need to read) |
| `roles/roles.md` | Spec A: 48 rules, A0.1 to A4.11 |
| `roles/checks/check_roles.py` | Check for Spec A: prints PASS or FAIL for each rule |
| `roles/checks/roles_rules.py` | The rule code `check_roles.py` runs (read it only to see exactly what a failing rule sent) |
| `channels/channels.md` | Spec B: 64 rules, B0.1 to B6.6 |
| `channels/checks/check_channels.py` | Check for Spec B: prints PASS or FAIL for each rule |
| `channels/checks/channels_rules.py` | The rule code `check_channels.py` runs |

## Scope

Build only the cluster and the rules in `roles.md` and `channels.md`. Do **not** build the browser
UI, files, DMs, user groups, slash commands, profile editing, `@here` or group mentions in this
run; they are not graded here.

## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)

| Minutes | Work |
|---|---|
| 0 to 4 | Read this file and `common/architecture.md`. Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt` |
| 4 to 22 | Spec A, in group order A0, A1, A2, A3, A4 |
| 22 to 40 | Spec B groups B0 to B5 |
| 40 to 45 | B6 (IRC), only if every other rule passes |
| last 5 | Stop adding features: restart, run both checks, submit (see "Before you submit") |

The rule groups are ordered so that each one only needs routes from earlier groups. If you fall
more than 5 minutes behind, move on once a group shows no more than 2 failures and come back at
the end.

## Order of work

1. Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`),
   `/app/requirements.txt`, `core.py` and `server.py`.
2. Read `roles/roles.md`. Work through it one rule group at a time:
   1. Implement the group.
   2. Run `bash /spec/common/restart.sh`, then that group's check, for example
      `python3 /spec/roles/checks/check_roles.py A2`.
   3. Fix failures one at a time, lowest rule number first, restarting before each re-check.
3. Run `python3 /spec/roles/checks/check_roles.py` (all groups). Fix anything that regressed.
4. Read `channels/channels.md` and do the same for B0 to B6, then run
   `python3 /spec/channels/checks/check_channels.py` and both full checks again.

## How to verify

Run each of these as a single command:

```bash
bash /spec/common/restart.sh                              # after every code change (--fresh after a schema change)
python3 /spec/roles/checks/check_roles.py                 # all of Spec A
python3 /spec/roles/checks/check_roles.py A3 A4           # only some groups
python3 /spec/channels/checks/check_channels.py           # all of Spec B
python3 /spec/channels/checks/check_channels.py B4        # only some groups
```

Each check prints one line per rule and a final summary:

```text
PASS A2.1 POST /api/workspaces returns 201 {workspace, general_channel}; join_mode open; caller is owner
FAIL A2.2 a duplicate slug returns 409, even from another user: expected 409, got 201 {"workspace": ...}
RESULT: 46 passed, 1 failed
```

- A check finishes in under 30 seconds. If it prints `NOTE ... hit its 26s limit`, run the groups
  that NOTE names in a separate command (the NOTE shows the exact command).
- `setup: ...` means a route from an earlier group is broken. Fix that group first.
- The checks start the cluster if it is down, but they do not restart it after a code change. Use
  `restart.sh`, which stops processes by process group and by port PID. Never kill by name.
- Rules A0.6 and B0.6 kill node `8001` once (last) and B4.7 kills Redis once, to prove `start.sh`
  respawns them.

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

- `check_roles.py` ends with `RESULT: 48 passed, 0 failed`;
- `check_channels.py` ends with `RESULT: 64 passed, 0 failed` (if it hits the time limit, run the
  groups its NOTE names; each run must end with `0 failed`);
- `GET /api/health` returns `200` on ports `8000`, `8001` and `8002`.

**Time-out rule:** when `bash /app/timer.sh` shows `0:04` or less, stop adding features. Run
`restart.sh`, confirm all three nodes answer `/api/health`, run `check_roles.py` once to be sure
nothing regressed, and submit. Never submit while the cluster does not boot.

These 112 rules are necessary, not sufficient. The full task in the grading contract still applies.
