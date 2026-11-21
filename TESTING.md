# FlowGate Testing Guidelines

flowgate.default.0394 — R0001 "Test code cleanup" §6–7, NR0003 §12–15.

This document defines **how to run tests** (execution tiers) and **when to add
tests** (addition policy). Both follow R0001. The original measurements below
come from actual NR0003 runs on 2026-08-08; later measurements are dated separately.

## 0. Rules for card workers

Comments, docs and commit messages must be in English.

Do not put internal document/task IDs in comments; put them in commit messages.
Keep the reason and constraints when shortening comments. Preserve runtime strings,
CLI help, immutable migrations, archived fixtures and existing Korean comment lines.
Run `python tools/internal_comment_baseline.py --check` after comment changes.
Its content baseline may only shrink; a removed reference does not permit a new one.
The read-only `--dump-baseline --offset N --limit M` command emits baseline rows
for review; concatenate all pages before replacing the baseline. Never add entries
to make a check pass.

Use `python tools/verify_comment_changes.py` to compare modified runtime syntax
with HEAD, including parser diagnostics. `--compare-client-tests` runs Vitest
against a temporary HEAD export and the current tree using installed dependencies.
It reports added failures and test counts; a clean comparison does not mean an
already failing suite passes. Use `--client-test tests/path.spec.ts` to narrow a
follow-up comparison. Test-consumed comment markers must remain unchanged.

Keep Korean UI copy, localization dictionaries, templates, SQL data, and test
expectation strings unchanged. Translate explanatory comments and docstrings,
including inline comments. When a comment quotes an actual UI label, retain the
quoted label exactly so that it remains searchable.

Run `python tools/korean_comment_baseline.py --check` from the repository root.
After translating existing comments, run the same command with `--write` to
remove their baseline entries. An existing baseline may only shrink: additions
are rejected by the writer and must also be rejected in review. Do not replace
or delete the baseline to admit new Korean comments. The scanner uses Python's
standard library and the existing TypeScript/Vue packages in `client_gui`;
prepare those dependencies with `npm ci` in `client_gui` before running it.

The guard compares normalized comment hashes and duplicate counts, not just
line totals. Adding a different comment while removing an old one still fails.
Keep the existing census and its allowlist unchanged.

---

### Selecting areas to run (0053 A1, A2)

To verify only the server features you changed, run from `server/`.

```text
python -m pytest tests -q -rEf --changed=flowgate/gate_decisions/service.py --tier=veryfast
python -m pytest tests -q -rEf --changed=flowgate/gate_decisions/service.py --tier=fast
python -m pytest tests -q -rEf -n 2 --dist loadfile --changed=flowgate/gate_decisions/service.py
python -m pytest tests -q -rEf -n 2 --dist loadfile --area=git.merge --tier=heavy
```

### Card worker test rules (0053)

If a card changed server source, follow only these steps. Do not run the whole suite.

1. **While editing**: `--changed=<changed paths> --tier=veryfast`. After finishing a chunk, `--tier=fast`.
2. **When finishing the card**: pass only `--changed=<changed paths>` (= standard). Use it with
   `-n 2 --dist loadfile` and `parallel_share 2`. Record this result as the TR's verification evidence.
3. **Run heavy or slow only for these changes**, once, with the changed sub-area as `--area`.
   - heavy: source of one sub-area changed across several files.
   - slow: `sql/migrations/**`, `db_bootstrap.py`, `modules/flow_gate/db/connection.py`/`dialect.py`,
     or `modules/flow_gate/services/git/**` changed.
4. Pass **every changed file** to `--changed`, comma-separated (paths relative to `server`). Include
   changed test files. Every path left out is verification left out.
5. Do not use `--install-check` or `python -m pytest tests` without an area option in a card.
6. If the client changed, run `npm run test:related -- <changed files>` and `npm run typecheck`.

Send it as a Self-check request like this (a single `python`, no shell operators).

```json
{"program": "python",
 "args": ["-m", "pytest", "tests", "-q", "-rEf", "-n", "2", "--dist", "loadfile",
          "--changed=modules/flow_gate/services/git/branch_merge.py,tests/modules/flow_gate/test_x.py"],
 "cwd": "server", "parallel_share": 2}
```

For parallel runs, also set `parallel_share` as the existing guidance says. The top-level areas are
`ai`, `git`, `gate`, `selfcheck`, `document`, `conversation`, `graph`, `card`, `platform`, `db` and
`guard`. The seven large areas are split into sub-areas (0053, 36 in total).

