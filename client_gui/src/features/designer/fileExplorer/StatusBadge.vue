<template>
  <Badge class="dfx-status" :tone="tone" size="sm" :title="label" :aria-label="label" data-test="dfx-status">{{ status }}</Badge>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — Base-relative change status (0024-D §8.3). The domain mapping
// (status letter -> tone/label) lives here in the feature; the chip itself is the shared
// domain-free Badge primitive.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '../../../components/ui/Badge.vue'
import type { ChangeStatus } from './fileExplorerModel'

const props = defineProps<{ status: ChangeStatus }>()
const { t } = useI18n()

const TONES: Record<ChangeStatus, 'warning' | 'success' | 'danger'> = { M: 'warning', A: 'success', D: 'danger' }
const tone = computed(() => TONES[props.status])
const label = computed(() => t(`gui.designer.file_explorer.status.${props.status}`))
</script>

<style scoped>
.dfx-status {
  min-width: 20px;
  justify-content: center;
  padding: 0 5px;
  font-size: 10.5px;
  line-height: 16px;
}
</style>
