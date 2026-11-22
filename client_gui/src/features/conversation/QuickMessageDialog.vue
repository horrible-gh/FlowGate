<template>
  <ModalShell
    :open="open"
    size="sm"
    :close-on-escape="!busy"
    :close-on-backdrop="false"
    @close="!busy && emit('cancel')"
  >
    <template #header="{ titleId }">
      <h2 :id="titleId" class="qm-dialog-title" data-test="qm-dialog-title">{{ item ? t('gui.quickMessages.edit_title') : t('gui.quickMessages.add_title') }}</h2>
    </template>
    <form class="qm-dialog" data-test="qm-dialog" novalidate @submit.prevent="submit">
      <label class="qm-field">
        <span class="qm-label">{{ t('gui.quickMessages.field_title') }}</span>
        <Input
          :model-value="title"
          :placeholder="t('gui.quickMessages.field_title_hint')"
          :invalid="problem === 'title'"
          :disabled="busy"
          maxlength="12"
          data-modal-autofocus
          data-test="qm-title"
          @update:model-value="onTitle"
        />
        <span v-if="problem === 'title'" class="qm-problem" role="alert" data-test="qm-problem">{{ t('gui.quickMessages.problem_title') }}</span>
      </label>
      <label class="qm-field">
        <span class="qm-label">{{ t('gui.quickMessages.field_message') }}</span>
        <Textarea
          v-model="message"
          :rows="4"
          :placeholder="t('gui.quickMessages.field_message_hint')"
          :invalid="problem === 'message'"
          :disabled="busy"
          data-test="qm-message"
        />
        <span v-if="problem === 'message'" class="qm-problem" role="alert" data-test="qm-problem">{{ t('gui.quickMessages.problem_message') }}</span>
      </label>
      <div class="qm-field">
        <span class="qm-label" :id="kindLabelId">{{ t('gui.quickMessages.field_kind') }}</span>
        <div class="qm-kinds" role="radiogroup" :aria-labelledby="kindLabelId">
          <button
            v-for="option in KINDS"
            :key="option.kind"
            type="button"
            role="radio"
            class="qm-kind"
            :class="`is-${option.kind}`"
            :aria-checked="kind === option.kind"
            :disabled="busy"
            :data-test="`qm-kind-${option.kind}`"
            @click="kind = option.kind"
          >
            <span class="qm-dot" aria-hidden="true"></span>{{ t(option.label) }}
          </button>
        </div>
      </div>
      <p v-if="failed" class="qm-problem" role="alert" data-test="qm-failed">{{ t('gui.quickMessages.save_failed') }}</p>
    </form>
    <template #footer>
      <div class="qm-actions">
        <Button variant="secondary" :disabled="busy" data-test="qm-cancel" @click="emit('cancel')">{{ t('gui.quickMessages.cancel') }}</Button>
        <Button variant="primary" :loading="busy" data-test="qm-save" @click="submit">{{ t('gui.quickMessages.save') }}</Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// flowgate.v02test.0072 card 3: the small window of ＋ 추가 and of 수정 (same window). Title (12 characters at most),
// the text to send and the kind (파랑 · 1회 / 빨강 · 계속, 1회 by default). A blank title or text is not saved: the
// reason shows inside the window. `save` resolves true when stored (the window closes); false keeps it open with a notice.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../components/ui/Button.vue'
import Input from '../../components/ui/Input.vue'
import ModalShell from '../../components/ui/ModalShell.vue'
import Textarea from '../../components/ui/Textarea.vue'
import {
  checkQuickMessageInput,
  QUICK_TITLE_MAX,
  type QuickMessage,
  type QuickMessageInput,
  type QuickMessageInputProblem,
  type QuickMessageKind,
} from './quickMessages/api'
import { installQuickMessages } from './quickMessages/messages'

const props = defineProps<{
  open: boolean
  /** The button being edited; null = a new one. */
  item: QuickMessage | null
  save: (input: QuickMessageInput) => Promise<boolean>
}>()

const emit = defineEmits<{ cancel: []; saved: [] }>()

installQuickMessages(useI18n({ useScope: 'global' }))
const { t } = useI18n()

const KINDS: readonly { kind: QuickMessageKind; label: string }[] = [
  { kind: 'once', label: 'gui.quickMessages.kind_once' },
  { kind: 'sticky', label: 'gui.quickMessages.kind_sticky' },
]
const kindLabelId = `qm-kind-label-${Math.random().toString(36).slice(2, 8)}`

const title = ref('')
const message = ref('')
const kind = ref<QuickMessageKind>('once')
const problem = ref<QuickMessageInputProblem | null>(null)
const failed = ref(false)
const busy = ref(false)

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return
    title.value = props.item?.title ?? ''
    message.value = props.item?.message ?? ''
    kind.value = props.item?.kind ?? 'once'
    problem.value = null
    failed.value = false
    busy.value = false
  },
  { immediate: true },
)

/** A title over 12 characters is stopped at the input (paste and IME included). */
function onTitle(value: string): void {
  title.value = [...value].slice(0, QUICK_TITLE_MAX).join('')
  if (problem.value === 'title') problem.value = null
}

watch(message, () => { if (problem.value === 'message') problem.value = null })

const input = computed<QuickMessageInput>(() => ({ title: title.value, message: message.value, kind: kind.value }))

async function submit(): Promise<void> {
  if (busy.value) return
  failed.value = false
  problem.value = checkQuickMessageInput(input.value)
  if (problem.value) return
  busy.value = true
  try {
    const ok = await props.save(input.value)
    if (ok) emit('saved')
    else failed.value = true
  } catch {
    failed.value = true
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.qm-dialog-title {
  margin: 0;
  padding: 20px 24px 8px;
  font-size: 16px;
  font-weight: 700;
  color: var(--fg2-text);
}

.qm-dialog {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 0 8px;
}

.qm-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.qm-label {
  font-size: 12px;
  font-weight: 650;
  color: var(--fg2-text-2);
}

.qm-problem {
  margin: 0;
  font-size: 12px;
  color: var(--fg2-status-danger-fg);
}

.qm-kinds {
  display: flex;
  gap: 8px;
}

.qm-kind {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: var(--fg2-control-h-md);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.qm-kind[aria-checked='true'].is-once {
  border-color: var(--fg2-status-info-fg);
  color: var(--fg2-status-info-fg);
}

.qm-kind[aria-checked='true'].is-sticky {
  border-color: var(--fg2-status-danger-fg);
  color: var(--fg2-status-danger-fg);
}

.qm-kind:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.qm-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--fg2-status-info-fg);
}

.qm-kind.is-sticky .qm-dot {
  background: var(--fg2-status-danger-fg);
}

.qm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 24px 20px;
}
</style>
