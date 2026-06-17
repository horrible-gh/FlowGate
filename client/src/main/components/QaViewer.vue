<template>
  <div class="qa-viewer">
    <!-- Loading / error state -->
    <div v-if="loading" style="padding:32px; text-align:center; opacity:.6; font-size:.8rem;">Loading...</div>
    <div v-else-if="fetchError" style="padding:16px; color:var(--danger); font-size:.8rem;">{{ fetchError }}</div>

    <template v-else-if="doc">
      <!-- Top: Q status badge + doc_id -->
      <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
        <span class="qa-status-badge" :class="statusBadgeClass">
          <i :class="statusIcon"></i> {{ statusLabel }}
        </span>
        <span style="font-size:.75rem; font-weight:700; color:var(--text-m);">{{ doc.doc_id }}</span>
        <span v-if="doc.target_id" style="font-size:.75rem; color:var(--text-m);">
          ← {{ doc.target_id }}
        </span>
      </div>

      <!-- Query body render -->
      <div class="qa-section">
        <div class="qa-section-title"><i class="fa-solid fa-question-circle"></i> Query Body</div>
        <div class="card" style="padding:0; overflow:hidden;">
          <div class="md-viewer__content" style="padding:14px 16px; font-size:.8rem; line-height:1.8;" v-html="renderedContent" />
        </div>
      </div>

      <!-- Related document links -->
      <div v-if="relatedDocIds.length > 0" class="qa-section">
        <div class="qa-section-title"><i class="fa-solid fa-link"></i> Related Documents</div>
        <div class="qa-related-links">
          <span
            v-for="docId in relatedDocIds"
            :key="docId"
            class="qa-doc-link"
            @click="openRelatedDoc(docId)"
          >
            <i class="fa-solid fa-file-lines" style="font-size:.65rem;"></i>
            {{ docId }}
          </span>
        </div>
      </div>

      <!-- A list (when answered/closed) -->
      <div v-if="answerDocs.length > 0" class="qa-section">
        <div class="qa-section-title"><i class="fa-solid fa-circle-check"></i> Posted Answers</div>
        <div class="qa-answer-list">
          <div
            v-for="aDoc in answerDocs"
            :key="aDoc.doc_id"
            class="qa-answer-item"
            @click="openRelatedDoc(aDoc.doc_id)"
          >
            <span class="qa-answer-badge">A</span>
            <span style="font-weight:600; font-size:.8rem;">{{ aDoc.doc_id }}</span>
            <span v-if="aDoc.title" style="color:var(--text-m); font-size:.78rem; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{{ aDoc.title }}</span>
            <span style="font-size:.72rem; color:var(--text-m);">{{ aDoc.created_at?.slice(0,10) }}</span>
          </div>
        </div>
      </div>

      <!-- Answer editor (when not closed) -->
      <div v-if="doc.status !== 'closed'" class="qa-section">
        <div class="qa-section-title"><i class="fa-solid fa-pen-to-square"></i> Submit Answer</div>
        <AnswerEditor
          :q-doc-id="doc.doc_id"
          :prev-doc-id="doc.target_id ?? null"
          @submitted="onAnswerSubmitted"
        />
      </div>

      <!-- Closed status message -->
      <div v-else style="padding:12px 16px; background:var(--bg); border:1px solid var(--border); border-radius:var(--r); font-size:.8rem; color:var(--text-m);">
        <i class="fa-solid fa-lock" style="margin-right:6px;"></i>This Q document is closed. No additional answers can be submitted.
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import { getRequest } from '@shared/api'
import { useTabsStore, type Tab } from '../stores/tabs'
import { stripFrontmatter } from '@shared/utils/markdown'
import AnswerEditor from './AnswerEditor.vue'

const props = defineProps<{ tab: Tab }>()

const emit = defineEmits<{
  submitted: [result: AnswerResult]
  'answer-advance-needed': [prevDocId: string, aDocId: string]
}>()

const tabsStore = useTabsStore()

interface DocDetail {
  doc_id: string
  title: string
  status: string
  type_code?: string | null
  created_at?: string | null
  owner_id?: string | null
  group_id?: string | null
  project_id?: string | null
  target_id?: string | null
  file_path?: string | null
}

interface AnswerDocItem {
  doc_id: string
  title?: string | null
  status?: string | null
  created_at?: string | null
}

interface AnswerResult {
  ok: boolean
  a_doc_id: string
  dispatch_mode: string
}

