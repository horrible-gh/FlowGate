<template>
  <span
    class="dfx-status"
    :class="`dfx-status--${status.toLowerCase()}`"
    :title="label"
    :aria-label="label"
    data-test="dfx-status"
  >{{ status }}</span>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — Base-relative change status badge (0024-D §8.3).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ChangeStatus } from './fileExplorerModel'

const props = defineProps<{ status: ChangeStatus }>()
const { t } = useI18n()
const label = computed(() => t(`main.designer_explorer.status.${props.status}`))
</script>

<style scoped>
.dfx-status { flex: none; font-size: 0.7rem; font-weight: 700; line-height: 1; }
.dfx-status--m { color: var(--git-modified, #e2c08d); }
.dfx-status--a { color: var(--git-added, #73c991); }
.dfx-status--d { color: var(--danger, #dc2626); }
</style>
