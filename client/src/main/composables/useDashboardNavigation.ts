import { useI18n } from 'vue-i18n'
import { useProjectStore } from '../stores/project'
import { useExplorerStore } from '../stores/explorer'
import { useTabsStore } from '../stores/tabs'
import { useToast } from '../components/common/useToast'
import type { DashboardNavigation } from '../stores/dashboard'

// Open the document or group a dashboard / 🔔 notification item points at.
//
// Extracted from MainPanel's overview cards so the notification center (R0001 group 0045 / NR0003
// option A) navigates identically — same explorer-tree lookup, ancestor expansion, and tab-open behaviour
// — instead of forking a second copy. Used by both MainPanel (recent activity / workflow cards) and
// NotificationCenter.
export function useDashboardNavigation() {
  const projectStore = useProjectStore()
  const explorerStore = useExplorerStore()
  const tabsStore = useTabsStore()
  const { showToast } = useToast()
  const { t } = useI18n()

  // 0245 R0001 / NR0003 §1 — the ancestor walk moved into the explorer store, which
  // owns tree expansion now and still persists it under the same localStorage keys.
  function expandGroupAncestors(
    projectId: string,
    nodes: Array<{ id: string; parent_id: string | null }>,
    nodeId: string,
  ) {
    explorerStore.expandGroupAncestors(projectId, nodes, nodeId)
  }

  async function openDashboardTarget(navigation: DashboardNavigation) {
    const projectId = projectStore.currentProjectId
    if (!projectId || navigation.kind === 'none') return
    try {
      // 0454 T0006 §4.2 — full variant on both the cache read and the fetch. A dashboard card
      // or a 🔔 notification can point at a document or a group that is already final-approved
      // or discarded; on the pruned tree the lookup below would miss and the user would get
      // "navigation failed" for a target that exists.
      let nodes = explorerStore.getCachedGroupTree(projectId, true)
      if (!nodes) nodes = await explorerStore.fetchGroupTree(projectId, true, true)

      if (navigation.kind === 'document' && navigation.doc_id) {
        // 0316 T0004 / NR0003 recommendation 1 — the reveal+select (tree fetch, force-refetch on
        // miss, ancestor expansion, selection) now lives in the explorer store as the
        // single implementation the AI-work open paths and the SSE select intent reuse.
        const node = await explorerStore.revealDocInGroupTree(projectId, navigation.doc_id)
        if (!node) throw new Error('document_not_found')
        tabsStore.openTab({
          id: node.id,
          title: node.label,
          path: node.md_path ?? '',
          type: node.type_code === 'Q' ? 'qtui' : node.has_md ? 'md' : 'unsupported',
          mdPath: node.md_path,
          typeCode: node.type_code,
        })
        return
      }

      if (navigation.kind === 'group' && navigation.group_id) {
        const node = nodes.find((item) => item.id === navigation.group_id)
        if (!node) throw new Error('group_not_found')
        expandGroupAncestors(projectId, nodes, node.id)
        explorerStore.selectedGroupNodeId = node.id
      }
    } catch {
      showToast(t('main.overview.navigation_failed'), 'warning')
    }
  }

  return { openDashboardTarget, expandGroupAncestors }
}
