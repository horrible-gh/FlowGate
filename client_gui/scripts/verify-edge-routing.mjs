/** Reproducible focused verification, including fresh/self-check worktrees.
 * node scripts/verify-edge-routing.mjs [--install] [--tests-only | --types-only]
 * --install explicitly restores lockfile dependencies in the same isolated run.
 */
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const cwd = fileURLToPath(new URL('..', import.meta.url))
function run(program, args, shell = false) {
  const result = spawnSync(program, args, { cwd, stdio: 'inherit', shell })
  if (result.error) console.error(result.error.message)
  return result.status ?? 1
}
if (process.argv.includes('--install')) {
  const status = run(process.platform === 'win32' ? 'npm.cmd' : 'npm',
    ['ci', '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'], process.platform === 'win32')
  if (status) process.exit(status)
}
const tests = process.argv.includes('--types-only') ? 0 : run(process.execPath, ['node_modules/vitest/vitest.mjs', 'run', '--maxWorkers=1', '--minWorkers=1',
  'tests/features/designer/edgeRouting.0076.spec.ts',
  'tests/features/designer/mergeEdges.spec.ts',
  'tests/features/designer/FlowCanvasFixes.0045.spec.ts',
  'tests/features/designer/directionalLayout.0068.spec.ts',
  'tests/features/designer/autoLayoutSession.0068.spec.ts'])
const types = process.argv.includes('--tests-only') ? 0 : run(process.execPath, ['node_modules/vue-tsc/bin/vue-tsc.js', '--noEmit', '--incremental', 'false',
  '--composite', 'false', '-p', 'tsconfig.app.json'])
process.exit(tests || types)
