<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="close">
      <div class="modal-box modal-nad">

        <!-- ── Header ── -->
        <div class="modal-hd">
          <div>
            <div class="modal-title">
              <i class="fa-solid fa-arrow-right" style="color:var(--primary); margin-right:6px;"></i>
              {{ t('main.next_action_modal.title') }}
            </div>
            <div class="nad-subtitle">
              {{ t('main.next_action_modal.subtitle', { code: nextTypeCode || '—', label: nextTypeLabel }) }}
            </div>
          </div>
          <button class="modal-close" type="button" @click="close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd nad-body">

          <!-- Section 1: Issue info card -->
          <div>
            <div class="nad-section-title">
              <i class="fa-solid fa-circle-info" style="color:var(--primary);"></i>{{ t('main.next_action_modal.section_info') }}
            </div>
            <div class="nad-target-card">
              <span v-if="nextTypeCode" class="nad-type-badge" :class="`c-${nextTypeCode}`">{{ nextTypeCode }}</span>
              <span class="nad-type-label">{{ t('main.next_action_modal.issue_label', { label: nextTypeLabel }) }}</span>
              <span class="nad-return-label">{{ t('main.next_action_modal.ref_return_r') }}</span>
              <span class="nad-ref-info">
                <span class="doc-tag c-R" style="font-size:.6rem; padding:1px 5px; vertical-align:middle;">R</span>
                <span style="margin-left:4px; font-family:'JetBrains Mono',monospace;">{{ normalizedDocRef || '—' }}</span>
              </span>
            </div>
          </div>

          <!-- Section 2: Selected reference docs -->
          <div>
            <div class="nad-section-title">
              <i class="fa-solid fa-book-open" style="color:var(--primary);"></i>
              {{ t('main.next_action_modal.section_selected_refs') }}
              <span class="nad-sel-count">{{ totalSelectedCount }}</span>
            </div>
            <div class="nad-selected-area">
              <span v-if="props.docRef" class="nad-sel-tag tag-locked">
                <i class="fa-solid fa-lock tag-lock-icon"></i>
                <span class="tag-locked-hint">{{ t('main.next_action_modal.locked_doc_hint') }}</span>
                {{ normalizedDocRef }}
              </span>
              <span
                v-for="docPath in extraSelectedDocs"
                :key="docPath"
                class="nad-sel-tag tag-extra"
              >
                {{ docPath }}
                <span class="tag-remove" :title="t('main.next_action_modal.remove_selection')" @click="removeSelectedDoc(docPath)">
                  <i class="fa-solid fa-xmark"></i>
                </span>
              </span>
              <span v-if="!props.docRef && extraSelectedDocs.size === 0" class="nad-selected-empty">
                {{ t('main.next_action_modal.no_extra_refs') }}
              </span>
            </div>
          </div>

          <!-- Section 3: Document browser -->
          <div class="nad-browser-section">
            <div class="nad-section-title">
              <i class="fa-solid fa-folder-open" style="color:var(--primary);"></i>
              {{ t('main.next_action_modal.section_browser') }}
            </div>
            <div class="nad-browser-wrap">

              <!-- Module tabs -->
              <div class="nad-module-tabs">
                <div v-if="modulesLoading" class="nad-loading-row">
                  <i class="fa-solid fa-spinner fa-spin"></i>
                </div>
                <div v-else-if="modulesError" class="nad-error-row">
                  <i class="fa-solid fa-triangle-exclamation"></i> {{ t('main.next_action_modal.module_load_failed') }}
                </div>
                <template v-else>
                  <div
                    v-for="mod in modules"
                    :key="mod.module_id"
                    class="nad-module-tab"
                    :class="{ active: currentModule === mod.module_id }"
                    @click="selectModule(mod.module_id)"
                  >
                    {{ moduleDisplayLabel(mod) }}
                  </div>
                </template>
              </div>

              <!-- 2-panel -->
              <div class="nad-browse-panels">

                <!-- Left: group list -->
                <div class="nad-group-panel">
                  <div class="nad-panel-hd">{{ t('main.next_action_modal.group_panel') }}</div>
                  <div class="nad-group-list" ref="groupListEl" @scroll="onGroupListScroll">
                    <div v-if="groupsLoading" class="nad-doc-empty">
                      <i class="fa-solid fa-spinner fa-spin"></i>
                    </div>
                    <div v-else-if="groups.length === 0 && !groupsLoading" class="nad-doc-empty">
                      {{ t('main.next_action_modal.no_groups') }}
                    </div>
                    <template v-else>
                      <div
                        v-for="grp in groups"
                        :key="grp.group_id"
                        class="nad-group-item"
                        :class="{ active: currentGroup === grp.group_id }"
                        @click="selectGroup(grp.group_id)"
                      >
                        <span class="nad-group-id">{{ shortId(grp.group_id) }}</span>
                        <span class="nad-group-name">{{ grp.title || shortId(grp.group_id) }}</span>
                      </div>
                      <div v-if="groupsLoadingMore" class="nad-group-more">
                        <i class="fa-solid fa-spinner fa-spin"></i>
                      </div>
                    </template>
                  </div>
                </div>

                <!-- Right: doc list -->
                <div class="nad-doc-panel">
                  <div class="nad-doc-search-wrap">
                    <i class="fa-solid fa-magnifying-glass nad-doc-search-icon"></i>
                    <input
                      v-model="searchQuery"
                      type="text"
                      class="nad-doc-search"
                      :placeholder="t('main.next_action_modal.search_placeholder')"
                    />
                  </div>
                  <div
                    v-if="filteredDocs.length > 0"
                    class="nad-doc-selectall"
                    :class="{ 'all-selected': allDocsSelected }"
                    @click="toggleSelectAll"
                  >
                    <span class="nad-doc-check"><i class="fa-solid fa-check"></i></span>
                    <span class="nad-doc-selectall-label">
                      {{ allDocsSelected ? t('main.next_action_modal.deselect_all') : t('main.next_action_modal.select_all') }}
                    </span>
                  </div>
                  <div class="nad-doc-list">
                    <div v-if="docsLoading" class="nad-doc-empty">
                      <i class="fa-solid fa-spinner fa-spin"></i>
                    </div>
                    <div v-else-if="filteredDocs.length === 0" class="nad-doc-empty">
                      {{ searchQuery ? t('main.next_action_modal.no_search_results') : t('main.next_action_modal.no_documents') }}
                    </div>
                    <div
                      v-for="doc in filteredDocs"
                      :key="doc.doc_id"
                      class="nad-doc-item"
                      :class="{ selected: isDocSelected(doc.doc_id) }"
                      @click="toggleDoc(doc)"
                    >
                      <span class="nad-doc-check"><i class="fa-solid fa-check"></i></span>
                      <span class="nad-doc-id">{{ shortId(doc.doc_id) }}</span>
                      <span class="nad-doc-title">
                        {{ doc.title }}
                      </span>
                      <span class="nad-doc-type doc-tag" :class="`c-${doc.type_code ?? doc.type}`">
                        {{ doc.type_code ?? doc.type }}
                      </span>
                    </div>
                  </div>
                </div>

              </div>
            </div>
          </div>

        </div><!-- /modal-bd -->

        <!-- ── Footer ── -->
        <div class="modal-ft">
          <button type="button" class="btn btn-ghost" @click="close">{{ t('common.cancel') }}</button>
          <div class="nad-proceed-wrap">
            <div class="nad-proceed-dropdown" :class="{ open: proceedOpen }">
              <!-- TR0005 rev3: reviewer asked why [Create empty doc] was missing here. It had been
                   removed (R0001 #1 / 0048) in favor of the action-bar split button, but the
                   reviewer wants it offered in the proceed dialog too. Restored at the top so
                   the dialog's selected-refs become the new empty doc's context. The
                   create-empty emit is already wired to MainPanel.onNextActionCreateEmpty. -->
              <div class="nad-proceed-item" @click="onProceedAction('create-empty')">
                <i class="fa-regular fa-file" style="width:1.2em;"></i> {{ t('main.next_action_modal.btn_create_empty') }}
              </div>
              <div class="nad-proceed-item" @click="onProceedAction('copy-mention')">
                <i class="fa-regular fa-copy" style="width:1.2em;"></i> {{ t('main.next_action_modal.btn_copy_mention') }}
              </div>
              <div class="nad-proceed-item" @click="onProceedAction('copy-mention-with-message')">
                <i class="fa-regular fa-comment-dots" style="width:1.2em;"></i> {{ t('main.next_action_modal.btn_copy_mention_with_message') }}
              </div>
              <div class="nad-proceed-item" @click="onProceedAction('invoke-command')">
                <i class="fa-solid fa-terminal" style="width:1.2em;"></i> {{ t('main.next_action_modal.btn_invoke') }}
              </div>
            </div>
            <button class="btn btn-primary" type="button" @click.stop="toggleProceed">
              <i class="fa-solid fa-bolt"></i> {{ t('main.next_action_modal.title') }}
              <i
                class="fa-solid"
                :class="proceedOpen ? 'fa-chevron-down' : 'fa-chevron-up'"
                style="font-size:.6rem; margin-left:4px;"
              ></i>
            </button>
          </div>
        </div>

      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import { useDocTypeStore } from '../stores/docTypeStore'
import { formatDocId } from '@shared/utils/docIdFormatter'

interface ModuleItem { module_id: string; title: string }
interface GroupItem  { group_id: string; title?: string }
interface DocItem    { doc_id: string; type_code?: string; type?: string; title: string; seq?: number }

const props = defineProps<{
  visible: boolean
  nextStepLabel: string
  nextTypeCode?: string
  projectId?: string
  groupId?: string
  docRef?: string
  docModule?: string
  initialSelectedDocs?: string[]
  // The current document the user is advancing FROM (the just-completed doc).
  // Always auto-included as a reference (except when it equals the locked R ref).
  currentDocId?: string
  // type_code of the current document — drives which context types get auto-checked.
  currentDocType?: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'invoke-command': [selectedDocs: string[]]
  'copy-mention':   [selectedDocs: string[]]
  'copy-mention-with-message': [selectedDocs: string[]]
  'create-empty':   [selectedDocs: string[]]
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()

// ── state ────────────────────────────────────────────────────────────────────
const modules       = ref<ModuleItem[]>([])
const modulesLoading = ref(false)
const modulesError  = ref(false)

const groups        = ref<GroupItem[]>([])
const groupsLoading = ref(false)
const groupsLoadingMore = ref(false)     // appending the next page via infinite scroll
const groupsTotal   = ref(0)             // server-reported total group count for this module
const groupListEl   = ref<HTMLElement | null>(null)

const docs          = ref<DocItem[]>([])
const docsLoading   = ref(false)

const currentModule = ref('')
const currentGroup  = ref('')
const searchQuery   = ref('')
const extraSelectedDocs = ref<Set<string>>(new Set())
const proceedOpen   = ref(false)
const nextTypeLabel = computed(() => {
  if (props.nextTypeCode === 'none') return t('main.next_action_modal.none_module_label')
  return props.nextTypeCode ? docTypeStore.getLabel(props.nextTypeCode) : props.nextStepLabel
})

// ── computed locked doc info ──────────────────────────────────────────────────
const normalizedDocRef = computed(() =>
  props.docRef ? formatDocId(props.docRef) : ''
)

const lockedDocPath = computed(() => {
  return normalizedDocRef.value || props.groupId || '—'
})

const totalSelectedCount = computed(() => extraSelectedDocs.value.size + (props.docRef ? 1 : 0))

const filteredDocs = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  let filtered = docs.value
  
  // Exclude current document (self)
  if (normalizedDocRef.value) {
    filtered = filtered.filter(d => formatDocId(d.doc_id) !== normalizedDocRef.value)
  }
  
  if (!q) return filtered
  return filtered.filter(
    (d) =>
      d.doc_id.toLowerCase().includes(q) ||
      d.title.toLowerCase().includes(q)
  )
})

