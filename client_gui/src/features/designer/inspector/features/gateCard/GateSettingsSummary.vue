<template>
  <div v-if="!rejected && (summary.target || summary.push || summary.unmergeUnknown || errorKey)" class="inspector-gate-summary" data-test="gate-settings-summary">
    <span v-if="summary.target">{{ t(k('git.target')) }} <strong data-test="gate-summary-target">{{ summary.target }}</strong></span>
    <span v-if="summary.unmergeWarning" data-test="gate-summary-unmerge">{{ t(k('target.unmerge_unsupported')) }}</span>
    <span v-if="summary.unmergeUnknown" data-test="gate-summary-unmerge-unknown">{{ t(k('target.unmerge_unknown')) }}</span>
    <span v-if="summary.push" data-test="gate-summary-push-create">{{ t(k('target.push_create')) }}</span>
    <Button v-if="unmergeBlocked" size="sm" variant="ghost" disabled :title="t(k(summary.unmergeUnknown || errorKey === 'target.unmerge_unknown' ? 'target.unmerge_unknown' : 'target.unmerge_unsupported'))" data-test="gate-unmerge">{{ t(k('target.undo_merge')) }}</Button>
    <span v-if="errorKey" role="alert" data-test="gate-summary-error">{{ t(k(errorKey)) }}</span>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import Button from '../../../../../components/ui/Button.vue'
import { useI18n } from 'vue-i18n'
import type { RunGateResult, SettingsNode } from '@/features/document'
import { checkOperationErrorKey, checkSettingsSummary } from '../checkSettings/checkSettingsModel'
const props = defineProps<{ node?: SettingsNode | null; decision?: RunGateResult | null; errorCode?: string | null }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.checkSettings.${key}`
const rejected = computed(() => props.decision?.result === 'rejected' || props.decision?.status === 'rejected')
const summary = computed(() => checkSettingsSummary(props.node ?? null, props.decision))
const unmergeBlocked = computed(() => (!!props.decision && (summary.value.unmergeWarning || summary.value.unmergeUnknown)) || ['target.unmerge_unsupported', 'target.unmerge_unknown'].includes(errorKey.value ?? ''))
const errorKey = computed(() => checkOperationErrorKey(props.errorCode ?? props.decision?.last_error_code, props.decision))
</script>
<style scoped>
.inspector-gate-summary { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; }
</style>
