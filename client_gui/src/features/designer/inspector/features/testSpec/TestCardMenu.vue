<template>
  <InspectorMenuItem v-for="kind in kinds" :key="kind" :item="`test-${kind}`" :disabled="!available(kind)" :reason="reason(kind)" @select="open(kind)">
    {{ t(k(`header.open_${kind}`)) }}
  </InspectorMenuItem>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorMenuItem from '../../parts/InspectorMenuItem.vue'
import { useTestCard } from './useTestCard'
const props = defineProps<{ target: InspectorTarget; close: () => void }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.${key}`
const card = useTestCard(() => props.target)
const kinds = ['scenario', 'report'] as const
function available(kind: typeof kinds[number]) { return !!card.value && card.value.openReason(kind) === null }
function reason(kind: typeof kinds[number]) { const key = card.value?.openReason(kind) ?? 'reason.busy'; return t(k(key)) }
function open(kind: typeof kinds[number]) { if (available(kind) && card.value?.openWindow(kind)) props.close() }
</script>
