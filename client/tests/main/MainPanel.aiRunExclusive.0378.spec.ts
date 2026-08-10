import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const panel = readFileSync(join(process.cwd(), 'src/main/components/MainPanel.vue'), 'utf8')
const store = readFileSync(join(process.cwd(), 'src/main/stores/aiInvokeRuns.ts'), 'utf8')

describe('MainPanel AI-run read-only coexistence contract (0398)', () => {
  it('keeps only bootstrap exclusive and mounts the status card with the document tree', () => {
    const bootstrap = panel.indexOf('v-if="aiRunBootstrapPending"')
    const documentBranch = panel.indexOf('<template v-else>', bootstrap)
    const run = panel.indexOf('v-if="activeGroupRunInlineVisible"', documentBranch)
    const header = panel.indexOf('<DocHeader', run)
    const viewer = panel.indexOf('<MdViewer', header)
    const branchEnd = panel.indexOf('</template>\n          </div><!-- doc-main -->', viewer)

    expect(bootstrap).toBeGreaterThan(0)
    expect(documentBranch).toBeGreaterThan(bootstrap)
    expect(run).toBeGreaterThan(documentBranch)
    expect(header).toBeGreaterThan(run)
    expect(viewer).toBeGreaterThan(header)
    expect(branchEnd).toBeGreaterThan(viewer)
    expect(panel).not.toContain('v-else-if="activeGroupRunActive"')
    expect(panel).not.toContain('suppress-doc-ref=')
  })

  it('locks mutation surfaces while keeping the header and body viewers mounted', () => {
    expect(panel).toContain('v-if="!aiRunDocumentLocked"\n            :test-run=')
    expect(panel).toContain('v-if="!aiRunDocumentLocked"\n            :type-code=')
    expect(panel).toContain('v-if="!aiRunDocumentLocked && tab.typeCode')
    expect(panel).toContain('v-if="!aiRunDocumentLocked && (tab.typeCode')
    expect(panel).toContain('v-if="!aiRunDocumentLocked && canShowDocInfoPanel(tab.id)"')
    expect(panel).toContain('v-if="!aiRunDocumentLocked && activeTabId != null && activeTab')
    expect(panel).toContain(':read-only="aiRunDocumentLocked"')
    expect(panel).toContain('class="ro-badge ro-badge-sm"')
    expect(panel).toContain("t('main.document_preview.edit_locked')")
  })

  it('covers the read-only renderer matrix for md, CH, Q, AC/DC, text, diff and fallback views', () => {
    expect(panel).toContain('<MdViewer')
    expect(panel).toContain('<ConversationView')
    expect(panel).toContain(':read-only="aiRunDocumentLocked && !activeChatOwnRun"')
    expect(panel.match(/<QTDetailViewer/g)).toHaveLength(2)
    expect(panel).toContain("tab.typeCode === 'AC'")
    expect(panel).toContain("tab.typeCode === 'DC'")
    expect(panel).toContain('<TextViewer')
    expect(panel).toContain('<FileDiffViewer')
    expect(panel).toContain("tab.type === 'too_large'")
    expect(panel).toContain('class="unsupported-view"')
  })

  it('closes mutation modals and refreshes the still-mounted viewers at run transitions', () => {
    for (const assignment of [
      'editVisible.value = false',
      'headerEditModeVisible.value = false',
      'editDropdownTabId.value = null',
      'rejectDialogVisible.value = false',
      'aiInvokeVisible.value = false',
      'nextActionModalVisible.value = false',
      'continuousDialogVisible.value = false',
      'continuousWarnVisible.value = false',
      'designHandoffVisible.value = false',
      'timeMachineVisible.value = false',
      'returnConfirmVisible.value = false',
    ]) expect(panel).toContain(assignment)
    expect(panel).toContain('await closeFullView()')
    expect(panel).toContain('onRejectDialogClosed()')
    expect(panel).toContain("new CustomEvent('fg:document_content_changed'")
    expect(panel).toContain("new CustomEvent('fg:qa_refresh'")
    expect(panel).not.toContain("new CustomEvent('fg:q_registered'")
    expect(panel).toContain('textViewerRefs[docId]?.loadContent?.()')
  })

  it('fails closed until global active-run discovery completes', () => {
    expect(store).toContain('const bootstrapPending = ref(true)')
    expect(store).toContain('bootstrapPending.value = false')
    expect(store).toMatch(/return \{\s*\n\s*runsByGroup,\s*\n\s*bootstrapPending,/)
    expect(panel).toContain('void aiInvokeRunsStore.bootstrap()')
  })
})