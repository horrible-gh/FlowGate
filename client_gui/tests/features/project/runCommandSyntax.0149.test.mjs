import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { resolve } from 'node:path'
const files = [
  'client/shared/api.ts',
  'client_gui/src/features/project/flowRunApi.ts',
  'client_gui/src/features/project/store.ts',
  'client_gui/src/features/designer/center/graph/flowGraphSource.ts',
  'client_gui/src/features/designer/center/graph/serverFlowGraphSource.ts',
  'client_gui/src/features/designer/center/run/flowRunControl.ts',
  'client_gui/src/features/designer/center/run/runFailureDiagnostic.ts',
  'client_gui/src/features/designer/center/run/runCommandStore.ts',
  'client_gui/src/features/ai/runResult/runResultWatch.ts',
  'client_gui/src/features/designer/shell/runFailureToast.ts',
  'client_gui/src/features/designer/shell/DesignerShell.vue',
  'client_gui/tests/features/project/runCommandRecovery.0149.spec.ts',
  'client_gui/tests/features/designer/cardInterventionShell.0125.spec.ts',
  'client/tests/shared/apiRunCommandTimeout.0149.spec.ts',
  'client_gui/src/app/i18n/ko.ts', 'client_gui/src/app/i18n/en.ts', 'client_gui/src/app/i18n/ja.ts',
]
for (const file of files) test(`TypeScript syntax parses: ${file}`, () => {
  let text = readFileSync(resolve(file), 'utf8')
  if (file.endsWith('.vue')) {
    const script = /<script setup lang="ts">([\s\S]*?)<\/script>/.exec(text)
    assert.ok(script, 'actual SFC script must be extracted')
    text = script[1]
  }
  assert.doesNotThrow(() => stripTypeScriptTypes(text, { mode: 'strip', sourceUrl: file }))
})


test('ko/en/ja recovery key sets match and existing run mode labels remain intact', async () => {
  const messages = await Promise.all(['ko','en','ja'].map(async lang => (await import(`../../../src/app/i18n/${lang}.ts`)).default))
  const keys = []
  for (const m of messages) {
    const top = m.designer.top
    assert.deepEqual(Object.keys(top.run_command).sort(), ['full', 'step'])
    assert.ok(top.run_command.step && top.run_command.full)
    assert.ok(top.run_command_recovery)
    keys.push(Object.keys(top.run_command_recovery).sort())
    assert.ok(Object.values(top.run_command_recovery).every(value => typeof value === 'string' && value.length > 0))
  }
  assert.equal(keys[0].length, 13)
  assert.deepEqual(keys[0], keys[1]); assert.deepEqual(keys[0], keys[2])
})
