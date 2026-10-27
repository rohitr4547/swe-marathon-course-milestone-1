# Read this first: what to build, in what order, and when you may submit

`/app` starts empty. This run is about one component: **Component A, roles and invitations**
(`/spec/roles/roles-1.md` to `roles-4.md`, steps A.1 to A.4): the cluster baseline, auth, workspaces, joining,
roles, ownership transfer, invitations and join mode.

The task description you were given is still the grading contract. The files in `/spec` do not
change it; they say what to build now and how to prove it works.

## Files in /spec

| File | What it is |
|---|---|
| `README.md` | This file: scope, order, time plan, how to verify, when to submit |
| `common/architecture.md` | **Read second.** The design, `start.sh`, and the function names to use |
| `common/restart.sh` | Restarts the cluster and waits for all three nodes |
| `common/checklib.py` | HTTP, WebSocket and IRC helpers used by the checks (no need to read) |
| `roles/roles-1.md` | A.1: baseline and auth (A0, A1); builds on nothing |
| `roles/roles-2.md` | A.2: workspaces and joining (A2); builds on A.1 |
| `roles/roles-3.md` | A.3: roles and ownership transfer (A3); builds on A.1, A.2 |
| `roles/roles-4.md` | A.4: invitations and join mode (A4); builds on A.1 to A.3 |
| `roles/checks/check_roles_1.py` to `check_roles_4.py` | One check per step: PASS or FAIL for each rule |
| `roles/checks/check_roles_all.py` | Runs every step's rules |
| `roles/checks/roles_rules.py` | The rule code those checks run (read it only to see exactly what a failing rule sent) |

## Scope

Build only the cluster and the four steps. `architecture.md` also names functions for
channels, messages and IRC: ignore those in this run, except that `POST /api/workspaces/{slug}/channels`
and `POST /api/channels/{id}/join` are part of Spec A. Do not build the UI or any other feature.

## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)

| Minutes | Work |
|---|---|
| 0 to 4 | Read this file and `common/architecture.md`. Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt` |
| 4 to 14 | A.1: `core.py` and `server.py` with health, auth, the WebSocket token check |
| 14 to 35 | A.2, A.3, A.4 |
| 35 to 45 | Re-run the whole check, fix anything left, re-read the rules for anything the check cannot see |
| last 5 | Restart, run the check, submit |

## Order of work

Each step file has **Uses** (functions from earlier steps that you call, never rewrite), **Adds**
(functions this step introduces) and **Rules** (numbered to match the check output).

1. Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`),
   `/app/requirements.txt`, `core.py` and `server.py`.
2. For each step in order (A.1, A.2, A.3, A.4): read its spec file, implement it, run
   `bash /spec/common/restart.sh`, then its check (`check_roles_1.py` for A.1, and so on), and fix
   failures lowest rule number first.
3. After each step, run `check_roles_all.py`. Do not start a step while a rule from an earlier step
   fails.

## How to verify

```bash
bash /spec/common/restart.sh                   # after every code change (--fresh after a schema change)
python3 /spec/roles/checks/check_roles_1.py    # likewise _2, _3, _4
python3 /spec/roles/checks/check_roles_all.py  # every A rule
```

Each check prints one line per rule and ends with `RESULT: <passed> passed, <failed> failed`. It
finishes in under 30 seconds, starts the cluster if it is down, and kills node `8001` once (last)
to prove `start.sh` respawns it. Never kill processes by name; use `restart.sh`.

## Mistakes to avoid

- One command block per reply; write big files in parts of about 250 lines.
- Check the JSON type of every body field before using it: bad input is `400`, never `500` (A4.11).
- Keep `/app` bootable at every moment: the grader runs `pip install -r /app/requirements.txt`
  and `/app/start.sh` in a fresh container.

## Before you submit

Run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` only when, after your last code change and a
`restart.sh`, `check_roles_all.py` ends with `RESULT: 48 passed, 0 failed` and `GET /api/health` returns
`200` on ports `8000`, `8001` and `8002`. When `bash /app/timer.sh` shows `0:04` or less, stop,
restart, confirm health on all three nodes, and submit. Never submit while the cluster does not boot.
