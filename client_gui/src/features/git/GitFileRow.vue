<template>
  <button type="button" class="gx-file" :title="t('gui.git.file.open_diff')" :data-path="path" @click="emit('select')">
    <span
      v-if="letter"
      class="gx-file-st"
      :class="`st-${letter === '?' ? 'u' : letter.toLowerCase()}`"
    >{{ letter }}</span>
    <span
      v-else
      class="gx-file-st st-unknown"
      :title="t('gui.git.file.unknown')"
      :aria-label="t('gui.git.file.unknown')"
    >·</span>
    <span class="gx-file-nm">{{ path }}</span>
    <span class="gx-file-go" aria-hidden="true">±</span>
  </button>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — one Git file row: state letter (or the "status unknown" mark) + path.
// Selecting it only raises the diff intent; the diff itself is the Center's (0033-D §4.7).
import { useI18n } from 'vue-i18n'
import type { GitFileLetter } from './types'

defineProps<{ path: string; letter: GitFileLetter | null }>()
const emit = defineEmits<{ select: [] }>()
const { t } = useI18n()
</script>
