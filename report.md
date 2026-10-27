# Milestone 1 report

- Name: Rohit Ramesh
- UNI: rr3713

All results are from my own checks. Every run used GPT 5.6 Luna through `run_student_openai.sh 1`
and started from an empty `/app`. For each run I report the last check output the agent saw in its
transcript. I confirmed each of these by re-running the same checks against the run's collected
`artifacts/app`, after a fresh `pip install -r requirements.txt`, in a clean Ubuntu 24.04 container.
The two agree for every run.

## Spec and checks

The final specs are `milestone-1-specs/single-file/` and `milestone-1-specs/staged/`. Both layouts
carry the same 112 rules: Spec A has 48 (A0.1 to A4.11) and Spec B has 64 (B0.1 to B6.6). The
single-file rule text is generated from the staged files, so the rules cannot diverge between
layouts. The README variants used for the one-component runs (Approaches 2 and 4) are in
`student-files/run-readmes/`.

The spec went through two versions:

| Version | Rules | Content |
| --- | --- | --- |
| Initial spec | 107 | Architecture guide, milestone 0 baseline (A0, B0), Spec A groups A1 to A4, Spec B groups B1 to B6. Checks validated against a reference implementation with mutation testing. One check message was corrected during this round, with no rule change. |
| Revised spec | 112 | Adds five rules for behavior that the initial runs' apps got wrong outside the checks: wrong JSON types or malformed bodies return `400` rather than `500` (A4.11, B5.10); edits recompute mentions and mention counts (B5.9); concurrent posts across the three nodes keep `event_id` dense and unique (B4.8); a subscriber can resume on a surviving node after another node is killed (B4.9). |

To check that the final apps solve the task rather than fit my checks, I also searched all twelve
apps for any reference to the spec folder, the check code or the check data (none found). I then
ran the two final combined apps against a held-out set of 15 tests built from `instruction.md`
with data and request shapes the checks never use. Both passed all 15.

| Run | Approach | Component | Version | Job folder | Steps | Time | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2 | A | Initial | `jobs/2026-10-21__18-17-47/` | 30 | 8 min | $0.10 |
| 2 | 2 | B | Initial | `jobs/2026-10-21__21-17-03/` | 41 | 4 min | $0.21 |
| 3 | 4 | A | Initial | `jobs/2026-10-21__21-22-23/` | 35 | 3 min | $0.14 |
| 4 | 4 | B | Initial | `jobs/2026-10-21__21-26-13/` | 68 | 6 min | $0.49 |
| 5 | 1 | both | Initial | `jobs/2026-10-21__21-33-17/` | 81 | 8 min | $0.76 |
| 6 | 3 | both | Initial | `jobs/2026-10-21__21-42-20/` | 88 | 10 min | $0.84 |
| 7 | 2 | A | Revised | `jobs/2026-10-23__00-55-31/` | 28 | 3 min | $0.08 |
| 8 | 2 | B | Revised | `jobs/2026-10-23__00-58-58/` | 70 | 7 min | $0.57 |
| 9 | 4 | A | Revised | `jobs/2026-10-23__11-33-03/` | 31 | 3 min | $0.11 |
| 10 | 4 | B | Revised | `jobs/2026-10-23__11-36-38/` | 62 | 7 min | $0.45 |
| 11 | 1 | both | Revised | `jobs/2026-10-23__11-43-44/` | 62 | 7 min | $0.55 |
| 12 | 3 | both | Revised | `jobs/2026-10-23__11-51-11/` | 57 | 6 min | $0.44 |

Total cost: $4.75. No run raised an exception or produced format errors, and every run submitted
on its own after its checks showed `0 failed`. In the one-component runs, `/app` starts empty, so
the Component B runs build only the parts of Component A that Spec B depends on. Each approach's
final `/app` comes from its run with the revised spec.

## Approach 1

### Run 1 (run 5)

- Component: both
- Spec and checks version: initial spec, single-file layout
- Job folder: `jobs/2026-10-21__21-33-17/`
- My check results: `check_roles.py` 47 passed, 0 failed; `check_channels.py` 60 passed, 0 failed
- Problems: Spec A started at 11 passed, 36 failed. Along the way the agent hit a login regression
  (A1.5) and a crash in the join handler (A2.6 to A2.8). In Spec B it fixed private channels shown
  in workspace detail (B1.4), the missing `423` on topic updates (B1.10), whitespace-only bodies
  being accepted (B2.2), malformed reply SQL (B2.11) and search paging (B3.5 to B3.7). Beyond the
  checks, wrong-type inputs returned `500` and edits did not recompute mentions.

### Run 2 (run 11)