| Top level | Sub-areas |
|---|---|
| ai | `ai.roles` `ai.run` `ai.invoke` `ai.agent` `ai.provider` |
| git | `git.remote` `git.merge` `git.recovery` `git.worktree` `git.core` |
| document | `document.edit` `document.tr2` `document.attach` `document.core` |
| conversation | `conversation.question` `conversation.command` `conversation.chat` `conversation.mention` `conversation.turn` |
| graph | `graph.attempt` `graph.run` `graph.definition` `graph.root` `graph.workflow` |
| card | `card.workplan` `card.review` `card.settings` `card.completion` |
| platform | `platform.auth` `platform.scratch` `platform.settings` `platform.api` |

`--area` accepts several areas separated by commas. A top-level name (`--area=ai`) selects all of
its sub-areas. `--changed` selects **only the sub-areas** matching the changed source. Source that
cannot be assigned to a single sub-area (the shared DB foundation) selects several sub-areas.
`services/git/**` and `graph_workflow/**` belong as a whole to `git.core` and `graph.definition`,
but their **shared parts** (`command.py`, `lock_manager.py`, `refs.py`, `graph_model.py`,
`flow_guard.py`, `errors.py` and so on) are also listed in the `source` of each sibling sub-area
that imports them directly. So changing `git/command.py` runs merge, recovery and worktree along
with git.core, while changing `git/branch_merge.py` runs only git.core and git.merge. When a sibling
sub-area starts importing a shared part, add that file to the sub-area's `source` (`also` was not
used because it would make any git source run all of git).
A sub-area targets at most 40 test files and, up to standard, 60 seconds with 2 workers.
`--changed` takes **paths relative to `server`**. Several paths together with `--area` form a
union. A test file path selects that file's area. Unknown paths or areas fail before the run.
Shared DB foundation changes select several areas, so verify them in TS when they exceed a small
work budget.

The `tests` lists in `tests/areas.toml` decide each file's primary area. When you add a new
`test_*.py`, register it in the map too. For an exception, declare it as a string at the top of the
module, such as `pytestmark = pytest.mark.area("gate")` (it also works inside a marker list). Areas
are per file; a per-function area mark is not a selection criterion.
Quote TOML sub-area names, as in `[area."conversation.chat"]`.
`also` lists areas to verify together and expands one level only, from standard upward (veryfast
and fast do not expand). `source` patterns may overlap across areas. Patterns apply to POSIX paths
with fnmatch, and `*` also matches `/`.
`tests/architecture/test_test_areas.py` checks for missing or duplicated mappings and empty patterns.

Files outside the selected areas are excluded before import. selected/excluded in the summary
count files in the map; collected counts files actually collected by this command (it may include
tests skipped by tier). Without an area option everything is collected (tiers still stop at
standard). Tiers follow the A3 measurement rules below. Do not read the old measurement table below
as the runtime of the new areas.

Run the client from `client_gui/`.

```text
npm run test:related -- src/features/git/api.ts
npm run test:area -- git
npm run test:area -- designer
npm run test:areas:check
npm run typecheck
```

`test:related` picks related specs from Vitest's import graph. It can miss structure checks that
read files as text and dynamic dependencies, so verify such changes with `test:area` over the whole
area. `test:area` accepts several areas separated by commas or spaces; the top-level name `designer`
selects its three children. The areas are `shell`, `ai`, `conversation`, `document`, `git`,
`project`, `settings`, `workplan`, `designer.inspector`, `designer.run` and `designer.canvas`. It
fails without arguments or with an unknown area.
Paths in `tests/areas.json` are relative to client_gui, and `*` also covers subpaths (the same as
the server map). Register each new spec in exactly one area. The remaining designer.canvas files
are kept as an explicit list, so update the list when adding one. `source` is a reference for the
changed area; test:related uses imports, not this map.
An area run also fails if it finds missing or duplicated entries or empty patterns in the whole map.
`test:areas:check` and the server's `tests/architecture/test_test_areas.py` check the same ownership
rules. `npm test` still runs everything. The client runtime budget has not been measured yet.

## 1. Execution tiers

### Why tiers?

The full server suite took **474 seconds for 3,223 tests**. The slowest 40 tests
accounted for only **15%** of that time; the remaining 85% was spread thinly across
the 3,223 tests at an average of 147 ms each. Removing or optimizing a few slow
tests therefore does not solve the overall runtime problem. The useful reduction
comes from avoiding tests unrelated to the current change. This is the evidence
behind R0001 §6: separate execution tiers instead of removing tests for being slow.

