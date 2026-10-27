# Read this first: what to build, in what order, and when you may submit

`/app` starts empty. This run is about one component: **Component A, roles and invitations**
(`/spec/roles/roles.md`, rule groups A0 to A4): the cluster baseline, auth, workspaces, joining,
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
| `roles/roles.md` | Spec A: 48 rules, A0.1 to A4.11 |
| `roles/checks/check_roles.py` | Check for Spec A: prints PASS or FAIL for each rule |
| `roles/checks/roles_rules.py` | The rule code `check_roles.py` runs (read it only to see exactly what a failing rule sent) |

## Scope

Build only the cluster and the rules in `roles.md`. `architecture.md` also names functions for
channels, messages and IRC: ignore those in this run, except that `POST /api/workspaces/{slug}/channels`
and `POST /api/channels/{id}/join` are part of Spec A. Do not build the UI or any other feature.

## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)

| Minutes | Work |
|---|---|
| 0 to 4 | Read this file and `common/architecture.md`. Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt` |
| 4 to 14 | A0 and A1: `core.py` and `server.py` with health, auth, the WebSocket token check |
| 14 to 35 | A2, A3, A4 |
| 35 to 45 | Re-run the whole check, fix anything left, re-read the rules for anything the check cannot see |
| last 5 | Restart, run the check, submit |

## Order of work

1. Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`),
   `/app/requirements.txt`, `core.py` and `server.py`.
2. Read `roles/roles.md`. Work through it one rule group at a time (A0, A1, A2, A3, A4):
   implement the group, run `bash /spec/common/restart.sh`, then
   `python3 /spec/roles/checks/check_roles.py <group>`, and fix failures lowest rule number first.
3. After each group, run the full check. If an earlier rule now fails, fix it before moving on.

## How to verify

```bash
bash /spec/common/restart.sh                   # after every code change (--fresh after a schema change)
python3 /spec/roles/checks/check_roles.py      # all of Spec A; append e.g. A3 to run one group
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
`restart.sh`, `check_roles.py` ends with `RESULT: 48 passed, 0 failed` and `GET /api/health` returns
`200` on ports `8000`, `8001` and `8002`. When `bash /app/timer.sh` shows `0:04` or less, stop,
restart, confirm health on all three nodes, and submit. Never submit while the cluster does not boot.
