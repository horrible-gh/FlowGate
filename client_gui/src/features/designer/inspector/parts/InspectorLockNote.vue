<template>
  <!-- flowgate.v02.0051 — the v7 lock line (0035 insp-v4 `.lock-note`): above locked settings,
       "🔒 실행 중" (or the caller's text, e.g. "🔒 위임 진행 중"). -->
  <p class="inspector-lock-note" role="note" data-test="inspector-lock-note">🔒 <slot>{{ text ?? t('gui.designer.inspector.lock.running') }}</slot></p>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

withDefaults(
  defineProps<{
    text?: string | null
  }>(),
  { text: null },
)

const { t } = useI18n()
</script>

<style scoped>
.inspector-lock-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px;
  padding: 5px 11px;
  border: 1px solid #5e4b9e;
  border-radius: 8px;
  background: #1a1733;
  color: #d8cdfd;
  font-size: 12px;
}
</style>