// Whether every currently-visible (filtered) doc is selected — drives the
// select-all/deselect-all toggle label and checked state (R0001 #2).
const allDocsSelected = computed(
  () => filteredDocs.value.length > 0 && filteredDocs.value.every((d) => isDocSelected(d.doc_id)),
)

// ── helpers ───────────────────────────────────────────────────────────────────
function shortId(id: string): string {
  return id.split('-').pop() ?? id
}

function isDocSelected(docId: string): boolean {
  return extraSelectedDocs.value.has(formatDocId(docId))
}

function moduleDisplayLabel(mod: ModuleItem): string {
  if (mod.module_id === 'none') {
    return `${mod.module_id} ${t('main.next_action_modal.none_module_label')}`
  }
  return mod.title || mod.module_id
}

// ── API calls ─────────────────────────────────────────────────────────────────
async function fetchModules() {
  if (!props.projectId) return
  modulesLoading.value = true
  modulesError.value = false
  try {
    const res = await getRequest<any>('/api/v1/modules', { project_id: props.projectId })
    const data = res.data as any
    modules.value = data?.modules ?? data?.items ?? []
  } catch {
    modulesError.value = true
    modules.value = []
  } finally {
    modulesLoading.value = false
  }
}

// The groups endpoint is paginated (server caps `limit` at 200, default 100). A
// module with >100 groups returns only its first page, which (a) drops the current
// document's group from the list when it sorts past the first page — so
// `preferredGroupId` fails to match and the modal falls back to the first group —
// and (b) hides those groups from the picker (bug 0145.0001-B).
//
// TR0005 rev1 — reviewer feedback: do NOT eagerly pull every page. A 1000-group
// module must not fetch/render 1000 rows on open. Instead:
//   1. load the first page, then load further pages ONLY until the preferred
//      (current document's) group appears, then stop — so the current group can be
//      pre-selected without walking the whole module;
//   2. the left panel lazily loads the next page as the user scrolls (infinite
//      scroll), so only what is actually viewed gets fetched/rendered;
//   3. after selecting the current group, scroll it into view so the cursor
//      visibly moves to it instead of sitting at the top of the list.
const GROUPS_PAGE_SIZE = 200          // server max per request

