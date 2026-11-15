// Compact, complete failure identities for comparing TR self-checks whose output is capped.
// Test names are hashed with their suite path; no failure is hidden or converted to a pass.
import { createHash } from 'node:crypto'
import type { Reporter } from 'vitest/reporters'

export default class RegressionReporter implements Reporter {
  onFinished(files: any[] = [], errors: any[] = []) {
    const failures: { file: string; ids: string[] }[] = []
    let passed = 0, failed = 0, skipped = 0
    for (const file of files) {
      const ids: string[] = []
      const visit = (task: any, parents: string[]) => {
        const names = [...parents, task.name]
        if (task.type === 'test') {
          if (task.result?.state === 'fail') { failed++; ids.push(createHash('sha256').update(JSON.stringify(names)).digest('hex')) }
          else if (task.result?.state === 'pass') passed++
          else skipped++
        } else {
          if (task.result?.state === 'fail' && !task.tasks?.length) ids.push('collection')
          for (const child of task.tasks ?? []) visit(child, names)
        }
      }
      for (const task of file.tasks ?? []) visit(task, [])
      if (file.result?.state === 'fail' || ids.length) failures.push({ file: file.name.replace(/\\/g, '/').split('/client_gui/').pop(), ids: ids.sort() })
    }
    console.log('TR_REGRESSION=' + JSON.stringify({ files: files.length, passed, failed, skipped, failures: failures.sort((a, b) => a.file.localeCompare(b.file)), errors: errors.map(error => ({ name: error.name, message: error.message })) }))
  }
}
