<template>
  <div class="special-body" data-test="work-plan-body" :data-state="state">
    <div v-if="state === 'loading'" class="special-note" data-test="work-plan-loading">{{ t(k('viewer.loading')) }}</div>
    <slot v-else-if="state === 'fallback'" name="fallback" />
    <div v-else-if="state === 'unreadable'" class="wp-unreadable" data-test="work-plan-unreadable">
      <div class="special-note special-note--warning"><span aria-hidden="true">⚠</span>{{ t(k('work_plan.unreadable')) }}</div>
      <pre class="wp-raw" data-test="work-plan-raw">{{ raw }}</pre>
    </div>
    <div v-else-if="plan" class="wp-sheet" role="region" :aria-label="t(k('work_plan.label'))" data-test="work-plan-sheet">
      <div class="wp-tiles" data-test="work-plan-totals">
        <div v-for="tile in tiles" :key="tile.key" class="wp-tile" :data-test="`work-plan-total-${tile.key}`">
          <span class="wp-tile-label">{{ tile.label }}</span>
          <span class="wp-tile-value">{{ tile.value }}</span>
        </div>
      </div>

      <section v-if="plan.quantities.length" class="wp-section">
        <h4 class="wp-heading">{{ t(k('work_plan.quantities')) }}</h4>
        <ul class="wp-quantities">
          <li
            v-for="box in plan.quantities"
            :key="box.type"
            class="wp-quantity"
            :class="{ 'wp-quantity--zero': box.count === 0 }"
            data-test="work-plan-quantity"
            :data-type="box.type"
            :data-count="box.count"
          >
            <span class="wp-quantity-name">{{ t(k(workPlanQuantityNameKey(box))) }}</span>
            <span class="wp-quantity-value">{{ box.count }}</span>
            <span v-if="box.unit" class="wp-quantity-unit">{{ t(k(`work_plan.unit.${box.unit}`)) }}</span>
          </li>
        </ul>
      </section>

      <section v-if="cards.length" class="wp-section">
        <h4 class="wp-heading">{{ t(k('work_plan.assignments')) }}</h4>
        <ol class="wp-cards">
          <li
            v-for="card in cards"
            :key="card.cardId"
            class="wp-card"
            :class="{ 'wp-card--started': card.started }"
            data-test="work-plan-card"
            :data-card="card.cardId"
            :data-started="card.started ? 'true' : 'false'"
          >
            <div
              v-for="row in card.rows"
              :key="row.key"
              class="wp-row"
              :class="{ 'wp-row--result': row.result, 'wp-row--locked': row.locked }"
              data-test="work-plan-step"
              :data-step="row.key"
            >
              <span class="wp-no" :title="t(k('work_plan.step_no'), { n: row.no })">{{ row.no }}</span>
              <span class="wp-type" data-test="work-plan-step-type">
                {{ t(k(documentTypeNameKey(row.type))) }}
                <small v-if="row.place" class="wp-place">{{ t(k(row.place.key), { ...row.place }) }}</small>
              </span>
              <span
                class="wp-provider"
                :class="{ 'wp-provider--none': !row.providerName }"
                :title="row.providerName ?? t(k(row.locked ? 'work_plan.provider_server' : 'work_plan.provider_none'))"
                data-test="work-plan-step-provider"
              >{{ row.providerName ?? '—' }}</span>
              <span
                v-if="row.reviewCount !== 0"
                class="wp-mark"
                :title="row.reviewCount < 0 ? t(k('work_plan.review_until_approved')) : t(k('work_plan.review'), { n: row.reviewCount })"
                data-test="work-plan-step-review"
              >⟳{{ row.reviewCount < 0 ? '∞' : row.reviewCount }}</span>
              <span v-if="row.note" class="wp-note" :title="row.note" data-test="work-plan-step-note">{{ row.note }}</span>
              <span
                v-if="row.hasInstruction"
                class="wp-mark"
                :title="t(k('work_plan.instruction'))"
                data-test="work-plan-step-instruction"
              >≡</span>
            </div>
            <span
              v-if="card.started"
              class="wp-started"
              role="img"
              :aria-label="t(k('work_plan.started'))"
              :title="t(k('work_plan.started'))"
              data-test="work-plan-card-started"
            />
          </li>
        </ol>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0053 T#4 — the work plan's view in the document window: the v0.1 WorkPlanEditor's table
