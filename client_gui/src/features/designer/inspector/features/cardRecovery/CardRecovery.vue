<template>
  <section v-if="candidate || done || failure !== null" class="card-recovery" data-test="card-recovery">
    <Button v-if="candidate && !done" size="sm" :disabled="!enabled" :loading="busy"
      :title="t(k('hint'))" data-test="card-recover" @click="recover">{{ t(k('recover')) }}</Button>
    <span v-if="done" role="status">{{ t(k('done')) }}</span>
    <span v-if="failure !== null" role="alert">{{ failure || t(k('failed')) }}</span>
  </section>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useCardRecovery } from './useCardRecovery'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.cardRecovery.${key}`
const { candidate, enabled, busy, done, failure, recover } = useCardRecovery(() => props.target)
</script>
<style scoped>
.card-recovery { display: flex; align-items: center; gap: 8px; padding: 8px 10px; font-size: 12px; }
</style>