const doc = ref<DocDetail | null>(null)
const content = ref('')
const loading = ref(false)
const fetchError = ref('')
const answerDocs = ref<AnswerDocItem[]>([])

const renderedContent = computed(() =>
  marked.parse(stripFrontmatter(content.value || '')) as string
)

const relatedDocIds = computed((): string[] => {
  const ids = new Set<string>()
  // Parse ## Related Documents section from body
  const lines = content.value.split('\n')
  let inRelatedSection = false
  for (const line of lines) {
    if (line.startsWith('## Related Documents')) {
      inRelatedSection = true
      continue
    }
    if (inRelatedSection && line.startsWith('## ')) break
    if (inRelatedSection) {
      const m = line.match(/[-*]\s+([A-Z]{1,5}\d+)/)
      if (m) ids.add(m[1])
    }
  }
  return Array.from(ids)
})

const statusBadgeClass = computed(() => {
  const s = doc.value?.status
  if (s === 'open') return 'qa-status-open'
  if (s === 'answered') return 'qa-status-answered'
  if (s === 'closed') return 'qa-status-closed'
  return 'qa-status-open'
})

const statusIcon = computed(() => {
  const s = doc.value?.status
  if (s === 'open') return 'fa-solid fa-circle-dot'
  if (s === 'answered') return 'fa-solid fa-circle-check'
  if (s === 'closed') return 'fa-solid fa-lock'
  return 'fa-solid fa-circle-dot'
})

const statusLabel = computed(() => {
  const s = doc.value?.status
  if (s === 'open') return 'open'
  if (s === 'answered') return 'answered'
  if (s === 'closed') return 'closed'
  return s ?? 'open'
})

async function fetchDoc(id: string) {
  loading.value = true
  fetchError.value = ''
  doc.value = null
  content.value = ''
  answerDocs.value = []
  try {
    const [docRes, contentRes] = await Promise.all([
      getRequest<DocDetail>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(id)}`),
      getRequest<{ content: string }>(`/api/v1/documents/content?doc_id=${encodeURIComponent(id)}`),
    ])
    doc.value = (docRes.data as any)?.data ?? docRes.data
    content.value = (contentRes.data as any)?.content ?? ''

    if (doc.value?.group_id) {
      fetchAnswerDocs(doc.value.group_id, id)
    }
  } catch (e: any) {
    fetchError.value = e?.response?.data?.detail ?? 'Unable to load Q document.'
  } finally {
    loading.value = false
  }
}

async function fetchAnswerDocs(groupId: string, qDocId: string) {
  try {
    const res = await getRequest<any>(`/api/v1/list/groups/${encodeURIComponent(groupId)}/documents`, {
      type: 'A',
      limit: 50,
    })
    const items: any[] = (res.data as any)?.items ?? []
    // Filter A docs triggered by (or following) this Q doc
    // Items with triggered_by === qDocId, or fallback: all A docs
    const linked = items.filter((d: any) => d.triggered_by === qDocId)
    answerDocs.value = (linked.length > 0 ? linked : items).map((d: any) => ({
      doc_id: d.doc_id,
      title: d.title,
      status: d.status,
      created_at: d.created_at,
    }))
  } catch { /* silently ignore — A list is supplementary */ }
}

async function openRelatedDoc(docId: string) {
  try {
    const res = await getRequest<DocDetail>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(docId)}`)
    const d: DocDetail = (res.data as any)?.data ?? res.data
    tabsStore.openTab({
      id: d.doc_id,
      title: d.title,
      path: d.file_path ?? '',
      type: 'md',
      mdPath: d.file_path ?? null,
      typeCode: d.type_code ?? null,
    })
  } catch { /* ignore */ }
}

function onAnswerSubmitted(result: AnswerResult) {
  // Update Q status to 'answered' locally
  if (doc.value) {
    doc.value = { ...doc.value, status: 'answered' }
  }
  // Refresh answer doc list
  if (doc.value?.group_id) {
    fetchAnswerDocs(doc.value.group_id, props.tab.id)
  }
  // Also: request to open NextActionModal for the original directive document
  const prevDocId = doc.value?.target_id ?? null
  if (prevDocId) {
    emit('answer-advance-needed', prevDocId, result.a_doc_id)
  }
}

onMounted(() => fetchDoc(props.tab.id))
watch(() => props.tab.id, fetchDoc)

defineExpose({ doc, loading })
</script>
