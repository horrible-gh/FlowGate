<template>
  <!-- flowgate.v02.0051 stage 2 — a Graph gate's header actions (0035 v7 확인 cards: ✓ 확인 · ✕ 반려 ▾).
       The gate confirm / reject API is not registered (D#1 §8.4) and the gate's role is under discussion
       (v7 §5), so both are shown disabled with that reason (0051-R §2.3) — never a working-looking
       button. A thin container over gateCardModel; nothing is sent. -->
  <div
    v-if="actions.confirm.visible || actions.reject.visible"
    class="inspector-gate-actions"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-gate-actions"
  >
    <Button
      v-if="actions.confirm.visible"
      size="sm"
      variant="primary"
      :disabled="!actions.confirm.enabled"
      :title="actions.confirm.reason ? t(k(actions.confirm.reason)) : undefined"
      data-test="gate-confirm"
    >{{ t(k('confirm')) }}</Button>
    <Button
      v-if="actions.reject.visible"
      size="sm"
      variant="danger"
      :disabled="!actions.reject.enabled"
      :title="actions.reject.reason ? t(k(actions.reject.reason)) : undefined"
      data-test="gate-reject"
    >{{ t(k('reject')) }} ▾</Button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { gateActions } from './gateCardModel'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.gateCard.${key}`

const actions = computed(() => gateActions(props.target))
</script>

<style scoped>
.inspector-gate-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
</style>
