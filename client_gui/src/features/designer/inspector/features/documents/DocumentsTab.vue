<template>
  <!-- flowgate.v02.0048 T#5 — documents tab (D#1 §10.2): the selected Node's document as the v0.1
       API gives it — type · seq · title, review status, rejection reason and history, AI review
       existence, the documents it superseded — with the v0.1 sequence slot or the Graph node run
       of the Node. ↗ 열기 opens a document in the v0.1 screen, where writing and editing happen.
       A thin container: useDocuments reads, documentsModel decides what is shown.
       flowgate.v02.0052 묶음 A — no prose line in the body: where writing and editing happen is the
       ↗ 열기 button's hover text (v7 keeps explanations in tooltips). -->
  <div v-if="view" class="inspector-documents" data-test="inspector-documents" :data-status="status">
    <section class="inspector-documents-current" data-test="documents-current" :data-doc="view.current.docId">
      <div class="inspector-documents-line">
        <span v-if="view.current.typeCode" class="inspector-documents-type" data-test="documents-current-type">{{ view.current.typeCode }}</span>
        <span v-if="view.current.seq !== null" class="inspector-documents-seq" data-test="documents-current-seq">#{{ view.current.seq }}</span>
        <span class="inspector-documents-title" :title="view.current.title" data-test="documents-current-title">{{ view.current.title }}</span>
        <Button
          v-if="availability.open.visible"
          size="sm"
          variant="secondary"
          :disabled="!availability.open.enabled"
          :title="t(k('open_hint'))"
          data-test="documents-open"
          @click="open(view.current.docId)"
        >{{ t(k('open')) }}</Button>
      </div>
      <div class="inspector-documents-meta">
        <InspectorStatusChip
          v-if="view.current.reviewStatus"
          :label="reviewText(view.current.reviewStatus)"
          :tone="view.current.reviewTone"
          data-test="documents-current-review"
          :data-value="view.current.reviewStatus"
        />
        <span v-if="view.current.status" data-test="documents-current-status">{{ t(k('doc_status'), { status: view.current.status }) }}</span>
        <span v-if="view.current.revisionNo !== null" data-test="documents-current-revision">{{ t(k('revision'), { n: view.current.revisionNo }) }}</span>
        <span v-if="view.current.hasAiReview" data-test="documents-current-ai-review">{{ t(k('ai_reviewed')) }}</span>
      </div>
      <div v-if="view.current.rejectionReason" data-test="documents-rejection-reason">
        <InspectorReason tone="danger" :text="t(k('rejection_reason'), { reason: view.current.rejectionReason })" />
      </div>
      <InspectorPaneState v-if="status === 'loading' && !stale" state="loading" />
      <InspectorPaneState v-else-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    </section>

    <section v-if="view.slot" class="inspector-documents-section" data-test="documents-slot">
      <h4 class="inspector-documents-heading">{{ t(k('slot.title')) }}</h4>
      <dl class="inspector-documents-rows">
        <template v-for="item in view.slot.rows" :key="item.key">
          <dt>{{ t(k(`slot.${item.key}`)) }}</dt>
          <dd :data-test="`documents-slot-${item.key}`">{{ item.value }}</dd>
        </template>
      </dl>
      <InspectorStatusChip v-if="view.slot.protected" :label="t(k('slot.protected'))" data-test="documents-slot-protected" />
    </section>

    <section v-if="view.run" class="inspector-documents-section" data-test="documents-run">
      <h4 class="inspector-documents-heading">{{ t(k('run.title')) }}</h4>
      <dl class="inspector-documents-rows">
        <template v-for="item in view.run.rows" :key="item.key">
          <dt>{{ t(k(`run.${item.key}`)) }}</dt>
          <dd :data-test="`documents-run-${item.key}`">{{ item.value }}</dd>
        </template>
      </dl>
      <Button
        v-if="view.run.resultDocId"
        size="sm"
        variant="ghost"
        :title="t(k('open_hint'))"
        data-test="documents-run-open"
        @click="open(view.run.resultDocId)"
      >{{ t(k('open')) }}</Button>
    </section>

    <section v-if="view.rejections.length" class="inspector-documents-section" data-test="documents-rejections">
      <h4 class="inspector-documents-heading">{{ t(k('rejections.title')) }}</h4>
      <ul class="inspector-documents-list">
        <li v-for="item in view.rejections" :key="item.key" data-test="documents-rejection">
          <span v-if="item.rejectedAt" class="inspector-documents-time">{{ item.rejectedAt }}</span>
          <span class="inspector-documents-text">{{ item.reason }}</span>
          <span v-if="item.aiResponse" class="inspector-documents-sub" data-test="documents-rejection-ai">{{ t(k('rejections.ai_response'), { text: item.aiResponse }) }}</span>
        </li>
      </ul>
    </section>

    <section v-if="view.previous.length" class="inspector-documents-section" data-test="documents-previous">
      <h4 class="inspector-documents-heading">{{ t(k('previous.title')) }}</h4>
      <ul class="inspector-documents-list">
        <li v-for="item in view.previous" :key="item.docId" data-test="documents-previous-item" :data-doc="item.docId" :data-state="item.state">
          <span class="inspector-documents-line">
            <span v-if="item.typeCode" class="inspector-documents-type">{{ item.typeCode }}</span>
            <span v-if="item.seq !== null" class="inspector-documents-seq">#{{ item.seq }}</span>
            <span class="inspector-documents-title" :title="item.title ?? item.docId">{{ item.title ?? item.docId }}</span>
            <InspectorStatusChip v-if="item.reviewStatus" :label="reviewText(item.reviewStatus)" :tone="item.reviewTone" />
            <Button
              v-if="availability.open.visible"
              size="sm"
              variant="ghost"
              :disabled="!availability.open.enabled"
              :title="t(k('open_hint'))"
              data-test="documents-previous-open"
              @click="open(item.docId)"
            >{{ t(k('open')) }}</Button>
          </span>
          <span v-if="item.state === 'missing'" class="inspector-documents-sub" data-test="documents-previous-missing">{{ t(k('previous.missing')) }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useDocuments } from './useDocuments'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.documents.${key}`

const { view, availability, status, stale, errorKey, reload, open } = useDocuments(() => props.target)

/** Display text of a review status; an unknown value shows as sent. */
function reviewText(value: string): string {
  return te(k(`review_status.${value}`)) ? t(k(`review_status.${value}`)) : value
}
</script>

<style scoped>
.inspector-documents {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  font-size: 12px;
}

.inspector-documents-current,
.inspector-documents-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.inspector-documents-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-documents-type {
  flex: 0 0 auto;
  padding: 0 6px;
  border: 1px solid var(--fg2-line);
  border-radius: 4px;
  font-weight: 600;
}

.inspector-documents-seq,
.inspector-documents-time,
.inspector-documents-sub {
  color: var(--fg2-muted);
}

.inspector-documents-title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-documents-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--fg2-muted);
}

.inspector-documents-heading {
  margin: 0;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--fg2-muted);
}

.inspector-documents-rows {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 2px 12px;
  margin: 0;
}

.inspector-documents-rows dt {
  color: var(--fg2-muted);
}

.inspector-documents-rows dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

.inspector-documents-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-documents-list li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.inspector-documents-text {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
</style>
