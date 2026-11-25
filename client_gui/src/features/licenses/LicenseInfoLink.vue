<template>
  <!-- One app-styled "라이선스 정보 보기" line (an info glyph and the text, no browser link look) that opens the license window. -->
  <button type="button" class="lic-open" :data-license-tab="tab" @click.stop="openLicenseWindow(tab)">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.5" /></svg>
    <span><slot /></span>
  </button>
</template>

<script setup lang="ts">
import { openLicenseWindow } from './licenseWindow'

defineProps<{ tab: string }>()
</script>

<style scoped>
.lic-open {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg, #93c5fd);
  font: inherit;
  font-size: 12.5px;
  text-decoration: none;
  cursor: pointer;
}

.lic-open:hover {
  color: var(--fg2-text);
}

.lic-open:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
  border-radius: 4px;
}

.lic-open svg {
  width: 14px;
  height: 14px;
  flex: none;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
}
</style>
