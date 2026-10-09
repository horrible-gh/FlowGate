import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { getRequest } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import { useProjectStore } from '../stores/project'
import { useToast } from '../components/common/useToast'
import { useDashboardNavigation } from './useDashboardNavigation'

// flowgate.v02.0048 T#4 (D#1 §9.3): `/main/?doc=<doc_id>` — the deep link the v0.2 Inspector opens
// in a new tab (↗ 열기). The URL carries the doc_id only; the session comes from this tab's own
// login (remember-me rotation at boot, or /login?next= back to this URL — a tab without a session
// is sent there by the router guard and never mounts, so this runs signed in).
//
// Order: read `doc` once the router is ready → look the document up for its project → switch to
// that project when it differs → open it through the dashboard / 🔔 navigation (the one
// explorer-reveal + tab-open implementation) → drop `doc` from the URL. A missing document (404)
// or any other failure shows the existing "could not open" toast; no permission (403) shows the
// existing permission toast. The dashboard stays as it is either way.
export const DOC_DEEP_LINK_QUERY = 'doc'

interface DocumentProjectRow {
  project_id?: unknown
}

export function useDocDeepLink() {
  const router = useRouter()
  const projectStore = useProjectStore()
  const { openDashboardTarget } = useDashboardNavigation()
  const { showToast } = useToast()
  const { t } = useI18n()

  async function dropDocQuery() {
    const current = router.currentRoute.value
    if (!(DOC_DEEP_LINK_QUERY in current.query)) return
    const query = { ...current.query }
    delete query[DOC_DEEP_LINK_QUERY]
    await router.replace({ path: current.path, query, hash: current.hash })
  }

  async function openDocFromQuery(): Promise<void> {
    await router.isReady()
    const raw = router.currentRoute.value.query[DOC_DEEP_LINK_QUERY]
    if (raw === undefined) return
    const docId = (Array.isArray(raw) ? raw[0] : raw)?.trim() ?? ''
    try {
      if (!docId) return
      let projectId: string | null = null
      try {
        const { data } = await getRequest<DocumentProjectRow>(`/api/v1/documents/${encodeURIComponent(docId)}`)
        projectId = typeof data?.project_id === 'string' && data.project_id ? data.project_id : null
      } catch (error) {
        const forbidden = normalizeApiError(error).status === 403
        showToast(t(forbidden ? 'error.forbidden' : 'main.overview.navigation_failed'), 'warning')
        return
      }
      if (projectId && projectId !== projectStore.currentProjectId) projectStore.setCurrentProject(projectId)
      await openDashboardTarget({ kind: 'document', doc_id: docId })
    } finally {
      await dropDocQuery()
    }
  }

  return { openDocFromQuery }
}
