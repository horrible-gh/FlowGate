// Card-only FlowGate Self-check adapter for F86-5's disposable runner (60s limit).
// Installs dependencies in that runner and probes its conventional local test URL.
// For normal regression checks use npm run verify:web-notices; this adapter is not CI.
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
function command(program, args) {
  const result = spawnSync(program, args, { stdio: 'inherit', timeout: 45000, env: { ...process.env, NODE_ENV: 'production' } })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
// cmd.exe is needed for npm.cmd on Windows; all arguments are fixed literals.
if (process.platform === 'win32') command('cmd.exe', ['/d', '/s', '/c', 'npm ci --include=dev --ignore-scripts --no-audit --no-fund'])
else command(npm, ['ci', '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'])
if (!process.argv.includes('--skip-types')) command(process.execPath, [require.resolve('vue-tsc/bin/vue-tsc.js'), '-b'])
if (process.argv.includes('--types-only')) process.exit(0)
command(process.execPath, [require.resolve('vite/package.json').replace(/package\.json$/, 'bin/vite.js'), 'build'])
command(process.execPath, ['scripts/check-web-notices.mjs'])
command(process.execPath, ['--check', 'tests/visual/themes/capture.mjs'])
try { require.resolve('playwright'); console.log('CAPTURE_PLAYWRIGHT: available') }
catch { console.log('CAPTURE_PLAYWRIGHT: unavailable; captures not produced') }
try {
  const r = await fetch('http://127.0.0.1:8088/app/designer', { signal: AbortSignal.timeout(2000) })
  console.log(`CAPTURE_TEST_SERVER: HTTP ${r.status}; authenticated test storage state still required`)
} catch { console.log('CAPTURE_TEST_SERVER: unavailable on 127.0.0.1:8088') }
