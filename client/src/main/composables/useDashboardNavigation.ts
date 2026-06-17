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

  function expandGroupAncestors(
    projectId: string,
    nodes: Array<{ id: string; parent_id: string | null }>,
    nodeId: string,
  ) {
    let current = nodes.find((node) => node.id === nodeId)
    let parentId = current?.parent_id ?? null
    while (parentId) {
      try {
        localStorage.setItem(`flowgate:grp-exp:${projectId}:${parentId}`, '1')
      } catch {
        /* ignore storage errors */
      }
      current = nodes.find((node) => node.id === parentId)
      parentId = current?.parent_id ?? null
    }
  }

  async function openDashboardTarget(navigation: DashboardNavigation) {
    const projectId = projectStore.currentProjectId
    if (!projectId || navigation.kind === 'none') return
    try {
      let nodes = explorerStore.getCachedGroupTree(projectId)
      if (!nodes) nodes = await explorerStore.fetchGroupTree(projectId, true)

      if (navigation.kind === 'document' && navigation.doc_id) {
        let node = nodes.find((item) => item.id === navigation.doc_id)
        if (!node) {
          nodes = await explorerStore.fetchGroupTree(projectId, true)
          node = nodes.find((item) => item.id === navigation.doc_id)
        }
        if (!node || node.node_type !== 'document') throw new Error('document_not_found')
        expandGroupAncestors(projectId, nodes, node.id)
        explorerStore.selectedGroupNodeId = node.id
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