### Server

```bash
cd server

python -m pytest tests --changed=<path> --tier=veryfast   # frequently while editing
python -m pytest tests --changed=<path> --tier=fast       # right after finishing a chunk
python -m pytest tests --changed=<path>                   # when finishing a card (= --tier=standard)
python -m pytest tests --area=<area> --tier=heavy         # after a large change to that area
python -m pytest tests --area=<area> --tier=slow          # risky changes such as migrations or git integration
python -m pytest tests --install-check                    # install check: everything
```

There are five tiers and they are **cumulative**: choosing a tier also runs every tier below it
(0053, 2026-11-20).

| Tier | Time per file | Target per sub-area (2 workers) | When |
|---|---:|---:|---|
| veryfast | 1 s or less | 10 s | frequently while editing code |
| fast | 5 s or less | 30 s | right after finishing a chunk |
| standard | 15 s or less | 60 s | when finishing a card (default) |
| heavy | 30 s or less | 120 s | when finishing a card that changed that area substantially |
| slow | 120 s or less | 300 s | only after risky changes such as DB migrations or git integration |

Without `--tier` the tier is **standard**. The full tier, meaning "everything", was removed.
`--tier=full` fails with a note to use `--install-check` for an install check.

The **install check** (`--install-check`) runs everything regardless of tier. Files over 120 s in
total, e2e and `@pytest.mark.install` belong to no tier and run only here. It is for a user who has
just installed FlowGate, not for cards or daily work. The environment variable
`FLOWGATE_REQUIRE_FULL_SUITE=1` is the same switch. In an install check, missing host capabilities
are failures.

The table below holds **2026-08 measurements** from the former three tiers (fast/standard/full),
not the times of the current tiers.

| Former tier | Purpose | Selected tests | Measured time |
|---|---|---:|---:|
| fast | During development | 1,614 | ~110 s |
| standard | Completion verification | 2,822 | ~340 s |
| full | Release, refactoring, architecture changes | 3,244 | ~475 s |

These are **2026-08 measurements (213 files)** and have not been remeasured.
The suite has since grown substantially (flowgate.v02.0113 regression baseline:
550 tests in 1800 seconds, about 3.3 seconds each). flowgate.v02.0114 measured
representative selections before and after its changes rather than rerunning
the full suite (D 0006 9.1). Measurements from 2026-10:

