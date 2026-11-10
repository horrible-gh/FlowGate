<template>
  <section class="git-recovery" role="status" data-test="git-recovery">
    <h3>{{ t('main.git_review.recovery.title') }}</h3>
    <p>{{ t(`main.git_review.recovery.next.${recovery.next_action}`) }}</p>
    <p v-if="recovery.automatic_retry_scheduled" data-test="recovery-scheduled">
      {{ t('main.git_review.recovery.scheduled', { at: recovery.reconcile_next_at }) }}
    </p>
    <p v-if="recovery.git_terminal && !recovery.approval.approved" data-test="approval-pending">
      {{ t('main.git_review.recovery.approval_pending') }}
      {{ recovery.approval.error?.code }} {{ recovery.approval.error?.message }}
    </p>
    <dl>
      <dt>{{ t('main.git_review.recovery.push_error') }}</dt>
      <dd data-test="push-evidence">
        <template v-if="recovery.push_error">
          {{ recovery.push_error.cause ?? unknown }} · {{ recovery.push_error.returncode ?? unknown }}
          <pre>{{ recovery.push_error.detail ?? unknown }}</pre>
          {{ recovery.push_error.at ?? unknown }} · {{ recovery.push_error.instance_id ?? unknown }}
        </template>
        <template v-else>{{ unknown }}</template>
      </dd>
      <dt>{{ t('main.git_review.recovery.preserved') }}</dt>
      <dd>
        <p v-for="entry in recovery.preserved_refs" :key="entry.ref" data-test="preserved-ref">
          <code>{{ entry.ref }}</code><br><code>{{ entry.sha }}</code>
          · {{ t(`main.git_review.recovery.verification.${entry.verification}`) }}
        </p>
        <span v-if="!recovery.preserved_refs.length">{{ unknown }}</span>
      </dd>
    </dl>
    <p v-if="recovery.last_error">{{ recovery.last_error.code }} {{ recovery.last_error.detail }}</p>
    <p v-if="recovery.probe_error">{{ recovery.probe_error.code }} {{ recovery.probe_error.detail }}</p>
    <p v-if="recovery.resume_missing?.length">{{ recovery.resume_missing.join(', ') }}</p>
    <p v-if="recovery.blocked_reasons?.length">{{ recovery.blocked_reasons.join(', ') }}</p>
    <p v-if="recovery.remote_outcome === 'unknown'">{{ t('main.git_review.recovery.remote_unknown') }}</p>
    <div class="git-recovery-actions">
      <button v-if="allowed('review') || allowed('resolve')" type="button" class="btn btn-primary"
        data-test="recovery-resume" :disabled="busy" @click="emit('resume')">
        {{ t(`main.git_review.recovery.next.${allowed('review') ? 'review' : 'resolve'}`) }}
      </button>
      <button v-if="allowed('retry') || allowed('retry_approval')" type="button" class="btn btn-primary"
        data-test="recovery-retry" :disabled="busy" @click="emit('retry')">
        {{ t(`main.git_review.recovery.${allowed('retry_approval') ? 'retry_approval' : 'retry'}`) }}
      </button>
      <button v-if="allowed('abort')" type="button" class="btn btn-secondary"
        data-test="recovery-abort" :disabled="busy" @click="confirmOpen = true">
        {{ t('main.git_review.recovery.abort') }}
      </button>
      <button type="button" class="btn btn-secondary" data-test="recovery-refresh" :disabled="busy" @click="emit('refresh')">
        {{ t('main.git_review.recovery.refresh') }}
      </button>
    </div>
    <ConfirmDialog :open="confirmOpen" danger :title="t('main.git_review.recovery.abort')"
      :message="t('main.git_review.recovery.abort_confirm')"
      @cancel="confirmOpen = false" @confirm="confirmOpen = false; emit('abort')" />
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from './dialogs/ConfirmDialog.vue'
import type { MergeRecovery } from '../composables/useConflictSession'
const props = defineProps<{ recovery: MergeRecovery; busy: boolean }>()
const emit = defineEmits<{ retry: []; abort: []; refresh: []; resume: [] }>()
const { t } = useI18n()
const unknown = computed(() => t('main.git_review.recovery.unknown'))
const confirmOpen = ref(false)
const allowed = (action: string) => props.recovery.allowed_actions.includes(action)
</script>
<style scoped>
.git-recovery { padding: 16px; overflow: auto; }
.git-recovery pre, .git-recovery code { white-space: pre-wrap; overflow-wrap: anywhere; }
.git-recovery-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.git-recovery dt { font-weight: 600; }
.git-recovery dd { margin: 6px 0 12px; }
</style>
