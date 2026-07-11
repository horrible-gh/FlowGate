<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="onCancel">
      <div class="modal-box" style="width:560px;max-width:94vw;">
        <div class="modal-hd">
          <span class="modal-title gd-danger">
            <AppIcon name="prohibit" />
            {{ t('main.group_actions.discard_title') }}
          </span>
          <button class="modal-close" type="button" @click="onCancel">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
          <div class="gd-warn">
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
        </div>
        <div class="modal-ft">
          <button type="button" class="btn btn-secondary" @click="onCancel">
            {{ t('main.group_actions.discard_cancel') }}
          </button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="!canConfirm || submitting"
            @click="onConfirm"
          >
            <AppIcon v-if="submitting" name="spinner" spin />
            <AppIcon v-else name="prohibit" />
            {{ t('main.group_actions.discard_confirm') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

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

// Double gate (TR0004 §discard): the danger button stays disabled until BOTH a
// non-empty reason and the explicit acknowledgement checkbox are satisfied.
const canConfirm = computed(() => reason.value.trim().length > 0 && ack.value)

// Reset the gate every time the modal (re)opens so a previous attempt's reason or
// acknowledgement never carries over to a new group.
watch(
  () => props.visible,
  (val) => {
    if (val) {
      reason.value = ''
      ack.value = false
      nextTick(() => reasonRef.value?.focus())
    }
  },
)

function onConfirm() {
  if (!canConfirm.value || props.submitting) return
  emit('confirm', reason.value.trim())
}

function onCancel() {
  emit('cancel')
  emit('update:visible', false)
}
</script>

<style scoped>
.gd-danger {
  display: flex;
  align-items: center;
  gap: 8px;
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
