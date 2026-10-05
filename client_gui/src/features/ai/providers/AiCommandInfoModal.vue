<template>
  <ModalShell :open="!!provider" size="sm" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">
          {{ provider?.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection') }}
        </h2>
      </div>
    </template>
    <div ref="contentRef" class="info-modal-content">
      <template v-if="provider?.exec_type === 'cli'">
        <div class="info-field">
          <span class="info-k">{{ t('gui.ai.providers.field_cli_command') }}</span>
          <div class="code-box">
            <pre class="info-v">{{ displayedCommand }}</pre>
            <IconButton
              class="code-copy"
              :class="{ 'is-copied': copied }"
              :label="t('gui.ai.providers.copy_command')"
              :title="t('gui.ai.providers.copy_command')"
              size="sm"
              @click="copyCommand"
            >
              <AiIcon :name="copied ? 'check' : 'copy'" />
            </IconButton>
          </div>
        </div>
        <textarea
          v-if="manualCopy"
          ref="manualCopyRef"
          class="manual-copy"
          :aria-label="t('gui.ai.providers.manual_copy')"
          :value="displayedCommand"
          readonly
          @focus="selectManualCopy"
        />
      </template>
      <template v-else>
        <div class="info-field">
          <span class="info-k">{{ t('gui.ai.providers.field_api_base_url') }}</span>
          <span class="info-v">{{ provider?.api_base_url || t('gui.ai.providers.default_value') }}</span>
        </div>
        <div class="info-field">
          <span class="info-k">{{ t('gui.ai.providers.field_api_model') }}</span>
          <span class="info-v">{{ provider?.api_model || t('gui.ai.providers.default_value') }}</span>
        </div>
        <div class="info-field">
          <span class="info-k">{{ t('gui.ai.providers.field_api_key') }}</span>
          <span class="info-v" :class="{ 'text-warn': !provider?.api_key_set }">
            {{ provider?.api_key_set ? t('gui.ai.providers.key_configured') : t('gui.ai.providers.credential_required') }}
          </span>
        </div>
      </template>
    </div>
    <template #footer>
      <div class="dialog-actions">
        <Button variant="secondary" @click="emit('close')">
          {{ t('gui.common.close') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// Command / connection info of one AI, with the small copy button next to the command.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import type { ToastHostItem } from '@/components/ui/ToastHost.vue'
import { copyToClipboard } from '@/core/interaction/clipboard'
import type { ProviderItem } from '../types'
import AiIcon from '../AiIcon.vue'

const props = defineProps<{ provider: ProviderItem | null }>()
const emit = defineEmits<{
  close: []
  toast: [message: string, tone: ToastHostItem['tone']]
}>()

const { t } = useI18n()
const copied = ref(false)
const manualCopy = ref(false)
const manualCopyRef = ref<HTMLTextAreaElement | null>(null)
const contentRef = ref<HTMLElement | null>(null)
const displayedCommand = computed(() => props.provider?.cli_command || t('gui.ai.providers.default_command'))
let copiedTimer: ReturnType<typeof setTimeout> | null = null

watch(() => props.provider, () => {
  copied.value = false
  manualCopy.value = false
})

onBeforeUnmount(() => {
  if (copiedTimer) clearTimeout(copiedTimer)
})

function selectManualCopy() {
  manualCopyRef.value?.select()
}

async function copyCommand() {
  const surface = contentRef.value?.closest('.ui-modal-surface')
  const success = await copyToClipboard(displayedCommand.value, surface as HTMLElement | null)
  if (success) {
    manualCopy.value = false
    copied.value = true
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => (copied.value = false), 1600)
    emit('toast', t('gui.ai.providers.copy_success'), 'success')
    return
  }
  copied.value = false
  manualCopy.value = true
  emit('toast', t('gui.ai.providers.copy_failed'), 'danger')
  await nextTick()
  manualCopyRef.value?.focus()
  selectManualCopy()
}
</script>

<style scoped>
.dialog-header {
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--fg2-line);
}

.dialog-title {
  margin: 0;
  font-size: 16px;
  font-weight: 800;
}

.info-modal-content {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-k {
  font-size: 11.5px;
  color: var(--fg2-muted);
  font-weight: 650;
}

.info-v {
  font-size: 12.5px;
  color: var(--fg2-text);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: var(--fg2-font-mono, monospace);
  background: #091522;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--fg2-line);
}

.manual-copy {
  width: 100%;
  min-height: 72px;
  resize: vertical;
  padding: 8px 10px;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
  background: #091522;
  color: var(--fg2-text);
  font: 12.5px var(--fg2-font-mono, monospace);
}

.text-warn {
  color: var(--fg2-amber-dim, #d97706);
  font-weight: 600;
}

.code-box {
  position: relative;
}

.code-box .info-v {
  padding-right: 40px;
}

.code-copy {
  position: absolute;
  top: 4px;
  right: 4px;
}

.code-copy.is-copied {
  color: #78dcb0;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  padding: 12px 20px;
  border-top: 1px solid var(--fg2-line);
}
</style>
