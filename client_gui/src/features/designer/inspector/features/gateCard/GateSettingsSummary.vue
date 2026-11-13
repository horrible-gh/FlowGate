<template>
  <!-- flowgate.v02test.0002 R0001 #1: no standing "missing remote branches are created" line (push ON or OFF);
       the box shows only the target, the restore limit and real errors, and is not drawn when none is left. -->
  <div v-if="!rejected && ((summary.target && !hideTarget) || summary.unmergeUnknown || errorKey)" class="inspector-gate-summary" data-test="gate-settings-summary">
    <span v-if="summary.target && !hideTarget">{{ t(k('git.target')) }} <strong data-test="gate-summary-target">{{ summary.target }}</strong></span>
    <span v-if="summary.unmergeUnknown" data-test="gate-summary-unmerge-unknown">{{ t(k('target.unmerge_unknown')) }}</span>
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
// flowgate.v02gui.0004: a merge conflict is told by the result pill and the Git tab's conflict card, not again here
// (and not left behind once the conflict review is closed).
const errorKey = computed(() => {
  const key = checkOperationErrorKey(props.errorCode ?? props.decision?.last_error_code, props.decision)
  return key === 'target.merge_conflict' ? null : key
})
</script>
<style scoped>
.inspector-gate-summary { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; }
</style>
