<template>
  <teleport to="body">
    <div
      v-if="visible"
      ref="overlayRef"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div class="modal-box modal-wpc" role="dialog" aria-modal="true" aria-labelledby="wpc-title">
        <div class="modal-hd">
          <div class="modal-title" id="wpc-title">
            <AppIcon name="clipboard-text" class="wpc-title-ico" />
            {{ t('main.work_plan_create_dialog.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <div class="modal-bd wpc-body">
          <p class="wpc-intro" v-html="introHtml"></p>

          <div class="wpc-cols">
            <!-- ① 수량을 확인할 타입 -->
            <section class="wpc-sec">
              <div class="wpc-sec-hd">
                <span class="wpc-sec-no">1</span>
                <span class="wpc-sec-title">{{ t('main.work_plan_create_dialog.section_types_title') }}</span>
                <span class="wpc-count-pill" :class="{ zero: selectedTypes.size === 0 }">
                  {{ selectedTypes.size }} / {{ typesLoaded.length }}
                </span>
                <span class="wpc-sec-acts">
                  <button type="button" class="wpc-mini-btn" @click="selectAllTypes">
                    {{ t('main.work_plan_create_dialog.select_all') }}
                  </button>
                  <button type="button" class="wpc-mini-btn" @click="clearAllTypes">
                    {{ t('main.work_plan_create_dialog.clear_all') }}
                  </button>
                </span>
              </div>

              <div v-if="typesError" class="wpc-load-error">
                {{ t('main.work_plan_create_dialog.types_load_failed') }}
                <button type="button" class="wpc-mini-btn" @click="loadTypes">
                  {{ t('main.work_plan_create_dialog.retry') }}
                </button>
              </div>
              <div v-else class="wpc-scroll">
                <div v-if="designTypes.length" class="wpc-subhead">
                  {{ t('main.work_plan_create_dialog.group_design') }}
                </div>
                <div v-if="designTypes.length" class="wpc-grid">
                  <label
                    v-for="item in designTypes"
                    :key="item.code"
                    class="wpc-check"
                    :class="{ on: selectedTypes.has(item.code) }"
                    @click.prevent="toggleType(item.code)"
                  >
                    <span class="wpc-check-box"><AppIcon name="check" class="wpc-check-ico" /></span>
                    <span class="doc-tag wpc-check-tag" :class="`c-${item.code}`">{{ item.code }}</span>
                    <span class="wpc-check-body">
                      <span class="wpc-check-name">{{ item.label }}</span>
                    </span>
                  </label>
                </div>

                <div v-if="workTypes.length" class="wpc-subhead">
                  {{ t('main.work_plan_create_dialog.group_work') }}
                </div>
                <div v-if="workTypes.length" class="wpc-grid">
                  <label
                    v-for="item in workTypes"
                    :key="item.code"
                    class="wpc-check"
                    :class="{ on: selectedTypes.has(item.code) }"
                    @click.prevent="toggleType(item.code)"
                  >
                    <span class="wpc-check-box"><AppIcon name="check" class="wpc-check-ico" /></span>
                    <span class="wpc-pair">
                      <span class="doc-tag wpc-check-tag" :class="`c-${item.code}`">{{ item.code }}</span>
                      <span
                        v-if="item.pairCode"
                        class="doc-tag wpc-check-tag"
                        :class="`c-${item.pairCode}`"
                      >{{ item.pairCode }}</span>
                    </span>
                    <span class="wpc-check-body">
                      <span class="wpc-check-name">{{ item.setName }}</span>
                    </span>
                  </label>
                </div>
              </div>
            </section>

            <!-- ② 투입할 프로바이더 -->
            <section class="wpc-sec">
              <div class="wpc-sec-hd">
                <span class="wpc-sec-no">2</span>
                <span class="wpc-sec-title">{{ t('main.work_plan_create_dialog.section_providers_title') }}</span>
                <span class="wpc-count-pill" :class="{ zero: selectedProviders.size === 0 }">
                  {{ selectedProviders.size }} / {{ providersLoaded.length }}
                </span>
                <span class="wpc-sec-acts">
                  <button type="button" class="wpc-mini-btn" @click="selectAllProviders">
                    {{ t('main.work_plan_create_dialog.select_all') }}
                  </button>
                  <button type="button" class="wpc-mini-btn" @click="clearAllProviders">
                    {{ t('main.work_plan_create_dialog.clear_all') }}
                  </button>
                </span>
              </div>

              <div v-if="providersError" class="wpc-load-error">
                {{ t('main.work_plan_create_dialog.providers_load_failed') }}
                <button type="button" class="wpc-mini-btn" @click="loadProviders">
                  {{ t('main.work_plan_create_dialog.retry') }}
                </button>
              </div>
              <template v-else>
                <div class="wpc-prov-tools">
                  <label class="wpc-search">
                    <AppIcon name="magnifying-glass" />
                    <input
                      v-model="providerQuery"
                      type="search"
                      :placeholder="t('main.work_plan_create_dialog.provider_search_placeholder')"
                    />
                  </label>
                  <button
                    type="button"
                    class="wpc-mini-btn"
                    :class="{ on: selectedOnly }"
                    @click="selectedOnly = !selectedOnly"
                  >
                    {{ t('main.work_plan_create_dialog.selected_only') }}
                  </button>
                </div>

                <div class="wpc-scroll">
                  <template v-for="group in visibleProviderGroups" :key="group.label">
                    <div class="wpc-subhead wpc-subhead-prov">{{ group.label }}</div>
                    <div class="wpc-grid wpc-prov-list">
                      <label
                        v-for="p in group.items"
                        :key="p.id"
                        class="wpc-check"
                        :class="{ on: selectedProviders.has(p.id) }"
                        @click.prevent="toggleProvider(p.id)"
                      >
                        <span class="wpc-check-box"><AppIcon name="check" class="wpc-check-ico" /></span>
                        <span class="wpc-check-body">
                          <span class="wpc-check-name">{{ p.name }}</span>
                          <span class="wpc-check-sub">{{ p.sub }}</span>
                        </span>
                      </label>
                    </div>
                  </template>
                  <p v-if="visibleProviderGroups.length === 0" class="wpc-empty-hint">
                    {{ t('main.work_plan_create_dialog.no_results') }}
                  </p>
                </div>

                <div class="wpc-picked">
                  <span class="wpc-picked-caption">{{ t('main.work_plan_create_dialog.selected_label') }}</span>
                  <span v-if="selectedProviders.size === 0" class="wpc-empty-hint">
                    {{ t('main.work_plan_create_dialog.no_selection') }}
                  </span>
                  <span v-for="id in selectedProviders" :key="id" class="wpc-chip">
                    {{ providerName(id) }}
                    <button type="button" class="wpc-chip-x" @click="toggleProvider(id)"><AppIcon name="x" /></button>
                  </span>
                </div>
              </template>
            </section>
          </div>

          <div class="wpc-bottom">
            <div class="wpc-preview" :class="{ warn: !!blockReason }">
              <AppIcon :name="blockReason ? 'warning-circle' : 'info'" />
              <span v-if="blockReason">{{ blockReason }}</span>
              <span v-else class="wpc-preview-lines">
                <span v-html="previewLine1Html"></span>
                <span v-html="previewLine2Html"></span>
              </span>
            </div>
            <div class="wpc-usage-note">
              <AppIcon name="lightning" />
              <span v-html="usageNoteHtml"></span>
            </div>
          </div>

          <p v-if="createError" class="wpc-create-error">{{ createError }}</p>
        </div>

        <div class="modal-ft">
          <button type="button" class="btn btn-ghost" :disabled="creating" @click="onClose">
            {{ t('common.cancel') }}
          </button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!!blockReason || creating"
            @click="onCreate"
          >
            <AppIcon name="plus" />
            {{ creating ? t('main.work_plan_create_dialog.creating') : t('main.work_plan_create_dialog.create') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import { useDocTypeStore, type DocTypeItem } from '../stores/docTypeStore'
import { useAiProviderStore } from '../stores/aiProvider'

const props = defineProps<{
  visible: boolean
  parentDocId: string
  projectId: string
  groupId: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'created': [payload: { docId: string; title: string; body: Record<string, unknown> }]
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const aiProviderStore = useAiProviderStore()

const overlayRef = ref<HTMLElement | null>(null)
const typesError = ref(false)
const providersError = ref(false)
const selectedTypes = ref<Set<string>>(new Set())
const selectedProviders = ref<Set<string>>(new Set())
const providerQuery = ref('')
const selectedOnly = ref(false)
const creating = ref(false)
const createError = ref('')

const typesLoaded = computed<DocTypeItem[]>(() => docTypeStore.countableTypes)
const allCountableTypeCodes = computed(() => typesLoaded.value.map((item) => item.code))

interface TypeRow { code: string; label: string; unit: 'sheet' | 'set'; pairCode?: string; setName: string }

const designTypes = computed<TypeRow[]>(() =>
  typesLoaded.value
    .filter((item) => item.unit === 'sheet')
    .map((item) => ({ code: item.code, label: item.label, unit: 'sheet', setName: item.label })),
)
const workTypes = computed<TypeRow[]>(() =>
  typesLoaded.value
    .filter((item) => item.unit === 'set')
    .map((item) => ({
      code: item.code,
      label: item.label,
      unit: 'set',
      pairCode: item.pair_code,
      setName: docTypeStore.getSetName(item.code),
    })),
)

const providersLoaded = computed(() => aiProviderStore.providers)

/**
 * Group by vendor, the way the mockup does — one "OpenAI" box holding both the
 * CLI and the API entries. The exec type is appended to the heading only when
 * every provider in the group shares it (that is what makes "Claude · CLI"),
 * and each row carries its own `kind · EXEC` sub-line.
 */
const visibleProviderGroups = computed(() => {
  const q = providerQuery.value.trim().toLowerCase()
  const groups = new Map<string, { id: string; name: string; sub: string; exec: string }[]>()
  for (const p of providersLoaded.value) {
    if (selectedOnly.value && !selectedProviders.value.has(p.id)) continue
    const kind = (p.kind || '').trim()
    const exec = (p.exec_type || '').trim()
    const vendor = kind ? `${kind.charAt(0).toUpperCase()}${kind.slice(1)}` : (exec || '—')
    if (q && !p.name.toLowerCase().includes(q) && !vendor.toLowerCase().includes(q) && !exec.toLowerCase().includes(q)) continue
    const sub = [kind, exec ? exec.toUpperCase() : ''].filter(Boolean).join(' · ')
    if (!groups.has(vendor)) groups.set(vendor, [])
    groups.get(vendor)!.push({ id: p.id, name: p.name, sub, exec })
  }
  return Array.from(groups.entries()).map(([vendor, items]) => {
    const execs = new Set(items.map((item) => item.exec).filter(Boolean))
    const label = execs.size === 1 ? `${vendor} · ${[...execs][0].toUpperCase()}` : vendor
    return { label, items }
  })
})

function providerName(id: string): string {
  return providersLoaded.value.find((p) => p.id === id)?.name ?? id
}

function toggleType(code: string) {
  const next = new Set(selectedTypes.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  selectedTypes.value = next
}
function selectAllTypes() {
  selectedTypes.value = new Set(typesLoaded.value.map((item) => item.code))
}
function clearAllTypes() {
  selectedTypes.value = new Set()
}
function toggleProvider(id: string) {
  const next = new Set(selectedProviders.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedProviders.value = next
}
function selectAllProviders() {
  selectedProviders.value = new Set(providersLoaded.value.map((p) => p.id))
}
function clearAllProviders() {
  selectedProviders.value = new Set()
}

const planSummary = computed(() => {
  let design = 0
  let work = 0
  for (const item of [...designTypes.value, ...workTypes.value]) {
    if (!selectedTypes.value.has(item.code)) continue
    if (item.unit === 'sheet') design += 1
    else work += 1
  }
  return { design, work }
})

const pickedProviderNames = computed(() =>
  providersLoaded.value.filter((p) => selectedProviders.value.has(p.id)).map((p) => p.name),
)

function bold(value: string | number): string {
  return `<strong>${String(value).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] as string))}</strong>`
}

const introHtml = computed(() =>
  t('main.work_plan_create_dialog.intro', {
    what: bold(t('main.work_plan_create_dialog.intro_what')),
    who: bold(t('main.work_plan_create_dialog.intro_who')),
    later: bold(t('main.work_plan_create_dialog.intro_later')),
  }),
)
const previewLine1Html = computed(() =>
  t('main.work_plan_create_dialog.preview_line1', {
    design: bold(planSummary.value.design),
    work: bold(planSummary.value.work),
  }),
)
const previewLine2Html = computed(() => {
  const names = pickedProviderNames.value
  const head = names.slice(0, 3).join(' / ')
  const rest = names.length - Math.min(3, names.length)
  const list = rest > 0
    ? t('main.work_plan_create_dialog.provider_list_more', { head, rest })
    : head
  return t('main.work_plan_create_dialog.preview_line2', {
    providers: bold(list),
    n: names.length,
    each: bold(t('main.work_plan_create_dialog.preview_each')),
  })
})
const usageNoteHtml = computed(() =>
  t('main.work_plan_create_dialog.advisory_hint', {
    lead: bold(t('main.work_plan_create_dialog.advisory_lead')),
  }),
)

const generatedTitle = computed(() =>
  t('main.work_plan_create_dialog.generated_title', {
    design: planSummary.value.design,
    work: planSummary.value.work,
  }).slice(0, 100),
)

const blockReason = computed<string>(() => {
  if (selectedTypes.value.size === 0) return t('main.work_plan_create_dialog.block_types')
  if (selectedProviders.value.size === 0) return t('main.work_plan_create_dialog.block_providers')
  return ''
})

async function loadTypes() {
  typesError.value = false
  try {
    await docTypeStore.loadLabels()
    if (docTypeStore.countableTypes.length === 0) typesError.value = true
  } catch {
    typesError.value = true
  }
}

async function loadProviders() {
  providersError.value = false
  try {
    await aiProviderStore.loadForProject(props.projectId, true)
    if (aiProviderStore.error) providersError.value = true
  } catch {
    providersError.value = true
  }
}

watch(
  () => props.visible,
  (val) => {
    if (!val) return
    selectedTypes.value = new Set()
    selectedProviders.value = new Set()
    providerQuery.value = ''
    selectedOnly.value = false
    createError.value = ''
    creating.value = false
    void loadTypes()
    void loadProviders()
    setTimeout(() => overlayRef.value?.focus(), 50)
  },
  { immediate: true },
)

function onClose() {
  emit('update:visible', false)
}

async function onCreate() {
  if (blockReason.value || creating.value) return
  creating.value = true
  createError.value = ''
  try {
    const res = await postRequest<{ ok: boolean; doc_id: string; title: string; body: Record<string, unknown> }>(
      '/api/v1/documents/work-plan',
      {
        parent_doc_id: props.parentDocId,
        title: generatedTitle.value.slice(0, 100),
        counted_types: allCountableTypeCodes.value,
        provider_candidates: Array.from(selectedProviders.value),
        // The dialog only picks what to count and who is a candidate; sheet and
        // set counts are decided in the document table (mockup xc32frrg screen 2).
        // flowgate.default.0423 T0005 item 10: a checked type is no longer hardcoded to
        // 1 -- it is simply left out of this map, so the server fills it from the
        // group's workflow_type_counts derivation when one exists, or 0 otherwise
        // (work_plan.py create_work_plan). An unchecked type still forces an explicit 0.
        quantities: Object.fromEntries(
          allCountableTypeCodes.value
            .filter((code) => !selectedTypes.value.has(code))
            .map((code) => [code, 0]),
        ),
        defaults: { provider_id: null, note: '' },
        type_providers: {},
      },
    )
    const data = res.data
    emit('created', { docId: data.doc_id, title: data.title, body: data.body })
    emit('update:visible', false)
  } catch (e: any) {
    const detail = e?.response?.data
    createError.value = detail?.message || detail?.detail || String(e)
  } finally {
    creating.value = false
  }
}
</script>

<style scoped>
/* Layout mirrors mockup xc32frrg screen 2 (작업계획 생성 다이얼로그): two columns
   that each scroll on their own, so a long provider list never pushes the
   footer out of reach. */
.modal-wpc { width: 940px; max-width: 96vw; }
.wpc-title-ico { color: var(--primary, #4f46e5); margin-right: 6px; }
.wpc-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; }
.wpc-intro {
  margin: 0; padding: 10px 12px; font-size: .78rem; line-height: 1.55;
  color: var(--text-s, #475569); background: var(--primary-l, #eff6ff);
  border: 1px solid var(--primary-b, #bfdbfe); border-radius: var(--r, 6px);
}
.wpc-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }
.wpc-sec { display: flex; flex-direction: column; min-width: 0; }
.wpc-sec-hd { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; }
.wpc-sec-no {
  width: 18px; height: 18px; border-radius: 50%; background: var(--primary, #2563eb); color: #fff;
  font-size: .66rem; font-weight: 700; display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.wpc-sec-title { font-size: .82rem; font-weight: 700; color: var(--text, #1e293b); white-space: nowrap; }
.wpc-count-pill {
  font-size: .68rem; font-weight: 700; padding: 1px 7px; border-radius: 999px;
  background: var(--primary-l, #eff6ff); color: var(--primary, #2563eb); font-variant-numeric: tabular-nums;
}
.wpc-count-pill.zero { background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626); }
.wpc-sec-acts { margin-left: auto; display: inline-flex; gap: 4px; }
.wpc-mini-btn {
  padding: 2px 9px; font-size: .68rem; border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r-sm, 4px); background: #fff; color: var(--text-m, #64748b); cursor: pointer;
}
.wpc-mini-btn:hover { background: var(--surface-h, #f8fafc); }
.wpc-mini-btn.on { border-color: var(--primary, #2563eb); color: var(--primary, #2563eb); background: var(--primary-l, #eff6ff); }
.wpc-scroll {
  height: 300px; overflow-y: auto; padding: 10px;
  border: 1px solid var(--border, #e2e8f0); border-radius: var(--r, 6px); background: #fff;
}
.wpc-subhead { font-size: .68rem; color: var(--text-m, #64748b); margin: 2px 0 6px; }
.wpc-subhead-prov { text-transform: uppercase; letter-spacing: .05em; font-weight: 700; margin-top: 8px; }
.wpc-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 8px; }
.wpc-check {
  display: flex; align-items: center; gap: 7px; padding: 7px 9px; min-width: 0;
  border: 1px solid var(--border, #e2e8f0); border-radius: var(--r, 6px);
  cursor: pointer; user-select: none; transition: all .1s;
}
.wpc-check:hover { background: var(--surface-h, #f8fafc); border-color: var(--border-d, #cbd5e1); }
.wpc-check.on { border-color: var(--primary, #2563eb); background: var(--primary-l, #eff6ff); }
.wpc-check-box {
  width: 15px; height: 15px; border: 1.5px solid var(--border-d, #cbd5e1); border-radius: 4px;
  background: #fff; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
}
.wpc-check.on .wpc-check-box { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); }
.wpc-check-ico { font-size: .55rem; color: transparent; }
.wpc-check.on .wpc-check-ico { color: #fff; }
.wpc-pair { display: inline-flex; gap: 3px; flex-shrink: 0; }
.wpc-check-tag { font-size: .62rem; padding: 1px 5px; flex-shrink: 0; }
.wpc-check-body { display: flex; flex-direction: column; min-width: 0; }
.wpc-check-name {
  font-size: .78rem; color: var(--text-s, #475569);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.wpc-check.on .wpc-check-name { color: var(--text, #1e293b); font-weight: 600; }
.wpc-check-sub {
  font-size: .66rem; color: var(--text-m, #94a3b8);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.wpc-prov-tools { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.wpc-search {
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; min-width: 0;
  border: 1px solid var(--border, #e2e8f0); border-radius: var(--r, 6px);
  background: #fff; color: var(--text-m); flex: 1;
}
.wpc-search input { border: none; outline: none; font-size: .75rem; background: transparent; width: 100%; }
.wpc-picked { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin-top: 7px; }
.wpc-picked-caption { font-size: .7rem; color: var(--text-m); }
.wpc-chip {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 6px 2px 9px;
  border-radius: 999px; background: var(--primary-l, #eff6ff); color: var(--primary, #2563eb);
  font-size: .7rem; font-weight: 600;
}
.wpc-chip-x { color: inherit; opacity: .7; padding: 0; }
.wpc-chip-x:hover { opacity: 1; }
.wpc-empty-hint { font-size: .74rem; color: var(--text-m); font-style: italic; margin: 4px 0; }
.wpc-load-error { display: flex; align-items: center; gap: 8px; font-size: .78rem; color: var(--danger, #dc2626); }
.wpc-bottom { display: flex; flex-direction: column; gap: 8px; }
.wpc-preview {
  display: flex; align-items: flex-start; gap: 8px; padding: 9px 11px; font-size: .77rem; line-height: 1.55;
  color: var(--text-s, #475569); background: var(--primary-l, #eff6ff);
  border: 1px solid var(--primary-b, #bfdbfe); border-left: 3px solid var(--primary, #2563eb);
  border-radius: var(--r, 6px);
}
.wpc-preview.warn {
  color: var(--warning, #b45309); background: var(--warning-l, #fef3c7);
  border-color: #fde68a; border-left-color: var(--warning, #b45309);
}
.wpc-preview-lines { display: flex; flex-direction: column; }
.wpc-usage-note {
  display: flex; align-items: flex-start; gap: 8px; padding: 8px 11px; font-size: .75rem; line-height: 1.55;
  color: var(--text-m, #64748b); background: var(--surface-h, #f8fafc);
  border: 1px dashed var(--border-d, #cbd5e1); border-radius: var(--r, 6px);
}
.wpc-create-error { color: var(--danger, #dc2626); font-size: .78rem; margin: 0; }

/* Narrow viewports fall back to one column — the two boxes stack instead of
   squeezing each other into unreadable strips. */
@media (max-width: 900px) {
  .wpc-cols { grid-template-columns: 1fr; }
  .wpc-scroll { height: 220px; }
}
</style>
