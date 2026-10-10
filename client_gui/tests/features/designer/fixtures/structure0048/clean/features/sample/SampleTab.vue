<template>
  <InspectorPaneState v-if="state.loading" state="loading" />
  <section v-else>
    <InspectorStatusChip :label="t('gui.inspector.sample.title')" />
    <InspectorReason v-if="availability.reason" :text="t(`gui.inspector.sample.${availability.reason}`)" />
    <Button :disabled="!availability.enabled" @click="open">{{ target.title }}</Button>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { sampleAvailability } from './sampleModel'
import { useSample } from './useSample'

const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const { state, open } = useSample(computed(() => props.target))
const availability = computed(() => sampleAvailability(props.target))
</script>
