<template>
  <!-- flowgate.default.0549 T0008: TS = structured test specification, TSR = test report.
       A legacy executable TS/TSR (no test_contract_version marker) is not re-interpreted:
       it keeps the ordinary Markdown body, which is handed in as the default slot. The
       canonical body stays the document file either way — the "source" view below is that
       same file, so revision/review/audit/export are untouched. -->
  <div v-if="structured" class="card test-doc-card" data-testid="test-document-body">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon :name="view?.kind === 'TSR' ? 'clipboard-text' : 'list-checks'" style="color:var(--primary);" />
        {{ view?.kind === 'TSR' ? t('main.test_document.report_title') : t('main.test_document.spec_title') }}
        <span class="badge badge-gray test-doc-contract">{{ t('main.test_document.contract_label', { version: 2 }) }}</span>
      </span>
      <div class="test-doc-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          class="btn btn-sm"
          :class="mode === 'structured' ? 'btn-primary' : 'btn-secondary'"
          :aria-selected="mode === 'structured'"
          data-testid="test-doc-view-structured"
          @click="mode = 'structured'"
        >
          {{ view?.kind === 'TSR' ? t('main.test_document.view_report') : t('main.test_document.view_spec') }}
        </button>
        <button
          type="button"
          role="tab"
          class="btn btn-sm"
          :class="mode === 'source' ? 'btn-primary' : 'btn-secondary'"
          :aria-selected="mode === 'source'"
          data-testid="test-doc-view-source"
          @click="mode = 'source'"
        >
          {{ t('main.test_document.view_source') }}
        </button>
      </div>
    </div>
    <div v-if="mode === 'structured'" class="card-bd pad">
      <TestSpecPanel
        v-if="view?.kind === 'TS'"
        :view="view"
        :doc-id="tab.id"
        :read-only="readOnly"
        :can-edit="canEdit"
        @changed="reload"
      />
      <TestReportPanel v-else-if="view?.kind === 'TSR'" :view="view" />
    </div>
  </div>
  <div v-if="structured && mode === 'source'" class="test-doc-source">
    <slot />
  </div>
  <!-- Until the contract is known the Markdown body is mounted but hidden: a legacy
       document then appears with no flash and no remount, and a contract-2 document swaps
       it for the structured view without ever showing raw Markdown first. -->
  <div v-else-if="!structured" v-show="!loading" class="test-doc-source" data-testid="test-doc-markdown">
    <slot />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest } from '@shared/api'

import type { Tab } from '../../stores/tabs'
import type { TestDocumentView } from '../../types/testRun'
import TestReportPanel from './TestReportPanel.vue'
import TestSpecPanel from './TestSpecPanel.vue'

const props = defineProps<{
  tab: Tab
  readOnly: boolean
  canEdit: boolean
}>()

const { t } = useI18n()

const view = ref<TestDocumentView | null>(null)
const loading = ref(true)
const mode = ref<'structured' | 'source'>('structured')
let generation = 0
let pollTimer: ReturnType<typeof setInterval> | null = null

/** Contract-2 documents, plus an empty TS that the user may start as a specification. */
const structured = computed(() => {
  const v = view.value
  if (!v) return false
  if (v.contract_version === 2) return true
  return v.kind === 'TS' && !!v.can_start_spec && props.canEdit && !props.readOnly
})

async function reload() {
  const current = ++generation
  const docId = props.tab.id
  try {
    const res = await getRequest<TestDocumentView>(
      `/api/v1/documents/${encodeURIComponent(docId)}/test-document`,
    )
    // A late answer for a previous tab (or a superseded reload) must not overwrite the
    // current one — generation guard, same idiom as the other async document views.
    if (current !== generation || docId !== props.tab.id) return
    view.value = (res.data as TestDocumentView) ?? null
  } catch {
    if (current !== generation) return
    // Fail open to the Markdown body: the canonical file is always readable there.
    view.value = null
  } finally {
    if (current === generation) loading.value = false
  }
}

function onOpenDocsRefresh(event: Event) {
  const detail = (event as CustomEvent).detail ?? {}
  const project = detail.project ?? null
  if (project && props.tab.projectId && project !== props.tab.projectId) return
  const docId = detail.doc_id ?? null
  if (docId && docId !== props.tab.id && docId !== view.value?.target_ts) return
  void reload()
}

watch(
  () => props.tab.id,
  () => {
    loading.value = true
    view.value = null
    mode.value = 'structured'
    void reload()
  },
)

onMounted(() => {
  void reload()
  window.addEventListener('fg:open_docs_refresh', onOpenDocsRefresh)
  pollTimer = setInterval(() => {
    if (view.value?.active_run) void reload()
  }, 2000)
})

onBeforeUnmount(() => {
  generation += 1
  if (pollTimer) clearInterval(pollTimer)
  window.removeEventListener('fg:open_docs_refresh', onOpenDocsRefresh)
})

defineExpose({ reload })
</script>

<style scoped>
.test-doc-card { margin-bottom: 16px; }
.test-doc-tabs { display: flex; gap: 6px; }
.test-doc-contract { margin-left: 8px; font-weight: 500; }
.test-doc-muted { color: var(--text-m); font-size: .8rem; }
</style>