async function fetchGroupPage(
  moduleId: string,
  offset: number,
): Promise<{ items: GroupItem[]; total: number }> {
  const res = await getRequest<any>(`/api/v1/modules/${encodeURIComponent(moduleId)}/groups`, {
    project_id: props.projectId,
    limit: GROUPS_PAGE_SIZE,
    offset,
  })
  const data = res.data as any
  const items = (data?.groups ?? data?.items ?? []) as GroupItem[]
  const total = typeof data?.total === 'number' ? data.total : offset + items.length
  return { items, total }
}

async function fetchGroups(moduleId: string, preferredGroupId?: string) {
  if (!props.projectId || !moduleId) return
  groupsLoading.value = true
  groupsLoadingMore.value = false
  groups.value = []
  groupsTotal.value = 0
  docs.value = []
  try {
    // First page only.
    const first = await fetchGroupPage(moduleId, 0)
    groups.value = first.items
    groupsTotal.value = first.total

    // Load further pages ONLY until the preferred group is present. Stops as soon
    // as it is found (bounded by total) — it does not walk the entire module.
    if (preferredGroupId) {
      while (
        groups.value.length < groupsTotal.value &&
        !groups.value.some(g => g.group_id === preferredGroupId)
      ) {
        const next = await fetchGroupPage(moduleId, groups.value.length)
        if (next.items.length === 0) break
        groups.value = [...groups.value, ...next.items]
        groupsTotal.value = next.total
      }
    }

    if (groups.value.length > 0) {
      // Prefer the head document's group; fall back to first group
      const matched = preferredGroupId
        ? groups.value.find(g => g.group_id === preferredGroupId) ?? null
        : null
      currentGroup.value = matched ? matched.group_id : groups.value[0].group_id
      await fetchDocs(currentGroup.value, moduleId)
    }
  } catch {
    groups.value = []
    groupsTotal.value = 0
  } finally {
    groupsLoading.value = false
  }
  // The list is now rendered (groupsLoading is false). Move the visible cursor to
  // the selected group so it appears highlighted in the viewport (reviewer #2).
  if (currentGroup.value) {
    await nextTick()
    scrollActiveGroupIntoView()
  }
}

