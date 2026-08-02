import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const panel = readFileSync(join(process.cwd(), 'src/main/components/MainPanel.vue'), 'utf8')
const store = readFileSync(join(process.cwd(), 'src/main/stores/aiInvokeRuns.ts'), 'utf8')

describe('MainPanel AI-run exclusive rendering contract (0378)', () => {
  it('mounts bootstrap, run, and document as mutually exclusive branches', () => {
    const bootstrap = panel.indexOf('v-if="aiRunBootstrapPending"')
    const run = panel.indexOf('v-else-if="activeGroupRunActive"')
    const documentBranch = panel.indexOf('<template v-else>', run)
    const header = panel.indexOf('<DocHeader', documentBranch)
    const branchEnd = panel.indexOf('</template>\n          </div><!-- doc-main -->', documentBranch)

    expect(bootstrap).toBeGreaterThan(0)
    expect(run).toBeGreaterThan(bootstrap)
    expect(documentBranch).toBeGreaterThan(run)
    expect(header).toBeGreaterThan(documentBranch)
    expect(branchEnd).toBeGreaterThan(header)
    expect(panel).not.toContain('suppress-doc-ref=')
  })

  it('keeps every document-side surface out of the active-run DOM', () => {
    expect(panel).toContain('v-if="!aiRunDocumentLocked && canShowDocInfoPanel(tab.id)"')
    expect(panel).toContain('v-if="!aiRunDocumentLocked && activeTabId != null && activeTab')
    expect(panel).toContain("aiRunBootstrapPending.value || activeGroupRunActive.value")
    expect(panel).toContain("fetchDoc?.(activeTabId.value)")
  })

  it('fails closed until global active-run discovery completes', () => {
    expect(store).toContain('const bootstrapPending = ref(true)')
    expect(store).toContain('bootstrapPending.value = false')
    expect(store).toMatch(/return \{\s*\n\s*runsByGroup,\s*\n\s*bootstrapPending,/)
    expect(panel).toContain('void aiInvokeRunsStore.bootstrap()')
  })
})