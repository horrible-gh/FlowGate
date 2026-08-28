<template>
  <!-- flowgate.default.0467 R0001 — [단계별 확인]. Sits between [첨부 파일] and [문서 내용
       미리보기] (MainPanel.vue's AttachmentCard mount), using the exact accordion idiom
       AttachmentCard already established (card-hd-toggle title row, caret rotate -90°, 0.18s
       transition, collapsed-by-default) — no new collapse mechanism was created. Each section
       is itself a nested accordion, collapsed by default, per R0001 rule 3. This card only
       renders the document's own `## 단계별 확인` body section — there is no separate upload/
       edit surface here, the same way `## 변경 파일` has none; the section is authored as part
       of the document body. -->
  <section class="card step-verify-card" :class="{ collapsed }">
    <div class="card-hd">
      <button
        class="card-hd-toggle"
        type="button"
        :aria-expanded="!collapsed"
        :aria-controls="bodyId"
        :title="collapsed ? t('main.step_verification_card.expand') : t('main.step_verification_card.collapse')"
        @click="collapsed = !collapsed"
      >
        <span class="card-title">
          <AppIcon name="list-checks" style="color:var(--text-m);" />
          {{ t('main.step_verification_card.title') }}
        </span>
        <AppIcon name="caret-down" class="card-hd-caret" />
        <span class="step-verify-fold-summary">{{ foldSummary }}</span>
      </button>
      <div v-if="status === 'sections'" class="card-actions">
        <span class="step-verify-count-pill">
          {{ t('main.step_verification_card.count', { count: sections.length }) }}
        </span>
      </div>
    </div>

    <div :id="bodyId" class="card-bd step-verify-card-bd">
      <p v-if="status === 'loading'" class="step-verify-empty-note">
        {{ t('main.step_verification_card.loading') }}
      </p>
      <p v-else-if="status === 'missing'" class="step-verify-empty-note">
        {{ t('main.step_verification_card.not_applicable') }}
      </p>
      <p v-else-if="status === 'error'" class="step-verify-empty-note step-verify-error-note">
        <AppIcon name="warning" />
        {{ t('main.step_verification_card.fetch_error') }}
      </p>
      <p v-else-if="status === 'none'" class="step-verify-empty-note">
        {{ t('main.step_verification_card.declared_none') }}
      </p>
      <ul v-else class="step-verify-sections">
        <li
          v-for="(section, idx) in sections"
          :key="idx"
          class="step-verify-section"
          :class="{ collapsed: sectionCollapsed[idx] }"
        >
          <button
            class="step-verify-section-hd"
            type="button"
            :aria-expanded="!sectionCollapsed[idx]"
            @click="toggleSection(idx)"
          >
            <AppIcon name="caret-down" class="step-verify-section-caret" />
            <span class="step-verify-section-title">{{ section.title }}</span>
            <span class="step-verify-section-summary">{{ section.summary }}</span>
          </button>
          <div class="step-verify-section-bd">
            <ol class="step-verify-steps">
              <li v-for="(step, sIdx) in section.steps" :key="sIdx" class="step-verify-step">
                <p class="step-verify-step-desc">{{ step.description }}</p>
                <ul class="step-verify-expectations">
                  <li v-for="(exp, eIdx) in step.expectations" :key="eIdx">{{ exp }}</li>
                </ul>
              </li>
            </ol>
          </div>
        </li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest } from '@shared/api'

/** flowgate.default.0467 R0001 shape — one document body section, own steps/expectations. */
export interface StepVerificationStep {
  description: string
  expectations: string[]
}

export interface StepVerificationSection {
  title: string
  summary: string
  steps: StepVerificationStep[]
}

const props = defineProps<{
  docId: string
}>()

const { t } = useI18n()

type StepVerificationStatus = 'loading' | 'sections' | 'none' | 'missing' | 'error'

const status = ref<StepVerificationStatus>('loading')
const sections = ref<StepVerificationSection[]>([])
const collapsed = ref(true)
const sectionCollapsed = ref<boolean[]>([])
const bodyId = computed(() => `step-verify-body-${props.docId.replace(/[^A-Za-z0-9_-]/g, '-')}`)