// Infinite scroll: pull the next page when the user nears the bottom of the group
// list, so a large module loads incrementally instead of all at once.
async function loadMoreGroups() {
  if (groupsLoadingMore.value || groupsLoading.value) return
  if (groups.value.length >= groupsTotal.value) return
  groupsLoadingMore.value = true
  try {
    const next = await fetchGroupPage(currentModule.value, groups.value.length)
    if (next.items.length > 0) {
      const seen = new Set(groups.value.map(g => g.group_id))
      groups.value = [...groups.value, ...next.items.filter(g => !seen.has(g.group_id))]
    }
    groupsTotal.value = next.total
  } catch {
    // Non-fatal: keep the groups already loaded.
  } finally {
    groupsLoadingMore.value = false
  }
}

function onGroupListScroll(e: Event) {
  const el = e.target as HTMLElement
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 48) {
    void loadMoreGroups()
  }
}

// Scroll the active group row toward the middle of the list viewport. Contained to
// the list (never scrolls the modal/page) and independent of offsetParent via rects.
function scrollActiveGroupIntoView() {
  const list = groupListEl.value
  if (!list) return
  const active = list.querySelector('.nad-group-item.active') as HTMLElement | null
  if (!active) return
  const listRect = list.getBoundingClientRect()
  const itemRect = active.getBoundingClientRect()
  const delta = (itemRect.top - listRect.top) - (list.clientHeight - active.clientHeight) / 2
  list.scrollTop = Math.max(0, list.scrollTop + delta)
}

