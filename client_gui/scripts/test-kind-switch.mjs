// Reproducible focused UI check for clean worktrees (including TR Self-check sandboxes).
// Install and run in the same process tree: separate sandboxes do not share node_modules.
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const cwd = fileURLToPath(new URL('..', import.meta.url))
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
function run(args) {
  const result = spawnSync(npm, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
run(['ci', '--no-audit', '--no-fund', '--prefer-offline'])
run(['test', '--',
  'tests/features/designer/inspector/kindSwitch/KindSwitchAction.0048.spec.ts',
  'tests/features/designer/inspector/kindSwitch/kindSwitchFlow.0048.spec.ts',
  'tests/features/designer/DesignerFlowCreateDialog.0104.spec.ts',
  '--maxWorkers=1', '--minWorkers=1',
])
