// Isolated Self-check trees need installation and verification in the same run.
import { spawnSync } from 'node:child_process'
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const run = (args) => {
  const result = spawnSync(npm, args, { stdio: 'inherit', shell: process.platform === 'win32' })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
run(['ci', '--include=dev', '--ignore-scripts', '--no-audit', '--no-fund'])
run(['test', '--', 'tests/features/conversation/chatStart.0047.spec.ts', 'tests/features/conversation/firstNames.0071.spec.ts'])
