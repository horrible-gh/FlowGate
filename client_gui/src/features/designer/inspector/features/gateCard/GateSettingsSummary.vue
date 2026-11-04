<template>
  <div v-if="!rejected && ((summary.target && !hideTarget) || summary.push || summary.unmergeUnknown || errorKey)" class="inspector-gate-summary" data-test="gate-settings-summary">
    <span v-if="summary.target && !hideTarget">{{ t(k('git.target')) }} <strong data-test="gate-summary-target">{{ summary.target }}</strong></span>
    <span v-if="summary.unmergeUnknown" data-test="gate-summary-unmerge-unknown">{{ t(k('target.unmerge_unknown')) }}</span>
    <span v-if="summary.push" data-test="gate-summary-push-create">{{ t(k('target.push_create')) }}</span>
    <Button v-if="unmergeBlocked" size="sm" variant="ghost" disabled :title="t(k('target.unmerge_unknown'))" data-test="gate-unmerge">{{ t(k('target.undo_merge')) }}</Button>
    <span v-if="errorKey" role="alert" data-test="gate-summary-error">{{ t(k(errorKey)) }}</span>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import Button from '../../../../../components/ui/Button.vue'
import { useI18n } from 'vue-i18n'
import type { RunGateResult, SettingsNode } from '@/features/document'
import { checkOperationErrorKey, checkSettingsSummary } from '../checkSettings/checkSettingsModel'
// hideTarget: the card's document tab does not show the target line (T0012 rework).
const props = defineProps<{ node?: SettingsNode | null; decision?: RunGateResult | null; errorCode?: string | null; hideTarget?: boolean }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.checkSettings.${key}`
const rejected = computed(() => props.decision?.result === 'rejected' || props.decision?.status === 'rejected')
const summary = computed(() => checkSettingsSummary(props.node ?? null, props.decision))
const unmergeBlocked = computed(() => (!!props.decision && summary.value.unmergeUnknown) || errorKey.value === 'target.unmerge_unknown')
const errorKey = computed(() => checkOperationErrorKey(props.errorCode ?? props.decision?.last_error_code, props.decision))
</script>
<style scoped>
.inspector-gate-summary { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; }
</style>
