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

python -m pytest tests --tier=fast       # Quick regression checks during development
python -m pytest tests --tier=standard   # Completion verification
python -m pytest tests                   # Release / major refactoring (full, the default)
```

Without `--tier`, all tests run. The full default is intentional: existing commands
and scripts must not silently start running fewer tests.

| Tier | Purpose | Selected tests | Measured time |
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

1. An explicit `@pytest.mark.fast`, `.standard`, or `.full` wins. It may be applied
   to a test, class, or module (`pytestmark`).
2. Otherwise, classify mechanically from module source:
   * fast: no `TestClient`, `subprocess`/Git, or `sleep(`.
   * standard: no Git/subprocess and no e2e, dialect, migration, worktree, or git filename category.
   * full: everything else.

Rules are preferable to a file list because a new file could otherwise fall into
no tier. Rules classify every new file immediately; only exceptions need an
explicit marker at the top of the file.

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
`run.bat`) and capability-related skip counts at the end. For release checks
that must actually run the full suite, use:

```bash
FLOWGATE_REQUIRE_FULL_SUITE=1 python -m pytest tests
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
