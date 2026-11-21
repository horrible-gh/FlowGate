// Reproducible test-card regression check in a fresh self-check worktree.
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const cwd = fileURLToPath(new URL('..', import.meta.url))
let failed = false
function run(program, args, directory) {
  const result = spawnSync(program, args, {
    cwd: directory, stdio: 'inherit', shell: process.platform === 'win32',
  })
  if (result.error) console.error(result.error.message)
  if (result.status !== 0) failed = true
  return result.status === 0
}
// Installation and test deliberately share a process and worktree.
if (run('npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd)) {
  run('npm', ['test', '--',
    'tests/features/designer/inspector/testSpec/testCardSession.0071.spec.ts',
    'tests/features/designer/inspector/testSpec/testCardPresentation.0071.spec.ts',
    'tests/features/designer/inspector/testSpec/windows/T2Windows.0071.spec.ts',
  ], cwd)
}
process.exitCode = failed ? 1 : 0