async function fetchDocs(groupId: string, moduleId: string) {
  if (!props.projectId || !groupId) return
  docsLoading.value = true
  docs.value = []
  searchQuery.value = ''
  try {
    const res = await getRequest<any>(
      `/api/v1/modules/${encodeURIComponent(moduleId)}/groups/${encodeURIComponent(groupId)}/documents`,
      { project_id: props.projectId },
    )
    const data = res.data as any
    docs.value = data?.documents ?? data?.items ?? []
  } catch {
    docs.value = []
  } finally {
    docsLoading.value = false
  }
}

// ── interactions ──────────────────────────────────────────────────────────────
async function selectModule(moduleId: string) {
  currentModule.value = moduleId
  currentGroup.value = ''
  await fetchGroups(moduleId)
}

async function selectGroup(groupId: string) {
  currentGroup.value = groupId
  await fetchDocs(groupId, currentModule.value)
}

function toggleDoc(doc: DocItem) {
  const path = formatDocId(doc.doc_id)
  const set = new Set(extraSelectedDocs.value)
  if (set.has(path)) {
    set.delete(path)
  } else {
    set.add(path)
  }
  extraSelectedDocs.value = set
}

function removeSelectedDoc(path: string) {
  const set = new Set(extraSelectedDocs.value)
  set.delete(path)
  extraSelectedDocs.value = set
}

// Select/deselect all currently-visible docs at once. Operates on filteredDocs so
// it respects the active search filter; never touches the locked ref doc (R0001 #2).
function toggleSelectAll() {
  const set = new Set(extraSelectedDocs.value)
  if (allDocsSelected.value) {
    for (const d of filteredDocs.value) set.delete(formatDocId(d.doc_id))
  } else {
    for (const d of filteredDocs.value) set.add(formatDocId(d.doc_id))
  }
  extraSelectedDocs.value = set
}

// §6-B v2 — Per-current-type auto-check. The just-completed doc (self) + the
// parent R (the locked ref) are always context; on top of that, each current
// type pulls a specific set of sibling documents from the same group:
//   - judgement steps (DS / design / N) add memos (M) and questions (Q)
//   - implementation / test steps (T / TS) take only structural inputs (design, T/TR)
//   - high-count types (M, Q) are capped to the latest N by seq (A is dropped —
//     answers are folded into Q).
const CONTEXT_TYPES_BY_CURRENT: Record<string, string[]> = {
  R:  [],
  DS: ['M', 'Q'],
  D:  ['D', 'P', 'L', 'DB', 'DS', 'M', 'Q'],
  P:  ['D', 'P', 'L', 'DB', 'DS', 'M', 'Q'],
  L:  ['D', 'P', 'L', 'DB', 'DS', 'M', 'Q'],
  DB: ['D', 'P', 'L', 'DB', 'DS', 'M', 'Q'],
  T:  ['D', 'P', 'L', 'DB'],
  N:  ['Q', 'M'],
  TS: ['D', 'P', 'L', 'DB', 'T', 'TR'],
  Q:  [],
}
const CAPPED_TYPES = new Set(['M', 'Q'])
const RECENT_CAP = 10

