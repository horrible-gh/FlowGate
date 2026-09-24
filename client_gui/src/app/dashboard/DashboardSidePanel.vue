<template>
  <section class="dash-sidepanel" :data-panel-key="panel" :aria-labelledby="headingId">
    <div class="dash-panel-head">
      <h2 :id="headingId" class="dash-panel-title">{{ t(`gui.dashboard.panels.${panel}`) }}</h2>
      <span class="dash-panel-status">{{ t('gui.dashboard.unavailable') }}</span>
    </div>
    <p class="dash-panel-empty">{{ t('gui.dashboard.panel_empty') }}</p>
  </section>
</template>

<script setup lang="ts">
// One right-hand summary panel. The mockup fills these with sample rows and counts; no
// v0.2 data source exists for them yet (T0011 §7/§9: don't fake features), so the panel
// keeps its place and heading and states plainly that it is not connected.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { DashboardPanelKey } from './dashboardContent'

const props = defineProps<{ panel: DashboardPanelKey }>()
const { t } = useI18n()

const headingId = computed(() => `dash-panel-${props.panel}`)
</script>

<style scoped>
.dash-sidepanel {
  margin-bottom: 12px;
  padding: 16px 16px 14px;
  border: 1px solid var(--fg2-line);
  border-radius: 10px;
  background: linear-gradient(180deg, rgba(14, 28, 43, 0.9), rgba(11, 23, 36, 0.88));
}

.dash-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 13px;
}

.dash-panel-title {
  margin: 0;
  font-weight: 800;
  font-size: 16px;
}

.dash-panel-status {
  flex: 0 0 auto;
  padding: 2px 8px;
  border: 1px dashed var(--fg2-line-strong);
  border-radius: 999px;
  font-size: 11px;
  color: #95a8bd;
}

.dash-panel-empty {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: #7f93a9;
  word-break: keep-all;
}

@media (max-width: 1120px) {
  .dash-sidepanel {
    margin: 0;
  }
}
</style>
