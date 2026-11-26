<template>
  <ModalShell :open="!!state" size="md" :aria-label="state?.title" @close="emit('cancel')">
    <template #header><h2 class="fgs-modal-title">{{ state?.title }}</h2></template>
    <div v-if="state" class="impact" data-testid="impact-dialog">
      <Alert v-if="state.refreshed" tone="warning" data-testid="impact-refreshed">{{ t('gui.settings.impact.refreshed') }}</Alert>
      <p v-if="!state.impact.length" class="fgs-muted" data-testid="impact-empty">{{ t('gui.settings.impact.none') }}</p>
      <template v-else>
        <p class="fgs-muted" data-testid="impact-count">{{ t('gui.settings.impact.count', { n: state.impact.length }) }}</p>
        <ul class="impact-list">
          <li v-for="row in state.impact" :key="`${row.user_id}:${row.project_id}`" data-testid="impact-row">
            <div class="who"><b>{{ userName(row.user_id) }}</b><span class="where">{{ projectName(row.project_id) }}</span></div>
            <div class="chg" data-testid="impact-role">{{ roleName(row.before) }} → {{ roleName(row.after) }}</div>
            <small v-if="row.lost.length" class="lost" data-testid="impact-lost">{{ t('gui.settings.impact.lost') }}: {{ actionNames(row.lost) }}</small>
            <small v-if="row.gained.length" class="gained" data-testid="impact-gained">{{ t('gui.settings.impact.gained') }}: {{ actionNames(row.gained) }}</small>
          </li>
        </ul>
      </template>
      <p v-if="state.error" class="fgs-error" role="alert" data-testid="impact-error">{{ state.error }}</p>
    </div>
    <template #footer>
      <div class="fgs-modal-actions">
        <button type="button" class="fgs-btn" data-testid="impact-cancel" @click="emit('cancel')">{{ t('gui.common.cancel') }}</button>
        <button type="button" class="fgs-btn pri" :disabled="busy" data-testid="impact-apply" @click="emit('confirm')">
          {{ state?.label ?? t('gui.settings.impact.apply') }}
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// v02test.0090: "누가 무엇을 얻고 잃는지" before an access change is saved. The rows are the server's
// preview (final role before → after, and the permission IDs gained / lost); this dialog only names
// them. The apply button belongs to the change flow (useChangeFlow), which sends the revision of the
// preview shown here and re-reads the preview when the server answers 409 revision_conflict.
import { useI18n } from 'vue-i18n'
import Alert from '@/components/ui/Alert.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import type { ChangeDialogState } from './useChangeFlow'

defineProps<{
  state: ChangeDialogState | null
  busy: boolean
  userName: (userId: string) => string
  projectName: (projectId: string) => string
}>()

const emit = defineEmits<{ confirm: []; cancel: [] }>()
const { t, te } = useI18n()

const SHOWN = 4

function roleName(role: string | null): string {
  return role && te(`gui.settings.project_roles.${role}`) ? t(`gui.settings.project_roles.${role}`) : t('gui.settings.impact.no_role')
}

function actionName(id: string): string {
  const key = `gui.settings.perm.${id.replace(/\./g, '_')}`
  return te(key) ? t(key) : id
}

function actionNames(ids: string[]): string {
  const names = ids.slice(0, SHOWN).map(actionName).join(', ')
  return ids.length > SHOWN ? `${names} ${t('gui.settings.impact.more', { n: ids.length - SHOWN })}` : names
}
</script>

<style scoped>
.impact {
  display: grid;
  gap: 10px;
}

.impact-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}

.impact-list li {
  border: 1px solid #263d57;
  border-radius: 10px;
  background: #0d2035;
  padding: 10px 12px;
  display: grid;
  gap: 4px;
}

.who {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.where {
  color: #83a5c8;
  font-size: 12px;
}

.chg {
  font-weight: 700;
  color: #dbe9fa;
}

.lost {
  color: #ff9d9d;
}

.gained {
  color: #6fe0b4;
}
</style>
