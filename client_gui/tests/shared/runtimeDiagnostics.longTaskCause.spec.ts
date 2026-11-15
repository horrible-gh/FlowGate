import { describe, expect, it } from 'vitest'
import {
  clearDiagnostics,
  dumpDiagnostics,
  recordMarkdownParse,
  recordSseEvent,
  snapshotRecoverySource,
} from '@shared/diagnostics/runtimeDiagnostics'

// 0616 T0004 rev5 finding 1: recordMarkdownParse()'s >=50ms promotion to a `long_task` entry
// must reuse the exact request-local cause already attributed to its own `markdown_parse`
// entry, not re-read the diagnostics module's ambient `lastRecoverySource` — which a second,
// unrelated SSE event arriving before the slow parse finishes can have already moved on to.
describe('recordMarkdownParse long_task promotion (0616 T0004 rev5 finding 1)', () => {
  it('promotes a slow parse to a long_task carrying the same cause as its markdown_parse entry, even after a later SSE event moved the ambient recovery source on', () => {
    clearDiagnostics()

    // Cause captured at reload-start time (mirrors MdViewer.vue's onDocumentContentChanged()).
    recordSseEvent('document_explorer_refresh')
    const cause = { recovery: snapshotRecoverySource(), epoch: null }

    // An unrelated SSE event arrives while the (slow) parse this `cause` belongs to is still
    // in flight — the ambient module-global recovery source now points somewhere else.
    recordSseEvent('ping')

    recordMarkdownParse(5000, 75, cause)

    const entries = dumpDiagnostics().entries
    const parse = entries.find((e) => e.type === 'markdown_parse') as any
    const longTasks = entries.filter((e) => e.type === 'long_task') as any[]

    expect(parse.lastRecoverySource).toBe('sse_event:document_explorer_refresh')
    expect(longTasks).toHaveLength(1)
    expect(longTasks[0].source).toBe('markdown_parse')
    expect(longTasks[0].lastRecoverySource).toBe(parse.lastRecoverySource)
    expect(longTasks[0].lastRecoverySourceAgeMs).toBe(parse.lastRecoverySourceAgeMs)
    // Not the ambient global, which by now points at the later, unrelated event.
    expect(longTasks[0].lastRecoverySource).not.toBe('sse_event:ping')
  })

  it('promotes an unattributed (cause=null) slow parse to a long_task with no recovery source, not whatever the ambient global currently holds', () => {
    clearDiagnostics()
    recordSseEvent('ping')

    recordMarkdownParse(5000, 60, null)

    const longTasks = dumpDiagnostics().entries.filter((e) => e.type === 'long_task') as any[]
    expect(longTasks).toHaveLength(1)
    expect(longTasks[0].lastRecoverySource).toBeNull()
    expect(longTasks[0].lastRecoverySourceAgeMs).toBeNull()
  })
})