const foldSummary = computed(() => {
  if (status.value === 'loading') return t('main.step_verification_card.loading')
  if (status.value === 'missing') return t('main.step_verification_card.fold_summary_missing')
  if (status.value === 'error') return t('main.step_verification_card.fold_summary_error')
  if (status.value === 'none') return t('main.step_verification_card.fold_summary_empty')
  const list = sections.value
  if (list.length === 1) return t('main.step_verification_card.fold_summary_one', { title: list[0].title })
  return t('main.step_verification_card.fold_summary_many', {
    title: list[0].title,
    rest: list.length - 1,
  })
})

function apiBase(): string {
  return `/api/v1/documents/${encodeURIComponent(props.docId)}/step-verification`
}

function toggleSection(idx: number) {
  sectionCollapsed.value = sectionCollapsed.value.map((v, i) => (i === idx ? !v : v))
}

let requestSeq = 0

/** Read-only: renders whatever the document body's `## 단계별 확인` section already says. */
async function fetchData() {
  const seq = ++requestSeq
  status.value = 'loading'
  sections.value = []
  sectionCollapsed.value = []

  try {
    const res = await getRequest<any>(apiBase())
    if (seq !== requestSeq) return
    const data = (res.data as any)?.data ?? res.data
    sections.value = (data?.sections ?? []) as StepVerificationSection[]
    sectionCollapsed.value = sections.value.map(() => true)
    if (data?.found && !data?.declared_none && sections.value.length > 0) {
      status.value = 'sections'
    } else if (data?.found && data?.declared_none) {
      status.value = 'none'
    } else {
      status.value = 'missing'
    }
  } catch {
    if (seq !== requestSeq) return
    status.value = 'error'
  }
}

watch(() => props.docId, () => { void fetchData() }, { immediate: true })

defineExpose({ fetchData, collapsed })
</script>

<style scoped>
/* Accordion rules copied from AttachmentCard.vue verbatim (same card idiom, D0010 6-3). */
.step-verify-card { margin-bottom: 14px; }

.card-hd-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  margin: -14px 0 -14px -18px;
  padding: 14px 10px 14px 18px;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.card-hd-toggle:hover .card-title { color: var(--primary); }
.card-hd-toggle:focus-visible { outline: 2px solid var(--info); outline-offset: -2px; }
.card-hd-caret {
  margin-left: 2px;
  color: var(--text-m);
  font-size: .7rem;
  transition: transform .18s ease;
}
.step-verify-card.collapsed .card-hd-caret { transform: rotate(-90deg); }
.step-verify-card.collapsed .card-bd { display: none; }
@media (prefers-reduced-motion: reduce) {
  .card-hd-caret, .step-verify-section-caret { transition-duration: .1s; }
}

.step-verify-fold-summary {
  display: none;
  overflow: hidden;
  color: var(--text-m);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: .72rem;
}
.step-verify-card.collapsed .step-verify-fold-summary { display: inline; }

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.step-verify-count-pill {
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-s);
  background: var(--surface-h);
  font-size: .68rem;
  font-weight: 700;
}

.step-verify-card-bd { padding: 0; }

.step-verify-empty-note {
  margin: 0;
  padding: 10px 0;
  color: var(--text-m);
  font-size: .72rem;
  text-align: center;
}

.step-verify-sections {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.step-verify-section {
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  overflow: hidden;
}
.step-verify-section-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.step-verify-section-hd:hover .step-verify-section-title { color: var(--primary); }
.step-verify-section-caret {
  flex: 0 0 auto;
  color: var(--text-m);
  font-size: .68rem;
  transition: transform .18s ease;
}
.step-verify-section.collapsed .step-verify-section-caret { transform: rotate(-90deg); }
.step-verify-section.collapsed .step-verify-section-bd { display: none; }
.step-verify-section-title {
  flex: 0 0 auto;
  color: var(--text);
  font-size: .8rem;
  font-weight: 600;
}
.step-verify-section-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--text-m);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: .74rem;
}
.step-verify-section-bd {
  padding: 4px 12px 10px 30px;
  border-top: 1px solid var(--border);
}
.step-verify-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 8px 0 0;
  padding: 0;
  list-style: decimal;
  list-style-position: inside;
}
.step-verify-step-desc {
  display: inline;
  margin: 0;
  color: var(--text);
  font-size: .78rem;
}
.step-verify-expectations {
  margin: 4px 0 0;
  padding-left: 18px;
  color: var(--text-m);
  font-size: .74rem;
  list-style: '↳ ';
}
</style>
