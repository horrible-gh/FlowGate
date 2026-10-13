<template>
  <!-- flowgate.v02.0056 F2-2 — the card's memos in its documents tab (O3 0009-TR §4-5; v0.2 memos, not the v0.1 M
       document). A stored card only.
       flowgate.v02.0056 (0017-CH) — no section head: each memo is one line of the same card as the document lines
       above it (InspectorSlotRow: 메모 · [AI 참조] · title · buttons), in written order, with the buttons of a document
       line — the document icon opens the memo to read (the name too), ✎ opens its form — and the trash icon, which
       deletes it after one confirm. A new memo is written from the header ✎ 메모 (CardMemoButton). -->
  <section v-if="card && (memos.length || status === 'error')" class="inspector-card-memo" data-test="card-memo" :data-node="card.nodeKey" :data-status="status">
    <p v-if="status === 'error'" class="inspector-card-memo-note" data-test="card-memo-read-error">
      <span>{{ readFailure ? failureText(readFailure) : t(k('failure.other')) }}</span>
      <Button size="sm" variant="secondary" data-test="card-memo-retry" @click="reload">{{ t(k('retry')) }}</Button>
    </p>
    <template v-else>
      <p v-if="removeFailure" class="inspector-card-memo-note" data-test="card-memo-remove-failure">{{ failureText(removeFailure) }}</p>
      <div class="inspector-card-memo-list" data-test="card-memo-list">
        <InspectorSlotRow
          v-for="memo in memos"
          :key="memo.memoId"
          :name="t(k('label'))"
          :openable="true"
          :state="memo.aiRef ? { label: t(k('ai_ref_on')), tone: 'info' } : null"
          :summary="memo.title"
          :test-ids="{ state: 'card-memo-ai-ref', summary: 'card-memo-title' }"
          data-test="card-memo-item"
          :data-memo="memo.memoId"
          @open="open(memo)"
        >
          <InspectorOpenButton data-test="card-memo-open" @click="open(memo)" />
          <IconButton
            :label="t('gui.designer.inspector.slot.edit')"
            :title="lockText ?? t('gui.designer.inspector.slot.edit')"
            variant="secondary"
            size="sm"
            :disabled="lockText !== null"
            data-test="card-memo-edit"
            @click="edit(memo)"
          >✎</IconButton>
          <IconButton
            :label="t(k('delete'))"
            :title="lockText ?? t(k('delete'))"
            variant="secondary"
            size="sm"
            class="inspector-card-memo-delete"
            :disabled="lockText !== null || removing === memo.memoId"
            data-test="card-memo-remove"
            @click="removeAsked = memo"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
              <path d="M5 7h14M9.5 7V4.5h5V7M7 7l1 13h8l1-13" />
            </svg>
          </IconButton>
        </InspectorSlotRow>
      </div>
    </template>
    <ConfirmDialog
      :open="removeAsked !== null"
      :title="t(k('delete_confirm'))"
      :message="removeAsked?.title ?? ''"
      :confirm-label="t(k('delete'))"
      :cancel-label="t(k('cancel'))"
      danger
      @confirm="confirmRemove"
      @cancel="removeAsked = null"
    />
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0056 F2-2 — display of the card memos (useCardMemo reads and deletes; the windows are useCardMemoWindows').
import { computed, ref, shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import ConfirmDialog from '../../../../../components/ui/ConfirmDialog.vue'
import IconButton from '../../../../../components/ui/IconButton.vue'
import InspectorOpenButton from '../../parts/InspectorOpenButton.vue'
import InspectorSlotRow from '../../parts/InspectorSlotRow.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import type { CardMemoFacts, CardMemoFailure } from './cardMemoModel'
import { closeCardMemoWindowsOf, openCardMemoWindow } from './useCardMemoWindows'
import { useCardMemo } from './useCardMemo'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()
const k = (key: string) => `gui.inspector.cardMemo.${key}`

const { card, memos, status, readFailure, lockedReason, reload, remove } = useCardMemo(() => props.target)

/** Why ✎ and the trash icon cannot be used here (the view is read only), or null. */
const lockText = computed(() => (lockedReason.value ? t(k(lockedReason.value)) : null))

/** The memo whose delete waits for the confirm, the one being deleted, and the last refusal. */
const removeAsked = shallowRef<CardMemoFacts | null>(null)
const removing = ref<string | null>(null)
const removeFailure = shallowRef<CardMemoFailure | null>(null)

function failureText(failure: CardMemoFailure): string {
  const words = t(k(`failure.${failure.key}`))
  return failure.message ? `${words} — ${failure.message}` : words
}

function open(memo: CardMemoFacts): void {
  if (card.value) openCardMemoWindow(card.value, memo)
}

function edit(memo: CardMemoFacts): void {
  if (card.value && !lockText.value) openCardMemoWindow(card.value, memo, true)
}

async function confirmRemove(): Promise<void> {
  const memo = removeAsked.value
  const current = card.value
  removeAsked.value = null
  if (!memo || !current || lockText.value) return
  removing.value = memo.memoId
  removeFailure.value = null
  const answer = await remove(current, memo.memoId)
  removing.value = null
  // A memo already gone elsewhere is gone all the same.
  if (answer.ok || answer.failure.key === 'memo_not_found') closeCardMemoWindowsOf(current, memo.memoId)
  else removeFailure.value = answer.failure
}
</script>

<style scoped>
.inspector-card-memo {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.inspector-card-memo-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  container-type: inline-size;
}

.inspector-card-memo-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--fg2-status-danger-fg);
  font-size: 12px;
}

.inspector-card-memo-delete :deep(svg) {
  width: 15px;
  height: 15px;
}

.inspector-card-memo-delete:hover:not(:disabled) {
  color: #ff9eae;
}
</style>
