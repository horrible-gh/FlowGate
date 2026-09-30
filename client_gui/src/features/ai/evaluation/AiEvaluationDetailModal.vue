<template>
  <ModalShell :open="open" size="md" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">{{ t('gui.ai.evaluation.detail_title') }}</h2>
      </div>
    </template>

    <div v-if="evaluation" class="dialog-body">
      <div class="detail-row">
        <span class="detail-k">ID:</span>
        <code class="detail-v">{{ evaluation.evaluation_id }}</code>
      </div>

      <div class="detail-row">
        <span class="detail-k">{{ t('gui.ai.evaluation.provider') }}:</span>
        <span class="detail-v font-bold">{{ evaluation.provider_name || evaluation.provider_id }}</span>
      </div>

      <div class="detail-row">
        <span class="detail-k">{{ t('gui.ai.evaluation.lifecycle') }}:</span>
        <span class="detail-v">
          <Badge :tone="getLifecycleTone(evaluation.lifecycle)">
            {{ evaluation.lifecycle }}
          </Badge>
          <Badge v-if="evaluation.is_stale" tone="warning" size="sm">
            {{ t('gui.ai.evaluation.stale') }}
          </Badge>
        </span>
      </div>

      <div v-if="evaluation.verdict" class="detail-row">
        <span class="detail-k">{{ t('gui.ai.evaluation.verdict') }}:</span>
        <span class="detail-v">
          <Badge :tone="evaluation.verdict === 'suitable' ? 'success' : 'danger'">
            {{ evaluation.verdict }}
          </Badge>
        </span>
      </div>

      <div v-if="evaluation.reason_code" class="detail-row">
        <span class="detail-k">{{ t('gui.ai.evaluation.reason') }}:</span>
        <span class="detail-v text-muted">{{ evaluation.reason_code }}</span>
      </div>

      <div v-if="evaluation.diagnostic_summary" class="detail-block">
        <span class="detail-k">{{ t('gui.ai.evaluation.diagnostic') }}:</span>
        <pre class="diagnostic-box">{{ evaluation.diagnostic_summary }}</pre>
      </div>

      <div class="detail-row">
        <span class="detail-k">{{ t('gui.ai.evaluation.created_at') }}:</span>
        <span class="detail-v">{{ formatDateTime(evaluation.created_at) }}</span>
      </div>

      <div v-if="evaluation.started_at" class="detail-row">
        <span class="detail-k">{{ t('gui.ai.evaluation.started_at') }}:</span>
        <span class="detail-v">{{ formatDateTime(evaluation.started_at) }}</span>
      </div>

      <div v-if="evaluation.finished_at" class="detail-row">
        <span class="detail-k">{{ t('gui.ai.evaluation.finished_at') }}:</span>
        <span class="detail-v">{{ formatDateTime(evaluation.finished_at) }}</span>
      </div>
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
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import type { AiEvaluation, EvaluationLifecycle } from '../types'

defineProps<{
  open: boolean
  evaluation: AiEvaluation | null
}>()

const emit = defineEmits<{
  close: []
}>()

const { t } = useI18n()

function getLifecycleTone(status: EvaluationLifecycle): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  switch (status) {
    case 'completed': return 'success'
    case 'running': return 'info'
    case 'pending': return 'warning'
    case 'failed': return 'danger'
    case 'cancelled': return 'neutral'
    default: return 'neutral'
  }
}

function formatDateTime(iso?: string | null): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
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
  color: var(--fg2-text);
}

.dialog-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-row {
  display: grid;
  grid-template-columns: 120px 1fr;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.detail-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}

.detail-k {
  color: var(--fg2-muted);
  font-weight: 650;
}

.detail-v {
  color: var(--fg2-text);
  display: flex;
  align-items: center;
  gap: 8px;
}

.diagnostic-box {
  margin: 0;
  padding: 10px 12px;
  border-radius: 6px;
  background: #091522;
  border: 1px solid var(--fg2-line);
  color: #cadbee;
  font-size: 11.5px;
  font-family: var(--fg2-font-mono, monospace);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}

.font-bold {
  font-weight: 700;
}

.text-muted {
  color: var(--fg2-muted);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  padding: 12px 20px;
  border-top: 1px solid var(--fg2-line);
}
</style>