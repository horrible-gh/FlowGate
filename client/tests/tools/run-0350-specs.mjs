/**
 * flowgate.default.0350 TS gate for the client-side `base_untracked_conflict`
 * specs.  Run from `client/` as `node tests/tools/run-0350-specs.mjs`.
 *
 * Why this exists instead of a bare `npx vitest run <files...>`:
 *
 *   Vitest exits 0 when a spec path on the command line matches nothing.  A
 *   renamed or never-committed spec therefore reports GREEN while testing
 *   nothing at all — the exact false-green this TS must not ship, since the
 *   verdict is the exit code and nothing else.  (Observed on this project
 *   before; see the run notes in the TS.)
 *
 * So this gate:
 *   1. asserts every expected spec file is present on disk *before* running,
 *   2. runs vitest with the JSON reporter,
 *   3. asserts the run actually executed the expected number of files and
 *      tests, with zero failed and zero pending/skipped.
 *
 * Any mismatch exits non-zero with a one-line reason.
 *
 * (C) 대상 아님 — 0394 T0016 / NR0003 §6.3. 이 파일은 테스트가 아니라 TS 게이트 스크립트다.
 * 여기서 읽는 파일은 제품 소스가 아니라 vitest가 방금 쓴 JSON 리포트이므로, "제품 소스의
 * 텍스트 배치를 단언한다"는 전환 대상에 해당하지 않는다.
 */
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

/** Specs added by flowgate.default.0350 TR0005 + this TS, and their test counts. */
const EXPECTED = [
  ['tests/main/GitUntrackedConflictDialog.spec.ts', 4],
  ['tests/main/GitStatusPanel.untrackedConflictRetry.spec.ts', 3],
  ['tests/main/GitFinalizePanel.untrackedConflict.spec.ts', 2],
  ['tests/main/GitActionMenu.untrackedConflict.spec.ts', 2],
  ['tests/main/ReviewActionBar.untrackedConflict.spec.ts', 2],
  ['tests/i18n/untrackedConflict0350.spec.ts', 9],
]

// `client/` — two levels up from tests/tools/. fileURLToPath, not URL.pathname:
// on Windows the latter yields a leading-slash "/C:/..." that resolve() keeps.
const CLIENT_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const EXPECTED_TESTS = EXPECTED.reduce((sum, [, count]) => sum + count, 0)

function fail(reason) {
  console.error(`GATE_FAILED ${reason}`)
  process.exit(1)
}

const missing = EXPECTED.map(([file]) => file).filter(
  (file) => !existsSync(resolve(CLIENT_DIR, file)),
)
if (missing.length > 0) {
  fail(`spec file(s) absent from the repo: ${missing.join(', ')}`)
}

const scratch = mkdtempSync(join(tmpdir(), 'fg-0350-gate-'))
const reportPath = join(scratch, 'vitest.json')

try {
  // Call vitest's own ESM entry with this interpreter rather than going through
  // npx/`.bin` shims — those resolve differently under cmd.exe and /bin/sh, and
  // the TS host runs cmd.exe.
  const vitestEntry = resolve(CLIENT_DIR, 'node_modules/vitest/vitest.mjs')
  if (!existsSync(vitestEntry)) {
    fail(`vitest is not installed at ${vitestEntry} — run \`npm install\` first`)
  }

  const run = spawnSync(
    process.execPath,
    [
      vitestEntry,
      'run',
      ...EXPECTED.map(([file]) => file),
      '--reporter=json',
      `--outputFile=${reportPath}`,
    ],
    { cwd: CLIENT_DIR, stdio: ['ignore', 'inherit', 'inherit'] },
  )

  if (!existsSync(reportPath)) {
    fail(`vitest produced no JSON report (exit ${run.status})`)
  }

  const report = JSON.parse(readFileSync(reportPath, 'utf8'))
  const results = report.testResults ?? []
  const total = report.numTotalTests ?? 0
  const passed = report.numPassedTests ?? 0
  const failed = report.numFailedTests ?? 0
  const pending = report.numPendingTests ?? 0
  const todo = report.numTodoTests ?? 0

  console.log(
    `0350 client gate: files=${results.length}/${EXPECTED.length} ` +
      `tests=${passed} passed / ${failed} failed / ${pending + todo} skipped`,
  )

  if (results.length !== EXPECTED.length) {
    fail(`expected ${EXPECTED.length} spec files to run, vitest ran ${results.length}`)
  }
  if (failed > 0) {
    for (const suite of results) {
      for (const assertion of suite.assertionResults ?? []) {
        if (assertion.status === 'failed') {
          console.error(`  FAILED ${assertion.fullName}`)
        }
      }
    }
    fail(`${failed} test(s) failed`)
  }
  if (pending + todo > 0) {
    fail(`${pending + todo} test(s) were skipped — a skip is not a pass`)
  }
  if (total !== EXPECTED_TESTS || passed !== EXPECTED_TESTS) {
    fail(`expected exactly ${EXPECTED_TESTS} passing tests, saw ${passed} of ${total}`)
  }
  if (run.status !== 0) {
    fail(`vitest exited ${run.status} despite a clean report`)
  }

  console.log('0350 client gate: OK')
} finally {
  rmSync(scratch, { recursive: true, force: true })
}
