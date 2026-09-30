<template>
  <section class="dash-hero">
    <div class="dash-hero-text">
      <h1 id="dash-hero-title">
        {{ t('gui.dashboard.greeting_line1') }}<br />{{ t('gui.dashboard.greeting_line2') }}
      </h1>
      <p>{{ t('gui.dashboard.lead') }}</p>
    </div>
    <div class="dash-quote">
      <span class="dash-quote-text">{{ t('gui.dashboard.quote') }}</span>
      <b>{{ t('gui.dashboard.quote_by') }}</b>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<style scoped>
.dash-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 210px;
  align-items: start;
  gap: 24px;
  margin-bottom: 30px;
}

.dash-hero-text {
  min-width: 0;
}

h1 {
  margin: 0;
  font-size: 34px;
  line-height: 1.28;
  letter-spacing: -0.045em;
}

p {
  margin: 18px 0 0;
  font-size: 15px;
  color: #9db0c6;
}

.dash-quote {
  padding-top: 14px;
  text-align: right;
  font-size: 12px;
  line-height: 1.85;
  color: #7f93aa;
}

.dash-quote-text {
  white-space: pre-line;
}

.dash-quote b {
  display: block;
  margin-top: 7px;
  font-weight: 500;
  color: #9fb0c3;
}

@media (max-width: 1380px) {
  h1 {
    font-size: 30px;
  }
}

@media (max-width: 760px) {
  .dash-hero {
    grid-template-columns: minmax(0, 1fr);
  }

  .dash-quote {
    display: none;
  }
}
</style>
