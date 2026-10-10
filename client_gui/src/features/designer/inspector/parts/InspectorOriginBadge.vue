<template>
  <!-- flowgate.v02.0051 — the v7 origin badge (0035 insp-v7 `.org-badge`): who put the Node on the
       Canvas — 🤖 AI (AI delegation expanded it) or ⇉ WP (a human work plan expanded it). A Node a
       person placed from the Palette has none. Shown in the header and on the settings origin line;
       `label` replaces the default text (the WP card's "작성 사람 / AI"). -->
  <span
    class="inspector-origin-badge"
    :class="`inspector-origin-badge--${origin}`"
    :title="title ?? t(`gui.designer.inspector.origin.${origin}_title`)"
    data-test="inspector-origin-badge"
    :data-origin="origin"
  >{{ label ?? t(`gui.designer.inspector.origin.${origin}`) }}</span>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

withDefaults(
  defineProps<{
    origin: 'ai' | 'wp'
    label?: string | null
    title?: string | null
  }>(),
  { label: null, title: null },
)

const { t } = useI18n()
</script>

<style scoped>
.inspector-origin-badge {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border: 1px solid #5e4b9e;
  border-radius: 6px;
  background: #201a3c;
  color: #d3c4ff;
  font-size: 10.5px;
  font-weight: 750;
  white-space: nowrap;
}

.inspector-origin-badge--wp {
  border-color: #1f6b74;
  background: #0c2a33;
  color: #7fe3ec;
}
</style>
