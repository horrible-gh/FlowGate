// Global document search (group 0123 R0001 / T0004 — Phase 1).
//
// Backs the first cross-group document search surface. The backend endpoint
// `GET /api/v1/search/documents` does a multi-dialect-safe LOWER(title)/LOWER(doc_id)
// LIKE match with optional project/type/status facets; this composable owns the
// reactive query/results/loading/error state and debounced fetch so any view
// (search panel, command palette, …) can share one source of truth.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'

export interface SearchResultItem {
  doc_id: string
  type: string | null
  title: string | null
  status: string | null
  project_id: string | null
  group_id: string | null
  revision_no?: number
  owner_id?: string | null
  created_at?: string | null
  updated_at?: string | null
  // Phase 2 (content search) only: body excerpt + which field matched.
  snippet?: string | null
  matched_in?: 'body' | 'title' | 'doc_id' | null
  // 0351 T4 — distinguishes a document-body hit from a conversation-turn hit once the
  // backend merges the two into one result list. Absent on Phase 1 (meta) results.
  match_kind?: 'document_body' | 'conversation_turn' | null
  // conversation_turn only: which turn matched, so the result can open the chat at
  // that exact position and show who said it.
  seq?: number | null
  speaker?: 'user' | 'ai' | null
  display_name?: string | null
}

export interface SearchFacets {
  project?: string
  type?: string
  status?: string
}

// 'meta' → Phase 1 title/doc_id endpoint; 'content' → Phase 2 full body search.
export type SearchMode = 'meta' | 'content'

const ENDPOINTS: Record<SearchMode, string> = {
  meta: '/api/v1/search/documents',
  content: '/api/v1/search/documents/content',
}

export interface SearchResponse {
  ok: boolean
  query: string
  total: number
  offset: number
  limit: number
  items: SearchResultItem[]
}

export function useDocumentSearch() {
  const { t } = useI18n()

  const query = ref('')
  const results = ref<SearchResultItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref('')
  const searched = ref(false)

  async function search(
    facets: SearchFacets = {},
    limit = 50,
    offset = 0,
    mode: SearchMode = 'meta',
  ): Promise<boolean> {
    const q = query.value.trim()
    // Empty query is not an error — just clear results (mirrors the backend 400 guard
    // without round-tripping for a blank box).
    if (!q) {
      results.value = []
      total.value = 0
      searched.value = false
      error.value = ''
      return false
    }
    loading.value = true
    error.value = ''
    try {
      const params: Record<string, unknown> = { q, limit, offset }
      if (facets.project) params.project = facets.project
      if (facets.type) params.type = facets.type
      if (facets.status) params.status = facets.status
      const res = await getRequest<SearchResponse>(ENDPOINTS[mode], params)
      results.value = res.data?.items ?? []
      total.value = res.data?.total ?? 0
      searched.value = true
      return true
    } catch (e: any) {
      error.value = e?.response?.data?.error_message ?? t('main.search.error')
      results.value = []
      total.value = 0
      searched.value = true
      return false
    } finally {
      loading.value = false
    }
  }

  function reset() {
    query.value = ''
    results.value = []
    total.value = 0
    error.value = ''
    searched.value = false
  }

  return { query, results, total, loading, error, searched, search, reset }
}
