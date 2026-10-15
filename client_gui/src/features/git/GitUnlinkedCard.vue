<template>
  <div class="gx-unlinked" data-test="git-unlinked">
    <div class="gx-unlinked-hero">
      <div class="gx-unlinked-symbol" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <circle cx="6" cy="4" r="2" />
          <circle cx="18" cy="7" r="2" />
          <circle cx="18" cy="19" r="2" />
          <path d="M6 6v8a5 5 0 0 0 5 5h5M6 6v4a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V9" />
        </svg>
      </div>
      <div class="gx-unlinked-heading" data-test="git-unlinked-heading">{{ t('gui.git.unlinked.heading') }}</div>
    </div>
    <button type="button" class="gx-connect-btn" :disabled="disabled" data-test="git-unlinked-connect" @click="emit('connect')">
      {{ t('gui.git.unlinked.connect') }}
    </button>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0065 — Git 미연동 (0065-R §3.1, v12 `#gxUnlinked`): the Git icon, `Git 미연동`
// and `Git 연결`, shown instead of the exception cards when the project's Git is off. A read
// failure is not this card (GitStatusErrorCard).
import { useI18n } from 'vue-i18n'

withDefaults(defineProps<{ disabled?: boolean }>(), { disabled: false })
const emit = defineEmits<{ connect: [] }>()
const { t } = useI18n()
</script>
