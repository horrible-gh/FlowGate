<template>
  <!-- flowgate.v02.0150 — the Q&A ↗ windows of every document with one open (useQnaWindows), drawn by the Frame for as
       long as it lives whatever is selected: no card, another card, another tab. Each document's windows keep the
       target they were opened for; only a window's own × closes it. -->
  <QnaWindows
    v-for="entry in qnaWindowDocuments"
    :key="entry.key"
    :target="entry.target"
    :windows="entry.windows"
    @close="(itemId: number) => closeQnaWindow(entry.key, itemId)"
  />
</template>

<script setup lang="ts">
import { watch } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import QnaWindows from './QnaWindows.vue'
import { closeQnaWindow, followQnaWindowTarget, qnaWindowDocuments } from './useQnaWindows'

const props = defineProps<{
  /** The selected target, or null (nothing selected): it only refreshes the windows of its own document. */
  target: InspectorTarget | null
}>()

watch(() => props.target, (target) => followQnaWindowTarget(target), { immediate: true })
</script>
