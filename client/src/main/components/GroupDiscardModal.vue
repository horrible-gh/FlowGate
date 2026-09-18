<template>
  <!-- flowgate.default.0560 T0016 §2.2 (2단계) — migrated onto the common dialog layer.
       D0008 "기존 instance 이관 목적지" maps this instance to `confirm-danger`. It is NOT
       routed through the imperative ConfirmDialog (T0016 §2.2): the body is a real form
       — reason textarea, acknowledgement checkbox and the impacted-document chips — so
       DialogShell is used directly and the body stays feature-owned.
       `size="md"` rather than the variant's `sm`: this box was 560px wide and the confirm
       default (400px) would re-narrow a form that has to hold a chip list. -->
  <DialogShell
    ref="shellRef"
    :open="visible"
    variant="confirm-danger"
    size="md"
    @request-close="onCancel"
    @opened="onOpened"
  >
    <template #header>
      <DialogHeader :title="t('main.group_actions.discard_title')" @close="onHeaderClose">
        <template #icon>
          <AppIcon name="prohibit" style="color:var(--danger);" />
        </template>
        <template #title>
          <span class="gd-danger">{{ t('main.group_actions.discard_title') }}</span>
        </template>
      </DialogHeader>
    </template>

    <template #default="{ descriptionId }">
      <div :id="descriptionId" class="gd-warn">
        <AppIcon name="warning" />
        <span>{{ t('main.group_actions.discard_warning', { group: groupTitle, count: documents.length }) }}</span>
      </div>
      <p class="gd-note">{{ t('main.group_actions.discard_note') }}</p>
      <div v-if="documents.length" class="gd-impact">
        <div class="gd-impact-hd">{{ t('main.group_actions.discard_impact_hd', { count: documents.length }) }}</div>
        <div class="gd-chip-row">
          <span v-for="d in documents" :key="d.id" class="gd-chip">
            <span class="doc-tag" :class="`c-${d.typeCode}`">{{ d.typeCode }}</span>
            {{ d.shortId }}
          </span>
        </div>
      </div>
      <div class="gd-field">
        <label class="gd-field-label">
          {{ t('main.group_actions.discard_reason_label') }} <span class="gd-req">*</span>
        </label>
        <textarea
          ref="reasonRef"
          v-model="reason"
          class="gd-textarea"
          :placeholder="t('main.group_actions.discard_reason_placeholder')"
        ></textarea>
      </div>
      <label class="gd-confirm-line">
        <input type="checkbox" v-model="ack" />
        {{ t('main.group_actions.discard_ack') }}
      </label>
    </template>

    <template #footer>
      <DialogFooter :actions="actions">
        <template #action-confirm>
          <AppIcon v-if="submitting" name="spinner" spin />
          <AppIcon v-else name="prohibit" />
          {{ t('main.group_actions.discard_confirm') }}
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'

export interface GroupDiscardDoc {
  id: string
  typeCode: string
  shortId: string
}

const props = defineProps<{
  visible: boolean
  groupTitle: string
  documents: GroupDiscardDoc[]
  submitting?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  confirm: [reason: string]
  cancel: []
}>()

const { t } = useI18n()

const reason = ref('')
const ack = ref(false)
const reasonRef = ref<HTMLTextAreaElement | null>(null)
const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)

// Double gate (TR0004 §discard): the danger button stays disabled until BOTH a
// non-empty reason and the explicit acknowledgement checkbox are satisfied.
const canConfirm = computed(() => reason.value.trim().length > 0 && ack.value)

/**
 * `[취소] [폐기(위험 주버튼)]` — D0008 §6 "Danger Confirm": the destructive action keeps
 * the primary POSITION and only carries `tone: 'danger'`. That is the same left-to-right
 * order this modal already had (T0016 §2.1 #2 found it matching the baseline), so nothing
 * moves; it is the layer that owns the order now.
 */
const actions = computed<DialogAction[]>(() => [
  {
    id: 'cancel',
    label: t('main.group_actions.discard_cancel'),
    role: 'cancel',
    onSelect: onCancel,
  },
  {
    id: 'confirm',
    label: t('main.group_actions.discard_confirm'),
    role: 'primary',
    tone: 'danger',
    disabled: !canConfirm.value || props.submitting === true,
    onSelect: onConfirm,
  },
])

// Reset the gate every time the modal (re)opens so a previous attempt's reason or
// acknowledgement never carries over to a new group.
watch(
  () => props.visible,
  (val) => {
    if (val) {
      reason.value = ''
      ack.value = false
    }
  },
)

/**
 * `confirm-danger` deliberately puts initial focus on 취소 so a reflex Enter cannot fire
 * the destructive button (L0009 §4). This dialog's own entry point is the reason field,
 * and `opened` fires AFTER the shell applied its initial focus — so restoring the
 * pre-migration focus here is deterministic instead of racing the shell.
 */
function onOpened() {
  void nextTick(() => reasonRef.value?.focus())
}

function onConfirm() {
  if (!canConfirm.value || props.submitting) return
  emit('confirm', reason.value.trim())
}

function onCancel() {
  emit('cancel')
  emit('update:visible', false)
}

function onHeaderClose() {
  shellRef.value?.requestClose('header')
}
</script>

<style scoped>
.gd-danger {
  color: var(--danger);
}
.gd-warn {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #fecaca;
  border-radius: var(--r);
  background: var(--danger-l);
  color: #b91c1c;
  font-size: .8rem;
  line-height: 1.55;
}
.gd-warn i { margin-top: 2px; flex-shrink: 0; }
.gd-note {
  margin: 0 0 14px;
  color: var(--text-s);
  font-size: .75rem;
  line-height: 1.6;
}
.gd-impact {
  margin-bottom: 14px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
}
.gd-impact-hd {
  margin-bottom: 8px;
  color: var(--text-m);
  font-size: .72rem;
  font-weight: 700;
}
.gd-chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.gd-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  color: var(--text-s);
  font-size: .7rem;
}
.gd-field { margin-bottom: 6px; }
.gd-field-label {
  display: block;
  margin-bottom: 6px;
  color: var(--text);
  font-size: .76rem;
  font-weight: 600;
}
.gd-req { color: var(--danger); }
.gd-textarea {
  width: 100%;
  min-height: 84px;
  padding: 9px 11px;
  resize: vertical;
  border: 1px solid var(--border);
  border-radius: var(--r);
  color: var(--text);
  background: var(--surface);
  font-family: inherit;
  font-size: .8rem;
  line-height: 1.5;
}
.gd-textarea:focus {
  outline: none;
  border-color: var(--danger);
  box-shadow: 0 0 0 3px var(--danger-l);
}
.gd-confirm-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 2px;
  color: var(--text-s);
  font-size: .76rem;
  cursor: pointer;
}
</style>
