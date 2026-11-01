<template>
  <InspectorBlock :title="t(k('title'))" data-test="ai-invocations" :aria-disabled="page?.available === false">
    <InspectorReadFailure v-if="error" :reason="error" @retry="reload" />
    <table v-if="page?.available" class="ai-invocations-table">
      <thead><tr><th v-for="column in columns" :key="column">{{ t(k(column)) }}</th></tr></thead>
      <tbody><tr v-for="item in page.invocations" :key="item.invId" :title="detail(item)" :data-inv-id="item.invId">
        <td :title="item.role || ''">{{ label(item.role) }}</td>
        <td :title="item.providerMatch || ''">{{ item.requestedAiId || '—' }} → {{ item.actualAi?.name || item.actualAi?.id || '—' }}<small v-if="item.configState === 'changed'"> {{ t('gui.aiRoles.changed') }}</small></td>
        <td :title="item.requestedModel.reason || item.requestedModel.state || ''">{{ item.requestedModel.state === 'value' ? item.requestedModel.value : label(item.requestedModel.state) }}</td>
        <td :title="[item.confirmedModel.reason, item.confirmedModel.coverage, item.confirmedModel.truncated ? 'truncated' : '', JSON.stringify(item.confirmedModel.calls)].filter(Boolean).join(' · ')">{{ confirmed(item) }}</td>
        <td :title="item.compare.status">{{ label(item.compare.status) }}</td>
        <td :title="item.resultDetail || item.result">{{ label(item.result) }}</td>
      </tr></tbody>
    </table>
    <button v-if="page?.available && page.nextCursor" type="button" :disabled="loading" :aria-label="t('gui.inspector.ai.title')" @click="next">⋯</button>
  </InspectorBlock>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AiInvocation } from '@/features/project'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorBlock from '../../parts/InspectorBlock.vue'
import InspectorReadFailure from '../../parts/InspectorReadFailure.vue'
import { installAiRoleMessages } from '@/features/ai'
import { useAiInvocations } from './useAiInvocations'
const props = defineProps<{ target: InspectorTarget }>()
const composer = useI18n({ useScope: 'global' }); installAiRoleMessages(composer)
const { t, te } = composer
const k = (key: string) => `gui.inspector.ai.${key}`
const columns = ['role', 'ai', 'requested', 'confirmed', 'compare', 'result'] as const
const { page, loading, error, reload, next } = useAiInvocations(() => props.target)
function label(code: string | null): string { return code && te(k(code)) ? t(k(code)) : '—' }
function confirmed(item: AiInvocation): string {
  const model = item.confirmedModel
  if (model.state === null) return item.result === 'running' ? label('running') : '—'
  if (model.state !== 'confirmed') return label(model.state)
  return model.values.join(', ') + (model.coverage === 'partial' ? ` (${label('partial')})` : '')
}
function detail(item: AiInvocation): string {
  return [item.purpose, item.roleSource, item.startedAt || item.createdAt, item.endedAt, item.aiRunId,
    item.link.kind, item.link.prevInvId, item.roundNo, item.launchNo, item.hopNo,
    item.qa && JSON.stringify(item.qa), item.target && JSON.stringify(item.target)].filter(value => value !== null).join(' · ')
}
</script>
<style scoped>
.ai-invocations-table { width: 100%; font-size: 11px; border-collapse: collapse; table-layout: fixed; }
th, td { text-align: left; padding: 5px; overflow-wrap: anywhere; vertical-align: top; }
th { color: var(--fg2-muted); }
small { display: block; }
</style>
