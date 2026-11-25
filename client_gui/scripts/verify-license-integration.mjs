// Reproducible F86 checks in a fresh Self-check workspace (dependencies do not persist between runs).
// Run from client_gui: node scripts/verify-license-integration.mjs build|bundle|typecheck|test|server
import { spawnSync } from 'node:child_process'
const mode = process.argv[2]
if (!['build', 'bundle', 'typecheck', 'test', 'server'].includes(mode)) throw new Error('Expected build, bundle, typecheck, test or server')
function run(program, args, shell = false) {
  const result = spawnSync(program, args, { stdio: 'inherit', shell })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
// Run typecheck, bundle, test and server separately under the 60-second Self-check limit.
// build is a combined frontend convenience command for environments without that limit.
if (mode === 'server') {
  const python = [process.env.PYTHON, 'python3', 'python'].filter(Boolean).find(program => {
    const probe = spawnSync(program, ['--version'], { stdio: 'ignore' })
    return !probe.error && probe.status === 0
  })
  if (!python) throw new Error('Python interpreter not found (PYTHON, python3, python)')
  run(python, ['-m', 'pytest', '-q', '../server/tests/regression/test_voice_0058.py', '../server/tests/architecture/test_0086_third_party_notices.py'])
  process.exit(0)
}
run(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['ci', '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'], process.platform === 'win32')
if (mode === 'build' || mode === 'typecheck') {
  run(process.execPath, ['node_modules/vue-tsc/bin/vue-tsc.js', '-b'])
}
if (mode === 'build' || mode === 'bundle') {
  run(process.execPath, ['node_modules/vite/bin/vite.js', 'build'])
  run(process.execPath, ['scripts/check-web-notices.mjs'])
} else if (mode === 'test') {
  run(process.execPath, ['node_modules/vitest/vitest.mjs', 'run',
    'tests/features/conversation/voiceLicense.f86.spec.ts',
    'tests/features/conversation/voiceInput.api.v02test0058.spec.ts',
    'tests/features/settings/systemVoice.v02test0058.spec.ts',
    '--maxWorkers=1', '--minWorkers=1'])
}
