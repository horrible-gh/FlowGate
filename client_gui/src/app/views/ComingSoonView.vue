<template>
  <section class="gui-coming-soon" :data-nav-key="navKey" aria-labelledby="gui-coming-soon-title">
    <div class="gui-coming-soon-card">
      <span v-if="item" class="gui-coming-soon-icon" :style="{ color: item.accent }"><NavGlyph :name="item.glyph" /></span>
      <span class="gui-coming-soon-badge">{{ t('gui.placeholder.badge') }}</span>
      <h1 id="gui-coming-soon-title">{{ t(`gui.nav.${navKey}.title`) }}</h1>
      <p>{{ t('gui.placeholder.body') }}</p>
      <p class="gui-coming-soon-hint">{{ t('gui.placeholder.legacy_hint', { label: t('nav.switch_to_legacy') }) }}</p>
      <RouterLink to="/" class="gui-coming-soon-back">{{ t('gui.placeholder.back') }}</RouterLink>
    </div>
  </section>
</template>

<script setup lang="ts">
// Placeholder for the v0.2 screens that are not built yet (T0011 §8): it names the screen
// and says plainly that it is not implemented. It never embeds or imports a v0.1 screen.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, useRoute } from 'vue-router'
import NavGlyph from '../components/NavGlyph.vue'
import { APP_NAV_ITEMS, type AppNavKey } from '../navigation'

const { t } = useI18n()
const route = useRoute()

const navKey = computed(() => route.meta.navKey as AppNavKey)
const item = computed(() => APP_NAV_ITEMS.find((entry) => entry.key === navKey.value) ?? null)
</script>

<style scoped>
.gui-coming-soon {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
  overflow: auto;
  padding: 38px 28px;
}

.gui-coming-soon-card {
  width: min(520px, 100%);
  padding: 34px 30px 30px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 10px;
  background: linear-gradient(145deg, rgba(17, 31, 47, 0.9), rgba(11, 23, 36, 0.93));
  text-align: center;
}

.gui-coming-soon-icon {
  display: block;
  width: 48px;
  height: 48px;
  margin: 0 auto 16px;
}

.gui-coming-soon-icon svg {
  width: 48px;
  height: 48px;
}

.gui-coming-soon-badge {
  display: inline-block;
  padding: 3px 10px;
  border: 1px dashed var(--fg2-line-strong);
  border-radius: 999px;
  font-size: 12px;
  color: #95a8bd;
}

h1 {
  margin: 14px 0 0;
  font-size: 26px;
  letter-spacing: -0.04em;
}

p {
  margin: 12px 0 0;
  font-size: 14px;
  line-height: 1.7;
  color: #a5b6c9;
  word-break: keep-all;
}

.gui-coming-soon-hint {
  font-size: 12px;
  color: var(--fg2-muted);
}

.gui-coming-soon-back {
  display: inline-block;
  margin-top: 22px;
  padding: 8px 16px;
  border: 1px solid rgba(75, 163, 255, 0.35);
  border-radius: 18px;
  background: rgba(8, 18, 30, 0.46);
  font-size: 13px;
  color: #62b4ff;
}

@media (max-width: 1120px) {
  .gui-coming-soon {
    overflow: visible;
  }
}
</style>
