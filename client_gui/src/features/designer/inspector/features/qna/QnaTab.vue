<template>
  <!-- flowgate.v02.0048 T#7 — Q&A tab (D#1 §10.2 T#7 row): the questions of the selected Node's
       document — waiting for the user first — with who asked and who answered, and an answer form
       on each question without an answer: prose, one of the question's options, or both. A thin
       container: useQna reads and answers, qnaModel decides the order, the sides and availability. -->
  <div class="inspector-qna" data-test="inspector-qna" :data-status="status">
    <InspectorPaneState v-if="status === 'loading' && !items" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !items" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="items">
      <InspectorPaneState v-if="!items.length" state="empty" :message="t(k('empty'))" />
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <ul v-if="items.length" class="inspector-qna-list">
        <li
          v-for="item in items"
          :key="item.id"
          class="inspector-qna-item"
          data-test="qna-item"
          :data-item="item.id"
          :data-state="item.state"
        >
          <div class="inspector-qna-line">
            <span v-if="item.seq !== null" class="inspector-qna-seq">{{ t(k('question'), { seq: item.seq }) }}</span>
            <span class="inspector-qna-title" :title="item.title ?? undefined" data-test="qna-item-title">{{ item.title ?? '' }}</span>
            <InspectorStatusChip :label="t(k(`state.${item.state}`))" :tone="toneOf(item.state)" data-test="qna-item-state" />
          </div>
          <span class="inspector-qna-sub" data-test="qna-item-asker" :data-side="item.asker.kind">{{ t(k('asked_by'), { side: sideText(item.asker) }) }}</span>
          <p class="inspector-qna-text" data-test="qna-item-body">{{ item.body }}</p>

          <ul v-if="item.answers.length" class="inspector-qna-answers">
            <li v-for="answer in item.answers" :key="answer.key" data-test="qna-answer" :data-side="answer.side.kind">
              <span class="inspector-qna-sub" data-test="qna-answer-side">{{ t(k('answered_by'), { side: sideText(answer.side) }) }}</span>
              <span v-if="answer.picked.length" class="inspector-qna-sub" data-test="qna-answer-picked">{{ t(k('picked'), { labels: answer.picked.join(', ') }) }}</span>
              <p class="inspector-qna-text" data-test="qna-answer-body">{{ answer.body }}</p>
            </li>
          </ul>

          <div v-if="item.answerable" class="inspector-qna-form" data-test="qna-answer-form">
            <div v-if="item.options.length" class="inspector-qna-options" role="group" :aria-label="t(k('options'))">
              <Button
                v-for="option in item.options"
                :key="option.id"
                size="sm"
                :variant="pickedId(item.id) === option.id ? 'primary' : 'secondary'"
                :aria-pressed="pickedId(item.id) === option.id"
                :disabled="!availability.answer.enabled"
                data-test="qna-option"
                :data-option="option.id"
                @click="pick(item.id, option.id)"
              >{{ option.label }}</Button>
            </div>
            <Textarea
              :model-value="bodyOf(item.id)"
              :rows="2"
              :placeholder="t(k('answer_placeholder'))"
              :disabled="!availability.answer.enabled"
              data-test="qna-answer-body-input"
              @update:model-value="(value: string) => setBody(item.id, value)"
            />
            <div class="inspector-qna-line">
              <Button
                size="sm"
                variant="primary"
                :disabled="!availability.answer.enabled || !canSend(item.id)"
                :loading="busy === item.id"
                data-test="qna-answer-submit"
                @click="submit(item.id)"
              >{{ t(k('answer')) }}</Button>
            </div>
            <div v-if="availability.answer.reason" data-test="qna-answer-reason">
              <InspectorReason :text="t(k(availability.answer.reason))" />
            </div>
            <div v-if="failure && failure.itemId === item.id" data-test="qna-answer-failure">
              <InspectorReason tone="danger" :text="t(k(failure.key))" />
            </div>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import Textarea from '../../../../../components/ui/Textarea.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import type { QnaItemState, QnaSide } from './qnaModel'
import { useQna } from './useQna'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.qna.${key}`

const { items, availability, status, errorKey, busy, failure, ready, answer, reload } = useQna(() => props.target)

interface Draft {
  body: string
  picked: string | null
}

// Unsent answers per question of the shown document (written by the handlers only, never in render).
const drafts = reactive<Record<number, Draft>>({})

function draftOf(itemId: number): Draft {
  if (!drafts[itemId]) drafts[itemId] = { body: '', picked: null }
  return drafts[itemId]
}

watch(
  () => props.target.docId,
  () => {
    for (const id of Object.keys(drafts)) delete drafts[Number(id)]
  },
)

const bodyOf = (itemId: number) => drafts[itemId]?.body ?? ''
const pickedId = (itemId: number) => drafts[itemId]?.picked ?? null
const pickedOf = (itemId: number) => {
  const picked = pickedId(itemId)
  return picked === null ? [] : [picked]
}

function setBody(itemId: number, value: string) {
  draftOf(itemId).body = value
}

/** Picks an option of the question; picking it again takes it back. */
function pick(itemId: number, optionId: string) {
  const draft = draftOf(itemId)
  draft.picked = draft.picked === optionId ? null : optionId
}

function canSend(itemId: number): boolean {
  return ready(itemId, bodyOf(itemId), pickedOf(itemId))
}

async function submit(itemId: number) {
  if (await answer(itemId, bodyOf(itemId), pickedOf(itemId))) delete drafts[itemId]
}

function sideText(side: QnaSide): string {
  return t(k(side.key), side.params)
}

function toneOf(state: QnaItemState): 'warning' | 'neutral' | 'success' {
  return state === 'waiting' ? 'warning' : state === 'answered' ? 'success' : 'neutral'
}
</script>

<style scoped>
.inspector-qna {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.inspector-qna-list,
.inspector-qna-answers {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-qna-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--fg2-line);
}

.inspector-qna-answers {
  gap: 6px;
  padding-left: 10px;
  border-left: 2px solid var(--fg2-line);
}

.inspector-qna-answers li,
.inspector-qna-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.inspector-qna-line,
.inspector-qna-options {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-qna-seq {
  flex: 0 0 auto;
  font-weight: 600;
}

.inspector-qna-title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-qna-sub {
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-qna-text {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
</style>