| Measurement (flowgate.v02.0114, one Self-check each) | Before | After |
|---|---:|---:|
| Selection 1: DB setup (26 tests), template DB (T#1) | 60 s | 11 s |
| Selection 2: Git setup (3 tests), direct creation / template mode on the same code (T#2) | 71 s | 50 s |
| Self-check regressions, 923 tests, `-n 2 --dist loadfile` (T#4 baseline) | — | 459 s |
| Parallel comparison, 233 tests (8 files): serial / 2 / 4 processors, approved Self-check path (reservation, affinity, slot pool) (T#4) | 179 s | 124 s / 103 s |

CPU time divided by wall time was 0.68 for serial execution, 1.17 for two
processors, and 1.47 for four, all below their reservations. No other Self-check
run was observed on the host during measurement, and no approval was rejected.
These tests spend much of their time waiting for Git and subprocesses, so adding
workers does not fully utilize the processors. Worker count alone does not prove
a runtime improvement.

`_tier_for_module` in `server/tests/conftest.py` assigns tiers in this order:

1. An explicit `@pytest.mark.veryfast`, `.fast`, `.standard`, `.heavy`, `.slow`, or `.install`
   wins. It may be applied to a test, class, or module (`pytestmark`).
2. If `tests/timings.json` has a valid serial record, the file total decides: 1 s or less
   veryfast, 5 s or less fast, 15 s or less standard, 30 s or less heavy, 120 s or less slow, and
   anything longer belongs to the install check.
3. Without a record, with an incomplete one, or when the file hash changed, the tier is
   **standard**, so an unmeasured file does not slip into the frequently run veryfast and fast
   tiers. However:
   * a file that uses `subprocess`/git, or whose name is in the dialect, migration, worktree, or
     git category, is heavy;
   * an e2e file belongs to the install check;
   * a file with no baseline because it failed, but observed running over 60 s in M1
     (`measurement.ineligible_files_over_60s` in `timings.json`), moves up to the tier matching
     that time (slow or the install check).
   Once measured, rule 2 puts it in place.

Rules are preferable to a file list because a new file could otherwise fall into
no tier. Rules classify every new file immediately; only exceptions need an
explicit marker at the top of the file.

### Per-file timing records and budget report (0053 A3)

From `server/`, `--fg-timings-out=<path outside the repository>` writes the sum of
setup/call/teardown per file as JSON. Under xdist the controller aggregates the reports in one
place. Startup, import and collection time are not included, and a parallel total is not a serial
measurement.

```text
python -m pytest tests -q --fg-timings-out=/tmp/flowgate-timings.json
python tools/test_budget.py --timings /tmp/flowgate-timings.json
```

The first command runs from `server`, the second from the repository root. On Windows use a real
temporary path outside the repository. Full measurements are done in the M1 measurement card; task
verification measures only the affected files. Each run replaces the output; it is never merged
into an earlier measurement automatically.

`server/tests/timings.json` is the committed baseline, with version 1 and a files object. It holds
the split serial records of the 515 valid files from the 2026-11-20 M1. Each key is a test path
relative to `server`, and each value has seconds, seconds_by_marker (auto or an explicit marker
name), sha256 and eligible. The output also records the measurement time and exit code. Serial
results reviewed in M1 are applied to the baseline.

Incomplete records (parallel runs, `-k`/`-m`, node id selection, skips, failures, interruptions)
are never used as a baseline. A file with even one test skipped because of its tier has a skip, so
it does not become a baseline either. To measure the heavy, slow and install-check files too, add
`--install-check`. Every collected item of a file must report all three phases. When a file hash
changes, its earlier record is ignored. An explicit tier marker always wins over a timing record.

The budget report (`python tools/test_budget.py`) shows, per sub-area, the cumulative serial work
time of each tier, the number of unmeasured files missing from that total (`+N`), and the number of
install-check files. Time assigned by a marker counts toward that tier. When a sub-area has more
than 40 files, or a tier's serial total ÷ 2 exceeds that tier's target, it is listed after `SPLIT:`
as a candidate to split; this is not a failure. Serial total ÷ 2 is an ideal lower bound, not wall
time, so confirm targets with a parallel measurement. With unmeasured files the total is
incomplete.

### M1 split serial measurement — 2026-11-20 (0053)

Run without xdist on Windows 11, Python 3.14.2 and pytest 8.3.4. Because of the 1800-second limit
on chat commands, files ran per area, then sequentially in batches of at most 20 and later 10
files. Within each file everything ran without a tier limit (full at the time). The conditions
differ from a regression run of everything in a single process.
The measurement window was 08:38:00–14:16:08 (UTC+09), **5 hours 38 minutes 8 seconds** elapsed.

837 files were run and 830 produced timing records. Only the 515 valid files went into `files` of
`tests/timings.json`. The 315 ineligible files (failures, errors, skips, xfails and so on) and the
7 without records were left out of the baseline. These 322 are classified by the no-record rule
(rule 3 above) and are not treated as 0 seconds.

The main measurement totaled **20,430 passed, 1,829 failed, 453 errors, 158 skipped, 1 xfailed**.
Six more files had collection errors. The first, interrupted selfcheck-3/git-3 runs were replaced
by remeasurements and excluded from the total, and the 19 passed recorder regression checks are not
added to it either. These numbers come from split runs and do not mean the whole suite passes.
Fixing the test failures found was not part of this measurement.

The timing recorder hit an internal error when a test reported while `os.name` was temporarily
changed, because it rebuilt the path at that point. It now reuses the per-node-id path computed at
collection; a regression check that keeps the platform change through teardown and the 19 existing
serial and parallel checks pass. The failed git-3 was remeasured and finished normally with 154
passed and 2 failed. The changed recorder test file was updated with the latest hash and time from
the verification run.

Below is the **cumulative serial work time per tier** over valid records only, under the 11 areas
and three tiers of the time (fast 5 s or less, standard 30 s or less, full everything). For the
current sub-areas and five tiers, see `python tools/test_budget.py`. Import, collection and startup
time are excluded. Every area has undetermined files, so whether a real area run meets its budget
cannot be decided.

| Area | Valid/total files | fast total (s) | standard total (s) | full total (s) | Undetermined files |
|---|---:|---:|---:|---:|---:|
| ai | 67/127 | 55.556 | 191.311 | 568.451 | 60 |
| git | 65/102 | 27.454 | 228.186 | 2170.072 | 37 |
| gate | 27/40 | 4.726 | 136.844 | 1483.001 | 13 |
| selfcheck | 25/49 | 13.364 | 196.605 | 373.280 | 24 |
| document | 59/92 | 22.199 | 201.878 | 1410.719 | 33 |
| conversation | 69/115 | 37.837 | 263.895 | 679.773 | 46 |
| graph | 84/117 | 31.967 | 331.881 | 1891.805 | 33 |
| card | 36/78 | 6.534 | 177.238 | 1373.669 | 42 |
| platform | 59/73 | 54.052 | 142.001 | 353.660 | 14 |
| db | 8/16 | 4.620 | 25.097 | 129.303 | 8 |
| guard | 16/28 | 10.478 | 51.476 | 124.674 | 12 |

The fast total of valid records is 60 s or less in every area, but undetermined files and
collection time are missing. For standard, even the ideal 2-worker lower bound is 131.948 s for
conversation and 165.940 s for graph, over 120 s. The other areas' 2-worker wall time was not
measured either, so none is judged as within budget.

**49 valid files exceeded 60 seconds.** The ten longest are below. Among diagnostic records with
failures or skips, 38 more files exceeded 60 seconds; they were kept out of the baseline and listed
separately in `measurement.ineligible_files_over_60s` of `timings.json`.

| Seconds | File (relative to server) |
|---:|---|
| 505.105 | `tests/flowgate/test_card_document_l3_states_0127.py` |
| 493.723 | `tests/flowgate/test_fg_settings_retry_0126.py` |
| 357.901 | `tests/flowgate/test_fg_integration_persistence_0156.py` |
| 346.435 | `tests/test_fg_gate_run_resume_0132.py` |
| 337.028 | `tests/test_fg_gate_push_sync_0132.py` |
| 313.544 | `tests/modules/flow_gate/test_review_atomicity_0535.py` |
| 272.201 | `tests/modules/flow_gate/test_branch_merge_attempt_0630.py` |
| 260.253 | `tests/flowgate/test_card_document_l3_paths_0127.py` |
| 207.825 | `tests/modules/flow_gate/test_system_agents_0076.py` |
| 193.858 | `tests/flowgate/test_fg_document_rejection_0126.py` |

Reproduce the full list of valid files over 60 seconds and the per-area budgets from the repository
root.

```text
python tools/test_budget.py
```

The seven files without records are below. An independent collection recheck confirmed the same
six collection errors.

| File (relative to server) | Finding |
|---|---|
| `tests/integration/src_content/test_t509_src_content_routes.py` | Missing required Settings values ALLOWED_ORIGIN, CONTEXT, DB_TYPE |
| `tests/modules/flow_gate/test_git_service_error_envelope_0233.py` | Same missing required Settings values |
| `tests/modules/flow_gate/test_resolve_conflict_issue_lease_block_0447.py` | Same missing required Settings values |
| `tests/modules/flow_gate/test_token_predecessor_parity_0061.py` | Same missing required Settings values |
| `tests/test_remote_tool_move_0092.py` | Import of `test_remote_tool_group_worktree_routing_0671` fails |
| `tests/test_test_card_gui_0094.py` | Import of `test_test_spec_0549` fails |
| `tests/modules/flow_gate/test_mention_verification.py` | Only a `main` for direct runs, no pytest items |

To read records of a large measurement file within output length limits, page the JSON like this.
Continue until `next_offset` is null. This option does not change the source file.

```text
python tools/test_budget.py --timings <measurement JSON path> --records-offset 0 --records-limit 20
```

### Client

There are no client tiers: always run the whole suite. `client_gui` is the only
client (flowgate.v02gui.0024 removed the 0.1 `client/` directory).

```bash
cd client_gui
npm run typecheck   # vue-tsc -b
npm test            # vitest run
```

### Parallel execution (pytest-xdist)

The investigation host had 16 logical cores, but server tests used one process.
Parallel execution was introduced in 0394 T0004, added to `requirements.txt`,
and checked for safety as a way to reduce total time without deleting tests.

```bash
python -m pytest tests -n auto --dist loadfile
```

**`--dist loadfile` is required.** Tests use module/session-scoped temporary DBs
and replace `conn_mod.STORE`. The default `--dist load` distributes cases from
one file across workers, splitting those fixtures between processes. `loadfile`
keeps every case in a file on one worker with the same fixtures.

### Parallel Self-check: processor reservations and host limits (flowgate.v02.0114 T#4)

Self-check enforces a host-wide logical processor limit. **Every** Self-check
run, including serial runs and non-pytest commands, reserves at least one
processor. Reservations for running and recovery-waiting runs on the same
`node_key` cannot exceed the limit. TS execution is not Self-check and is not counted.

* `parallel_share` requests a share (default 1). The response's `cpu_reservation`
  is the granted reservation R.
* The operating system restricts the entire process tree to R processors
  (Windows: Job affinity; POSIX: the supervisor's CPU set). Whatever worker count
  a tool creates, at most R can execute simultaneously. Extra workers slow down
  rather than failing.
* Pytest under `server/tests` shares a per-run worker slot pool of size R.
  `-n 8` and `-n auto` are reduced to the available slots. Nested pytest uses only
  remaining slots and runs serially without a slot if none remain. The result's
  `parallel` block records requested/applied values and the slot source.
* Insufficient capacity returns **409 `selfcheck_host_capacity_exhausted`** without
  creating a run record. Retry later; there is no queue or background handoff.

Defaults, based on approved-path measurements on 2026-10-31 (recheck operational
reservation totals and rejection counts after rollout): limit = min(4, logical
processor count), serial reserve = 1, requested share = 1. Shares above one must
leave the serial reserve available, so `parallel_share=4` gets 3 on an idle host.
`FLOWGATE_SELFCHECK_HOST_LIMIT` changes the limit, capped at the logical processor
count; `FLOWGATE_SELFCHECK_SERIAL_RESERVE` changes the reserve.

```text
Large selection: pytest -q -n 2 --dist loadfile tests/...   + parallel_share 2
Small selection: pytest -q tests/test_x.py                 (omit parallel_share: 1)
```

Do not parallelize integration tests using fixed ports, builds/type checks that
write artifacts into the same source tree, or selections of one/few files.
Run baseline and final regressions on the same host with the same `parallel_share`
and `-n`. A baseline is not reusable when applied worker counts or affinity differ.

### Keeping large regressions below 1800 seconds (flowgate.v02.0146 T#2)

Do not increase the 1800-second Self-check limit. Three factors combined to push
19–21-file selections over it (2026-11-07, Windows, Python 3.14, pytest 8.3.4,
xdist 3.6.1, host reservations of 1–3 processors):

1. **`-n 4` without `parallel_share` runs one worker on one processor.** The result
   reports `requested 4 / applied 1`. The same four-file selection took 264 s with
   `-n 4` (share 1), 203 s with `-n 2` (share 2), and 193 s with `-n 4` (share 4,
   reservation 3). Always give `-n` and `parallel_share` the same value.
2. **Git subprocesses consume the time.** On one processor, 13 tests in
   `test_fg_gate_publish_0085` took 499 s, 29 in `test_fg_document_rejection_0126`
   took 388 s, two Git tests in `test_fg_card_settings` took about 255 s, and 29
   in `test_fg_provider_failure_retry_0121` took 150 s. Removing slow tests is not
   the remedy. Since `loadfile` gives each file to one worker, large files set a
   runtime floor. The two Git tests were moved without body changes to
   `test_fg_card_settings_merge_target_git_0146.py` and
   `test_fg_card_settings_no_worktree_git_0146.py`.
3. **Some files depended on global state left by other files:** document-write
   gates, port registration, the run-ID allocator contract, and the
   `tests/flowgate` import path. Running alone or changing worker assignment
   caused setup errors. Each harness now establishes its own prerequisites while
   keeping product contracts and assertions unchanged. The shared
   `test_fg_card_settings.env` harness and `test_fg_card_documents` use
   `fg_harness_write_gate.install` (the real gate, allowing only their own seed
   rows). `tr2_approval` registers the four document-write ports normally installed
   at server startup. `card_settings_restart` uses its own SQLite store,
   initialized run-ID contract, and real gate. `0136` initializes its store's
   run-ID contract. `tests/conftest.py` supplies the `tests/flowgate` import path.

Recommended plan: each run uses `pytest -q -rEf -n 3 --dist loadfile <files>`,
`parallel_share 3`, and `timeout_seconds` 1200.

| Batch | Files | Measured 2026-11-07 after the final change (requested 3, reserved 2) |
|---|---|---|
| G1 card settings | `test_fg_card_settings*.py` (7), `test_fg_settings_{capability_0126,context_0130,retry_0126,retry_completion_0126}.py` | 199 passed, 0 failures/errors, 528 s |
| G2 Git gates/rejection | `tests/test_fg_gate_intake_0085.py`, `tests/test_fg_gate_publish_0085.py`, `test_fg_document_rejection_0126.py` | 108 passed, 0 failures/errors, 708 s |
| G3 review/rework | `test_fg_provider_failure_retry_0121.py`, `test_review_loop_rework_binding_0136.py`, `test_fg_review_{duplicate_start,retry_verdict}_0128.py`, `test_fg_rewind_rework_0118.py`, `test_fg_rejection_history_0126.py`, `test_fg_card_documents.py` | 161 passed, 0 failures/errors, 242 s |

Even G2, the longest batch, uses 708 seconds (39% of the limit) in one hop.
The 3600-second `rework_timeout_sec` budget fits only two 1800-second runs, so
start with the batch directly related to the change instead of running everything
at once. Busy hosts may grant less than the requested share; inspect
`cpu_reservation`. These measurements requested 3 and received 2. Measurements
with reservation 3 were G1 271 s and G2 726 s; variation means the important
criterion is headroom below the limit, not the requested count alone.

**Per-test timeout:** `tests/hang_guard.py` applies `FLOWGATE_TEST_TIMEOUT`
(default 900 s, `0` disables it) or `@pytest.mark.timeout(seconds)` to every test.
On timeout it prints the test ID and all thread stacks to stderr and exits with
status 70. This is not a pass. Under xdist, the dead worker's test is reported
as failed and the others continue. The guard stays active even when
`pytest-timeout` is installed and loaded: that plugin does not interrupt tests
without a limit set through `--timeout`, ini `timeout`, or `PYTEST_TIMEOUT`.
Deferring to it would silently lose the environment/default/zero contract above.
When both are enabled, the first limit reached wins. Keep the limit above the
slowest healthy test (about 200 s); do not raise it to conceal slow tests.

---

## 2. Policy for new tests (v0.2 onward)

### File placement (NR0003 §7)

* Place a test for `server/<source-relative-path>/<module>.py` at
  `server/tests/<source-relative-path>/test_<module>.py`. Use feature or regression
  suffixes when splitting files and keep basenames unique throughout the test tree.
* Use `tests/integration/<feature>/` for multi-module integration,
  `tests/e2e/<feature>/` for real execution flows, and `tests/regression/<feature>/`
  for cross-module regressions that do not belong to a single module.
* Put source-text/global-structure guards in `tests/architecture/`. Do not move
  all ticket regressions into `regression/` indiscriminately.
* Keep shared fixtures scoped by root `tests/conftest.py`. Create only necessary
  directories; add no duplicate test copies or unrelated code changes.

### Choose tests by risk

Do not add tests to match a historical ratio. **There is no target for test LOC
or the test/product ratio.** NR0003 §3.2 supports this numerically: R0001's client
figures, 25,371 test lines / 6,858 product lines, omitted 39,945 lines of `.vue`
UI code. The actual ratio was about 0.44 rather than 3.7. Measurement choices
can change the ratio that much.

**Prioritize:** potential data loss, complex state transitions, difficult recovery,
external API contracts, permissions/security, concurrency, core workflow logic,
and regressions for bugs that actually occurred.

**Optional:** simple UI/presentation, implementation details, and low-risk features
that are easy to verify manually.

### Five rules learned in 0394

The first four are evidence-based. Classifying 44 persistent failures found that
**21 of 28 cases failed because tests were coupled to implementation, not because
the product was wrong**. One of these rules would have prevented each of those 21.

**(1) Use endpoints or public functions, rather than directly calling or stubbing internals.**

`_handle_new`, `_handle_review`, `_handle_edit`, and `_handle_test_run` changed
from `async def` to ordinary `def` plus `anyio.to_thread` to avoid blocking the
event loop. POST /inbox requests and responses stayed identical, yet eight tests
calling those functions through `asyncio.run(...)` failed at once. Endpoint tests
would have been unaffected. Use `post_inbox()` from `tests/inbox_client.py` in
server tests that call inbox.

Similarly, six tests stubbed `storage_paths.src_root`. When the product switched
to `git_service.base_src_root`, the unused stubs silently exposed real paths.
Every symptom was a 404, hiding the cause. Silent stub misses are an inherent
weakness of this approach. Change inputs for anything configurable through inputs,
such as paths/settings (`FLOWGATE_STORAGE_DIR`).

**(2) Reserve source-text assertions for global rules.**

Do not pin a ticket's layout, ordering, or file location with source assertions.
`MainPanel.aiRunExclusive.0378.spec.ts` checks string ordering in a `.vue` file.
The change that introduced it broke 11 tests of actual rendering, while the
source-text test stayed green. Tests that broke when `.document-modal` moved
from `MainPanel.vue` to `shared/app.css` had the same problem: the style still
worked; only the assertion was wrong.

For global rules (no duplicate definitions, no event-loop blocking, no Korean
leaks, unique migration numbers), scan the entire scope from the start. Eight
duplicate migration-number groups produced only one failure because the 076
guard only checked 076; the preceding seven groups had no guard at all.

**(3) Do not build schemas manually.**

`test_auth.py` applied only migrations 001/002/005 out of 87. When
`067a_auth_sessions.sql` arrived, seven tests failed with `no such table`.
Use `migrated_sqlite_db(name, seed_sql)` from `conftest.py` when a schema is needed.
Apply all migrations and do not swallow failures. Product-object doubles carry
the same risk: that file's `_SqliteStore` lacked `transaction()` and failed as
soon as session functionality used it.

**(4) If tests can be skipped, the results must show that they were skipped.**

`test_git_integration_0115.py` contains 107 tests. Without Git, all 107 disappear
while the result still reports zero failures, just as a fully passing run does.
Server tests therefore report host capabilities (Git, symlinks, API source tree,
`run.bat`) and capability-related skip counts at the end. For install checks
that must actually run the full suite, use:

```bash
python -m pytest tests --install-check
```

Missing host capabilities then fail instead of silently skipping tests.

**(5) Borrow environment variables and restore their prior values; do not delete them.**

Many suites publish storage roots at import time
(`os.environ["FLOWGATE_STORAGE_DIR"] = tempfile.mkdtemp(...)`). Pytest imports all
modules before running any tests, so all these values have already been set by
the time execution starts. Deleting a variable during cleanup removes other
modules' state too.

`test_numbering.py` and `test_outbound_single.py` deleted `FLOWGATE_STORAGE_DIR`
in `finally`. Later, `test_worktree_resolution_e2e_0280.py` lost its temporary root,
and `ensure_worktree` created directories in the wrong place, causing seven setup
errors. That file alone passed all eight tests. Remember why this remained hidden:
pytest counts errors separately from failures, and `-rf` does not summarize errors.
Use `-rEf` for full runs.

The simplest restoration is `monkeypatch.setenv`: pytest restores either the old
value or the original absence. For manual changes, save the previous value and
restore it in `finally`. As a safety net, `conftest.py` restores FLOWGATE_* variables
at module boundaries and reports leaking modules at the end. Fix the module that
leaks; do not rely on the safety net as the solution.

### Additional client rules

* **Mount tests must observe a ready component tree.** MainPanel's async gate,
  `v-if="aiRunBootstrapPending"`, broke 11 tests in five files that waited only
  for `$nextTick()`. Use `mountMainPanel()` from `tests/helpers/mountMainPanel.ts`;
  it waits for bootstrap and provides the shared stubs.
* **Before asserting absence, verify that the UI has rendered.** A closed gate
  mounts nothing, making absence assertions pass vacuously. A passing case in
  `MainPanel.reviewRequestMention.spec.ts` never consumed its seeded
  `pending_review`. Call `expectDocumentBranchMounted(wrapper)` first.
* **Tests must not contact a real server.** `tests/setup/blockNetwork.ts` blocks
  XHR and fetch. Mock `@shared/api` when a response is needed. Before this guard,
  four files sent real requests to a development server address and passed only
  because nothing was listening; results differed on hosts running that server.

### What to remove

Do not delete tests merely because there are many. Remove a test only when the
product no longer has that feature. Even then, changing it just to make it pass
would revive the removed feature, a decision the test author cannot make alone.
Record why it was removed in the file (0394's `test_worker_token_hint_0238.py`
is an example).

Consider consolidating duplicates before deleting them, but do not make broad
changes solely to consolidate files. Preserve which bug each case prevents,
including names and comments. The 145 ticket-numbered files are both fragmented
and real bug regressions; losing that information violates R0001 §4.6.