function autoCheckRelatedDocs() {
  const set = new Set(extraSelectedDocs.value)

  // 1. Always include the current document (self) — skip in the R case where it
  //    is the locked ref already.
  const selfPath = props.currentDocId ? formatDocId(props.currentDocId) : ''
  if (selfPath && selfPath !== normalizedDocRef.value) set.add(selfPath)

  // 2. Type-aware sibling context from the current group.
  const wanted = new Set(CONTEXT_TYPES_BY_CURRENT[props.currentDocType ?? ''] ?? [])
  if (wanted.size > 0) {
    // newest-first so the per-type cap keeps the most recent documents
    const sorted = [...docs.value].sort((a, b) => (b.seq ?? 0) - (a.seq ?? 0))
    const capCount: Record<string, number> = {}
    for (const doc of sorted) {
      const tc = doc.type_code ?? doc.type ?? ''
      if (!wanted.has(tc)) continue
      const path = formatDocId(doc.doc_id)
      if (path === normalizedDocRef.value) continue
      if (CAPPED_TYPES.has(tc)) {
        capCount[tc] = (capCount[tc] ?? 0) + 1
        if (capCount[tc] > RECENT_CAP) continue
      }
      set.add(path)
    }
  }

  extraSelectedDocs.value = set
}

// R0001 / TR0005 (group 0061) — auto-check the workflow predecessors so the dialog
// VISIBLY shows "previous + the one before it" checked (e.g. T and NR when advancing
// to TR), on top of self + the locked R. The previous fix only merged these on the
// server at token time, so the dialog itself left the 2-previous unchecked (review
// rejection). The predecessors come from the server endpoint that reuses the SAME
// helper the token path uses (get_predecessor_result_doc_ids) — so the dialog's checked
// set matches exactly what the worker will receive, with no client/server drift.
async function autoCheckPredecessors() {
  if (!props.docRef) return
  let predIds: string[] = []
  try {
    const res = await getRequest<any>(
      `/api/v1/documents/${encodeURIComponent(props.docRef)}/predecessors`,
      { limit: 2 },
    )
    predIds = (res.data as any)?.predecessor_doc_ids ?? []
  } catch {
    // Non-fatal: fall back to self + type-aware context already checked.
    return
  }
  if (predIds.length === 0) return
  const set = new Set(extraSelectedDocs.value)
  for (const id of predIds) {
    const path = formatDocId(id)
    if (path === normalizedDocRef.value) continue
    set.add(path)
  }
  extraSelectedDocs.value = set
}

function toggleProceed() {
  proceedOpen.value = !proceedOpen.value
}

function getAllSelectedDocs(): string[] {
  const head = normalizedDocRef.value || lockedDocPath.value
  return [head, ...Array.from(extraSelectedDocs.value).map(formatDocId)]
}

function onProceedAction(action: 'create-empty' | 'copy-mention' | 'copy-mention-with-message' | 'invoke-command') {
  proceedOpen.value = false
  const selected = getAllSelectedDocs()
  emit('update:visible', false)
  if (action === 'invoke-command') emit('invoke-command', selected)
  else if (action === 'copy-mention') emit('copy-mention', selected)
  else if (action === 'copy-mention-with-message') emit('copy-mention-with-message', selected)
  else emit('create-empty', selected)
}

function close() {
  proceedOpen.value = false
  emit('update:visible', false)
}

// ── lifecycle ─────────────────────────────────────────────────────────────────
watch(
  () => props.visible,
  async (val) => {
    if (!val) {
      proceedOpen.value = false
      return
    }
    // Reset state
    extraSelectedDocs.value = new Set((props.initialSelectedDocs ?? []).map(formatDocId))
    searchQuery.value = ''
    modules.value = []
    groups.value = []
    docs.value = []
    currentModule.value = ''
    currentGroup.value = ''
    modulesError.value = false

    await fetchModules()
    if (modules.value.length > 0) {
      // Prefer head document's module; fall back to first module
      const headModule = props.docModule
      const matchedModule = headModule
        ? modules.value.find(m => m.module_id === headModule) ?? null
        : null
      currentModule.value = matchedModule ? matchedModule.module_id : modules.value[0].module_id
      await fetchGroups(currentModule.value, props.groupId || undefined)
    }
    // §6-B: auto-check M / Q / A docs in the same group
    autoCheckRelatedDocs()
    // R0001 / TR0005: auto-check the workflow predecessors (previous + 2-previous)
    // so they show as checked in the dialog, matching the worker's received refs.
    await autoCheckPredecessors()
  },
)
</script>

<style scoped>
/* ── Modal dimensions ── */
.modal-nad {
  width: 680px;
  max-width: 96vw;
  height: 85vh;
  max-height: 85vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.modal-nad .modal-bd {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}
.modal-nad .modal-hd,
.modal-nad .modal-ft {
  flex-shrink: 0;
}

.nad-subtitle {
  font-size: .8rem;
  color: var(--text-s);
  margin-top: 2px;
  font-weight: 400;
}

/* ── Body layout ── */
.nad-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Section title ── */
.nad-section-title {
  font-size: .72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-m);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.nad-section-title::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border);
}