// (client/src/main/components/WorkPlanEditor.vue — the totals cards, ① the quantity boxes, ② one row per
// Step grouped by card with its provider, review count, one-line note and pre-instruction), read only. The
// plan is changed with the window's ✎ text editor like every document, so none of the editor's controls
// (steppers, provider pickers, drawers, card moves, AI fill, upload / download, raw-view dialog) came along.
// Type codes, keys and explanation paragraphs are not shown: a type reads by its name, a state by a mark
// with the word in its tooltip.
// Read through the v0.1 endpoint (`GET /documents/{doc_id}/work-plan`, workPlanDocumentRead). A plan the
// server cannot read as a table (409 wp_unreadable) shows one short line and its raw text; any other failure
// hands the document to the MD Viewer (the fallback slot). A read that fails while a table is shown keeps it.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { documentContentRead, workPlanDocumentRead, workPlanErrorOf, type WorkPlanDocument } from '@/features/document'
import { documentTypeNameKey, workPlanQuantityNameKey, workPlanSheetCards } from './specialDocumentModel'

const props = defineProps<{
  docId: string
}>()

const { t } = useI18n()
const k = (key: string) => `gui.document_window.${key}`

const plan = ref<WorkPlanDocument | null>(null)
const state = ref<'loading' | 'ready' | 'unreadable' | 'fallback'>('loading')
const raw = ref('')
// Request order: only the latest read may set the view.
let generation = 0

async function readRaw(docId: string, seq: number): Promise<void> {
  try {
    const read = await documentContentRead(docId)
    if (seq === generation) raw.value = read.content
  } catch {
    if (seq === generation) raw.value = ''
  }
}

async function reload(): Promise<void> {
  const seq = ++generation
  const docId = props.docId
  try {
    const read = await workPlanDocumentRead(docId)
    if (seq !== generation) return
    plan.value = read
    state.value = 'ready'
  } catch (error) {
    if (seq !== generation) return
    if (workPlanErrorOf(error).kind === 'unreadable') {
      plan.value = null
      state.value = 'unreadable'
      await readRaw(docId, seq)
    } else if (plan.value === null) {
      state.value = 'fallback'
    }
  }
}

const cards = computed(() => workPlanSheetCards(plan.value))

const tiles = computed(() => {
  const totals = plan.value?.view.totals ?? { designSheets: 0, workSets: 0, steps: 0 }
  return [
    { key: 'design', label: t(k('work_plan.design')), value: t(k('work_plan.total_design'), { n: totals.designSheets }) },
    { key: 'work', label: t(k('work_plan.work')), value: t(k('work_plan.total_work'), { n: totals.workSets }) },
    { key: 'steps', label: t(k('work_plan.steps')), value: t(k('work_plan.total_steps'), { n: totals.steps }) },
  ]
})

watch(
  () => props.docId,
  () => {
    plan.value = null
    raw.value = ''
    state.value = 'loading'
    void reload()
  },
)

onMounted(() => {
  void reload()
})

onBeforeUnmount(() => {
  generation += 1
})

defineExpose({ reload })
</script>

<style scoped>
.special-body {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  box-sizing: border-box;
}

.special-note {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 18px;
  color: var(--fg2-muted);
  font-size: 13px;
}

.special-note--warning {
  color: var(--fg2-status-warning-fg);
}

.wp-raw {
  margin: 0 18px 16px;
  padding: 10px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.wp-sheet {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px 18px;
  color: var(--fg2-text);
  font-size: 12.5px;
}

.wp-tiles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.wp-tile {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-panel);
}

.wp-tile-label {
  color: var(--fg2-muted);
  font-size: 11px;
}

.wp-tile-value {
  font-size: 16px;
  font-weight: 600;
}

.wp-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.wp-heading {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
  font-weight: 600;
}

.wp-quantities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wp-quantity {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
}

.wp-quantity--zero {
  opacity: 0.5;
}

.wp-quantity-value {
  font-weight: 600;
}

.wp-quantity-unit,
.wp-place {
  color: var(--fg2-muted);
  font-size: 11px;
}

.wp-cards {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wp-card {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 4px 22px 4px 8px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
}

.wp-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  min-height: 24px;
}

.wp-row--result {
  padding-left: 14px;
}

.wp-row--locked {
  color: var(--fg2-muted);
}

.wp-no {
  flex: 0 0 auto;
  min-width: 18px;
  color: var(--fg2-muted);
  font-size: 11px;
  text-align: right;
}

.wp-type {
  flex: 0 0 auto;
  font-weight: 600;
}

.wp-provider {
  flex: 0 0 auto;
}

.wp-provider--none {
  color: var(--fg2-muted-2);
}

.wp-mark {
  flex: 0 0 auto;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.wp-note {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wp-started {
  position: absolute;
  top: 10px;
  right: 8px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--fg2-status-info-fg);
}
</style>
