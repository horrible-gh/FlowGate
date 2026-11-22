// flowgate.v02test.0066 #36-1: reproducible check of the Flow tab conflict / AI resolving marks in a fresh self-check
// worktree (no node_modules there). Installation and the checks deliberately share one process and worktree, like
// check-test-card.mjs.   node scripts/check-flow-tab-conflicts.mjs [test] [typecheck] [<spec files>]
// (no argument = both, tests first; spec files given = only those are run by `test`)
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const cwd = fileURLToPath(new URL('..', import.meta.url))
const only = process.argv.slice(2).filter((arg) => arg.endsWith('.spec.ts'))
const named = process.argv.slice(2).filter((arg) => !arg.endsWith('.spec.ts'))
const steps = named.length ? named : only.length ? ['test'] : ['test', 'typecheck']
let failed = false
function run(program, args) {
  const started = Date.now()
  const result = spawnSync(program, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' })
  if (result.error) console.error(result.error.message)
  if (result.status !== 0) failed = true
  console.log(`[check-flow-tab-conflicts] ${program} ${args.slice(0, 2).join(' ')} -> exit ${result.status} in ${Math.round((Date.now() - started) / 1000)}s`)
  return result.status === 0
}
if (run('npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund'])) {
  if (steps.includes('test')) {
    run('npm', ['test', '--', ...(only.length ? only : [
      'tests/features/designer/flowTabConflicts.0066.spec.ts',
      'tests/features/designer/flowTabOverflowList.0023.spec.ts',
      'tests/features/designer/flowTabStripMarks.0035.spec.ts',
      'tests/features/designer/flowTabChatMark.0020.spec.ts',
      'tests/features/designer/DesignerCenter.0026.spec.ts',
      'tests/features/project/flowFilesApi.0024.spec.ts',
      'tests/features/git/gitSession.0033.spec.ts',
      'tests/features/git/DesignerGitPanel.0033.spec.ts',
    ])])
  }
  if (steps.includes('typecheck')) run('npm', ['run', 'typecheck:app'])
}
process.exitCode = failed ? 1 : 0