/* ── Issue info card ── */
.nad-target-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  background: var(--surface-h);
  border: 1px solid var(--border);
  border-radius: var(--r);
}
.nad-type-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: .75rem;
  font-weight: 700;
  color: white;
  padding: 3px 8px;
  border-radius: var(--r-sm);
  flex-shrink: 0;
}
.nad-type-label {
  font-size: .85rem;
  font-weight: 600;
  color: var(--text);
}
.nad-return-label {
  font-size: .7rem;
  font-weight: 600;
  color: var(--text-m);
  white-space: nowrap;
  flex-shrink: 0;
  padding: 0 2px;
}
.nad-ref-info {
  font-size: .78rem;
  color: var(--text-s);
  font-family: 'JetBrains Mono', monospace;
}

/* ── Selected area ── */
.nad-selected-area {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-height: 42px;
  padding: 8px 10px;
  background: var(--surface-h);
  border: 1px solid var(--border);
  border-radius: var(--r);
}
.nad-selected-empty {
  font-size: .75rem;
  color: var(--text-m);
  font-style: italic;
}
.nad-sel-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px 3px 10px;
  border-radius: 20px;
  font-size: .75rem;
  font-weight: 600;
  font-family: 'JetBrains Mono', monospace;
  white-space: nowrap;
  user-select: none;
}
.nad-sel-tag.tag-locked {
  background: var(--primary-l);
  color: var(--primary);
  border: 1px solid rgba(37,99,235,.3);
}
.nad-sel-tag.tag-extra {
  background: var(--bg);
  color: var(--text-s);
  border: 1px solid var(--border-d);
}
.tag-lock-icon {
  font-size: .6rem;
  color: var(--primary);
  opacity: .7;
}
.tag-locked-hint {
  font-size: .6rem;
  font-weight: 700;
  letter-spacing: .04em;
  opacity: .65;
  text-transform: uppercase;
}
.tag-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  font-size: .55rem;
  color: var(--text-m);
  cursor: pointer;
  transition: all var(--tr);
  flex-shrink: 0;
}
.tag-remove:hover {
  background: var(--border-d);
  color: var(--danger);
}
.nad-sel-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--primary);
  color: white;
  font-size: .65rem;
  font-weight: 700;
  border-radius: 10px;
  padding: 1px 7px;
  margin-left: 4px;
}

