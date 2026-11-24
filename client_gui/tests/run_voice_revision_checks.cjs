// 격리 Self-check마다 의존성을 준비한 뒤 음성 UI와 주석 기준을 검사한다.
const { spawnSync } = require('node:child_process')
const path = require('node:path')
const client = path.resolve(__dirname, '..')
function run(program, args, cwd) {
  const result = spawnSync(program, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' && program === 'npm.cmd' })
  if (result.error) throw result.error
  return result.status ?? 1
}
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const prepared = run(npm, ['ci', '--ignore-scripts', '--no-audit', '--no-fund'], client)
if (prepared) process.exit(prepared)
if (process.argv.includes('--comments')) {
  process.exit(run('python', ['tools/internal_comment_baseline.py', '--check'], path.dirname(client)))
}
const ui = run(process.execPath, ['node_modules/vitest/vitest.mjs', 'run',
  'tests/features/settings/systemVoice.v02test0058.spec.ts',
  'tests/features/conversation/voiceInput.api.v02test0058.spec.ts',
  '--maxWorkers=1', '--minWorkers=1'], client)
process.exit(ui)