- Component: both
- Spec and checks version: revised spec, single-file layout
- Job folder: `jobs/2026-10-23__11-43-44/`
- My check results: `check_roles.py` 48 passed, 0 failed; `check_channels.py` 64 passed, 0 failed
- Problems: the first full Spec A check gave 23 passed, 25 failed. The fixes covered `500`s in the
  join handler (A2.7 to A2.9), exhausted and expired invitations still being listed and targeted
  invitations keeping `max_uses` above 1 (A4.4, A4.6, A4.7), and a string `expires_in_seconds` being
  accepted (A4.11). In Spec B (41 passed, 23 failed at its first full run) it fixed `500`s on
  archive and `include_archived` (B1.5, B1.10), private-channel visibility (B1.4), threads (B2.11),
  search filtering and paging (B3.5 to B3.7), event emission, no-op writes and resume (B4.4 to B4.7),
  read-state counting and edit mentions (B5.4 to B5.9), and the IRC gateway (B6). Beyond the
  checks, one implementation problem remains: a deleted message still counts toward
  `unread_count` and `mention_count`, which contradicts B5.4 ("non-deleted messages"). No check
  deletes a message and then reads the read state.

### Final `/app`

- Job folder: `jobs/2026-10-23__11-43-44/`
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_roles.py` (A0 to A4) | 48 | 0 | |
  | `check_channels.py` (B0 to B6) | 64 | 0 | Deleted messages still counted in read state (outside the checks); held-out tests 15/15 |

## Approach 2

### Run 1 (run 1)

- Component: A
- Spec and checks version: initial spec, single-file layout
- Job folder: `jobs/2026-10-21__18-17-47/`
- My check results: 47 passed, 0 failed
- Problems: three full check runs (10 passed, 37 failed → 19 passed, 28 failed → 47 passed). Early
  failures included duplicate registration returning `500` instead of `409` (A1.2) and missing
  `401`s (A0.7, A1.8).

### Run 2 (run 2)

- Component: B
- Spec and checks version: initial spec, single-file layout
- Job folder: `jobs/2026-10-21__21-17-03/`
- My check results: 60 passed, 0 failed
- Problems: after building the Spec A prerequisites, the agent fixed private-channel visibility
  (B1.4), topic permissions (B1.6), search filtering and paging (B3.5 to B3.7), event emission and
  resume (B4.4 to B4.7) and read state (B5.4 to B5.7), and built the IRC gateway.

### Run 3 (run 7)

- Component: A
- Spec and checks version: revised spec, single-file layout
- Job folder: `jobs/2026-10-23__00-55-31/`
- My check results: 48 passed, 0 failed
- Problems: three full check runs (10 passed, 38 failed → 47 passed, 1 failed → 48 passed). The
  agent's first input-type validation was too strict and rejected valid registrations with `400`
  (A1.1, A1.2, A1.4), so the logins that depended on them failed too (A1.5, A1.7, A1.9). Joining a
  private channel was also allowed (A2.9).

### Run 4 (run 8)

- Component: B
- Spec and checks version: revised spec, single-file layout
- Job folder: `jobs/2026-10-23__00-58-58/`
- My check results: 64 passed, 0 failed
- Problems: the agent fixed private-channel visibility (B1.4) and `500`s on archive and
  `include_archived` (B1.5, B1.10). It fixed the access rules for leave and posting (B1.8, B2.3),
  threads, edits, deletes and reactions (B2.11 to B2.14), and search (B3.5 to B3.7). It fixed event
  emission, no-op writes, resume, the Redis outage and resume after a node kill (B4.4 to B4.7,
  B4.9), and read state and edit mentions (B5.4 to B5.9). A malformed JSON body returned `500`
  (B5.10). The IRC gateway went from 0 of 6 to 6 of 6.

### Final `/app`

- Job folders: Spec A in `jobs/2026-10-23__00-55-31/`, Spec B in `jobs/2026-10-23__00-58-58/`. Each run
  starts from an empty `/app`, so this approach ends with two separate apps.
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_roles.py` on the A run's `/app` | 48 | 0 | |
  | `check_channels.py` on the A run's `/app` | — | — | Not built in this run |
  | `check_roles.py` on the B run's `/app` | — | — | Not built in this run |
  | `check_channels.py` on the B run's `/app` | 64 | 0 | |

## Approach 3

### Run 1 (run 6)

- Component: both
- Spec and checks version: initial spec, staged layout
- Job folder: `jobs/2026-10-21__21-42-20/`
- My check results: `check_roles_all.py` 47 passed, 0 failed; `check_channels_all.py` 60 passed, 0 failed
- Problems: step A.1 hit a missing table and duplicate registration returning `500` (A1.2). After the B
  steps, the final `check_roles_all.py` caught a regression the agent had introduced in invitations
  (39 passed, 8 failed; A4.2 to A4.10 returning `500`). It was repaired before submission.

### Run 2 (run 12)

- Component: both
- Spec and checks version: revised spec, staged layout
- Job folder: `jobs/2026-10-23__11-51-11/`
- My check results: `check_roles_all.py` 48 passed, 0 failed; `check_channels_all.py` 64 passed, 0 failed
- Problems: the agent passed each step check in order. Notable fixes: the topic length was not
  validated at creation (B1.2), new workspace members were not added to `#general` (B1.9), and topic
  permissions were wrong (B1.6). The concurrent-write check (B4.8) found events that were dense but
  numbered from the wrong starting point relative to the subscriber's `head_event_id`. Resume after
  a node kill (B4.9) failed at first, and the IRC gateway did not start until a connection-list bug
  was fixed (B6). The run ended with a cluster restart and both full checks passing.