/* ── Browser section (fills remaining space) ── */
.nad-browser-section {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.nad-browser-section .nad-section-title {
  flex-shrink: 0;
}
.nad-browser-wrap {
  border: 1px solid var(--border);
  border-radius: var(--r);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

/* ── Module tabs ── */
.nad-module-tabs {
  display: flex;
  flex-shrink: 0;
  background: var(--surface-h);
  border-bottom: 1px solid var(--border);
}
.nad-module-tab {
  padding: 8px 16px;
  font-size: .78rem;
  font-weight: 600;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  color: var(--text-m);
  transition: color var(--tr), border-color var(--tr), background var(--tr);
  user-select: none;
  white-space: nowrap;
}
.nad-module-tab:hover {
  background: rgba(255,255,255,.04);
  color: var(--text);
}
.nad-module-tab.active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  background: var(--primary-l);
}
.nad-loading-row,
.nad-error-row {
  padding: 8px 16px;
  font-size: .78rem;
  color: var(--text-m);
}
.nad-error-row { color: var(--danger); }

/* ── 2-panel ── */
.nad-browse-panels {
  display: flex;
  align-items: stretch;
  gap: 0;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

/* Left: group panel (~27%) */
.nad-group-panel {
  flex: 0 0 27%;
  border-right: 1px solid var(--border);
  background: var(--surface-h);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.nad-panel-hd {
  padding: 8px 12px;
  font-size: .68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .07em;
  color: var(--text-m);
  border-bottom: 1px solid var(--border);
  background: var(--surface-h);
  flex-shrink: 0;
}
.nad-group-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: scroll;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: var(--border-d) var(--surface-h);
}
.nad-group-list::-webkit-scrollbar { width: 4px; }
.nad-group-list::-webkit-scrollbar-track { background: var(--surface-h); }
.nad-group-list::-webkit-scrollbar-thumb { background: var(--border-d); border-radius: 2px; }

.nad-group-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  font-size: .8rem;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
  transition: background var(--tr);
  user-select: none;
}
.nad-group-item:last-child { border-bottom: none; }
.nad-group-item:hover { background: rgba(255,255,255,.04); }
.nad-group-item.active {
  background: var(--primary-l);
  color: var(--primary);
  font-weight: 600;
}
.nad-group-more {
  padding: 10px;
  text-align: center;
  font-size: .75rem;
  color: var(--text-m);
}
.nad-group-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: .7rem;
  font-weight: 700;
  flex-shrink: 0;
}
.nad-group-name {
  font-size: .78rem;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Right: doc panel */
.nad-doc-panel {
  flex: 1;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.nad-doc-search-wrap {
  position: relative;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.nad-doc-search {
  width: 100%;
  padding: 5px 10px 5px 28px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface-h);
  color: var(--text);
  font-size: .78rem;
  outline: none;
  transition: border-color var(--tr), box-shadow var(--tr);
  box-sizing: border-box;
}
.nad-doc-search:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px rgba(37,99,235,.1);
}
.nad-doc-search-icon {
  position: absolute;
  left: 19px;
  top: 50%;
  transform: translateY(-50%);
  font-size: .7rem;
  color: var(--text-m);
  pointer-events: none;
}
.nad-doc-selectall {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
  transition: background var(--tr);
  flex-shrink: 0;
}
.nad-doc-selectall:hover { background: var(--surface-h); }
.nad-doc-selectall.all-selected .nad-doc-check {
  background: var(--primary);
  border-color: var(--primary);
  color: white;
}
.nad-doc-selectall-label {
  font-size: .74rem;
  font-weight: 600;
  color: var(--text-m);
}

.nad-doc-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: scroll;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: var(--border-d) var(--surface);
}
.nad-doc-list::-webkit-scrollbar { width: 4px; }
.nad-doc-list::-webkit-scrollbar-track { background: var(--surface); }
.nad-doc-list::-webkit-scrollbar-thumb { background: var(--border-d); border-radius: 2px; }

.nad-doc-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background var(--tr);
  user-select: none;
}
.nad-doc-item:last-child { border-bottom: none; }
.nad-doc-item:hover { background: var(--surface-h); }
.nad-doc-item.selected { background: var(--primary-l); }
.nad-doc-item.selected:hover { background: #dbeafe; }

.nad-doc-check {
  width: 16px;
  height: 16px;
  border-radius: 3px;
  border: 1.5px solid var(--border-d);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--tr);
  font-size: .6rem;
  color: transparent;
}
.nad-doc-item.selected .nad-doc-check {
  background: var(--primary);
  border-color: var(--primary);
  color: white;
}
.nad-doc-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: .72rem;
  font-weight: 700;
  color: var(--primary);
  min-width: 46px;
  flex-shrink: 0;
}
.nad-doc-item.selected .nad-doc-id { color: var(--primary); }
.nad-doc-title {
  flex: 1;
  font-size: .8rem;
  color: var(--text-s);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nad-doc-item.selected .nad-doc-title { color: var(--text); }
.nad-doc-type {
  font-size: .65rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  flex-shrink: 0;
  font-family: 'JetBrains Mono', monospace;
}
.nad-doc-empty {
  padding: 24px;
  text-align: center;
  font-size: .78rem;
  color: var(--text-m);
  font-style: italic;
}

/* ── Proceed dropdown ── */
.nad-proceed-wrap {
  position: relative;
}
.nad-proceed-dropdown {
  position: absolute;
  bottom: calc(100% + 6px);
  right: 0;
  background: var(--surface-h);
  border: 1px solid var(--border);
  border-radius: var(--r);
  box-shadow: 0 -4px 16px rgba(0,0,0,.3);
  z-index: 20;
  overflow: hidden;
  min-width: 150px;
  display: none;
}
.nad-proceed-dropdown.open {
  display: block;
  animation: dropup-in .15s ease;
}
@keyframes dropup-in {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
.nad-proceed-item {
  padding: 9px 16px;
  font-size: .82rem;
  cursor: pointer;
  transition: background var(--tr);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.nad-proceed-item:last-child { border-bottom: none; }
.nad-proceed-item:hover {
  background: var(--primary-l);
  color: var(--primary);
}
</style>
