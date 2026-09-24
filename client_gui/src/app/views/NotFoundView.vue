<template>
  <section class="gui-not-found" aria-labelledby="gui-not-found-title">
    <div class="gui-not-found-card">
      <h1 id="gui-not-found-title">{{ t('gui.not_found.title') }}</h1>
      <p>{{ t('gui.not_found.body', { path: requested }) }}</p>
      <RouterLink to="/" class="gui-not-found-back">{{ t('gui.placeholder.back') }}</RouterLink>
    </div>
  </section>
</template>

<script setup lang="ts">
// Unknown /app/* path. Rendered in place (no redirect) so the requested URL stays in the
// address bar exactly as typed — the T#1 deep-link contract.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, useRoute } from 'vue-router'

const { t } = useI18n()
const route = useRoute()

const requested = computed(() => `/app${route.path}`)
</script>

<style scoped>
.gui-not-found {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
  overflow: auto;
  padding: 38px 28px;
}

.gui-not-found-card {
  width: min(520px, 100%);
  padding: 30px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 10px;
  background: rgba(12, 24, 38, 0.78);
  text-align: center;
}

h1 {
  margin: 0;
  font-size: 24px;
}

p {
  margin: 12px 0 0;
  font-size: 14px;
  color: #a5b6c9;
  overflow-wrap: anywhere;
}

.gui-not-found-back {
  display: inline-block;
  margin-top: 20px;
  padding: 8px 16px;
  border: 1px solid rgba(75, 163, 255, 0.35);
  border-radius: 18px;
  font-size: 13px;
  color: #62b4ff;
}

@media (max-width: 1120px) {
  .gui-not-found {
    overflow: visible;
  }
}
</style>