### Final `/app`

- Job folder: `jobs/2026-10-23__11-51-11/`
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_roles_all.py` (A.1 to A.4) | 48 | 0 | |
  | `check_channels_all.py` (B.1 to B.6) | 64 | 0 | No problems found outside the checks; held-out tests 15/15 |

## Approach 4

### Run 1 (run 3)

- Component: A
- Spec and checks version: initial spec, staged layout
- Job folder: `jobs/2026-10-21__21-22-23/`
- My check results: 47 passed, 0 failed
- Problems: every step passed on its first check except A.2, where a non-member could list workspace
  members (A2.5). It was fixed in one step.

### Run 2 (run 4)

- Component: B
- Spec and checks version: initial spec, staged layout
- Job folder: `jobs/2026-10-21__21-26-13/`
- My check results: 60 passed, 0 failed
- Problems: B.1 took several rounds (`401` ordering, archived listing, topic permissions, leave,
  archive). B.2 needed fixes to empty bodies, edits, deletes and reactions. B.4 started at 0 of 7
  and B.6 at 0 of 6, and both reached full passes.

### Run 3 (run 9)

- Component: A
- Spec and checks version: revised spec, staged layout
- Job folder: `jobs/2026-10-23__11-33-03/`
- My check results: 48 passed, 0 failed
- Problems: bogus tokens produced `500` instead of `401` (A0.7). Login and `me` returned `500` until
  the token lookup was fixed (A1.5 to A1.9). The join response was missing the `{channel}` envelope
  (A2.7) and joining a private channel was allowed (A2.9). Role validation ran after the permission
  check (A3.4), guests could create channels (A3.8), and expired invitations were still listed
  (A4.7).

### Run 4 (run 10)

- Component: B
- Spec and checks version: revised spec, staged layout
- Job folder: `jobs/2026-10-23__11-36-38/`
- My check results: 64 passed, 0 failed
- Problems: an unknown message id returned `500` on pin (B0.8), and several permission rules were
  wrong (B1.6, B1.8, B1.10, B2.3). Offset pagination was accepted (B2.10). Further fixes covered
  threads, edits, deletes and reactions (B2.11 to B2.14), pin idempotence and paging (B3.1, B3.2),
  search (B3.5 to B3.7), event emission and resume (B4.4 to B4.7), `@channel` (B5.3) and edit
  mentions (B5.9). The final `check_channels_all.py` showed 63 passed, 1 failed (B1.6, topic update
  by the workspace owner). This was fixed before submission.

### Final `/app`

- Job folders: Spec A in `jobs/2026-10-23__11-33-03/`, Spec B in `jobs/2026-10-23__11-36-38/` (separate
  apps, as in Approach 2).
- My check results for A and B:

  | Check | Passed | Failed | Notes |
  | --- | --- | --- | --- |
  | `check_roles_all.py` on the A run's `/app` | 48 | 0 | |
  | `check_channels_all.py` on the A run's `/app` | — | — | Not built in this run |
  | `check_roles_all.py` on the B run's `/app` | — | — | Not built in this run |
  | `check_channels_all.py` on the B run's `/app` | 64 | 0 | |

## Comparison of the approaches

With the final specs, all four approaches reached 100% of my checks for the components in scope,
each in a single run well inside the 50-minute budget (3 to 7 minutes). The two combined approaches
both produced one `/app` passing all 112 rules and all 15 held-out tests. On my checks, then, the
layouts tie on final quality; the differences are in process and robustness.

- **Staged (Approaches 3 and 4).** The staged layout gave the agent smaller targets and caught
  problems at the step that introduced them. Its per-step checks exposed the `event_id` offset
  (B4.8) during B.4. Its component-wide regression check caught an invitation regression that an
  agent introduced while building Component B. The final staged combined run was also the cheaper
  of the two final combined runs (57 steps, $0.44, against 62 steps and $0.55), and its app showed
  no problems outside the checks.
- **Single-file (Approaches 1 and 2).** The single-file layout let the agent build a whole component
  before testing. It saw large failure counts early (for example 23 passed, 25 failed) but
  converged just as reliably. It also produced the one implementation problem found outside the
  checks (deleted messages still counted in read state), which suggests the staged structure led
  to more careful work per rule.
- **One component at a time (Approaches 2 and 4).** These runs were cheaper per run and made each
  component's failures easy to isolate. Because every run starts from an empty `/app`, though, the
  Component B runs had to rebuild part of Component A, and neither approach yields one app covering
  both components.

Across the two spec versions, the agent reliably built what the checks verified. Behavior that the
initial spec described but did not check (input-type validation, mentions on edit) was missing from
both initial combined apps. Once the revised spec checked it, it was present in both final combined
apps. Because the final apps also passed held-out tests they had never seen, they implement the
contract rather than just the checks. These results come from my checks and held-out tests only;
the hidden tests may cover behavior that neither set exercises.
