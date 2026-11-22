// flowgate.v02test.0067 #46: reproducible check of the chat follow-up ("이어서 답하기") in a fresh self-check worktree
// (no node_modules there). Installation and the checks deliberately share one process and worktree, like
// check-test-card.mjs.   node scripts/check-follow-up.mjs [test] [typecheck] [<spec files>]
// (no argument = tests only; spec files / tests/ folders given = only those are run by `test`; `--x` options go to vitest)
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const cwd = fileURLToPath(new URL('..', import.meta.url))
const only = process.argv.slice(2).filter((arg) => arg.endsWith('.spec.ts') || arg.startsWith('tests/'))
// vitest options (`--...`) go to vitest as they are; the chat specs log every send's timing, so --silent is the default.
const flags = process.argv.slice(2).filter((arg) => arg.startsWith('--'))
const named = process.argv.slice(2).filter((arg) => !only.includes(arg) && !arg.startsWith('--'))
const steps = named.length ? named : ['test']
let failed = false
function run(program, args) {
  const started = Date.now()
  const result = spawnSync(program, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' })
  if (result.error) console.error(result.error.message)
  if (result.status !== 0) failed = true
  console.log(`[check-follow-up] ${program} ${args.slice(0, 2).join(' ')} -> exit ${result.status} in ${Math.round((Date.now() - started) / 1000)}s`)
  return result.status === 0
}
if (run('npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund', '--prefer-offline'])) {
  if (steps.includes('test')) {
    run('npm', ['test', '--', ...(flags.length ? flags : ['--silent']), ...(only.length ? only : [
      'tests/features/conversation/followUp.0067.spec.ts',
      'tests/features/conversation/sendWhileRunning.v02test0029.spec.ts',
    ])])
  }
  if (steps.includes('typecheck')) run('npm', ['run', 'typecheck:app'])
}
process.exitCode = failed ? 1 : 0
