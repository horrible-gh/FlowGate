<template>
  <!-- A card with a destination is a real link (T0011 §14: clickable vs. informational cards
       must differ semantically); one without is an inert, aria-disabled article. -->
  <RouterLink v-if="card.to" :to="card.to" custom v-slot="{ href, navigate }">
    <a :href="href" class="dash-card" :class="cardClasses" :data-card-key="card.key" @click="navigate">
      <span class="dash-card-icon" :style="{ color: card.accent }"><NavGlyph :name="card.glyph" :stroke-width="1.65" /></span>
      <span class="dash-card-title">{{ title }} <span aria-hidden="true">→</span></span>
      <span class="dash-card-desc">{{ desc }}</span>
      <span class="dash-card-action">{{ action }} <span class="dash-action-circle" aria-hidden="true">›</span></span>
    </a>
  </RouterLink>
  <article v-else class="dash-card is-unavailable" :class="cardClasses" :data-card-key="card.key" aria-disabled="true">
    <span class="dash-card-icon" :style="{ color: card.accent }"><NavGlyph :name="card.glyph" :stroke-width="1.65" /></span>
    <span class="dash-card-title">{{ title }}</span>
    <span class="dash-card-desc">{{ desc }}</span>
    <span class="dash-card-action">{{ t('gui.dashboard.unavailable') }}</span>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import NavGlyph from '../components/NavGlyph.vue'
import type { DashboardCard } from './dashboardContent'

const props = defineProps<{ card: DashboardCard }>()
const { t } = useI18n()

const cardClasses = computed(() => [`tone-${props.card.tone}`, `dash-card--${props.card.key}`])
const title = computed(() => t(`gui.dashboard.cards.${props.card.key}.title`))
const desc = computed(() => t(`gui.dashboard.cards.${props.card.key}.desc`))
const action = computed(() => t(`gui.dashboard.cards.${props.card.key}.action`))
</script>

<style scoped>
.dash-card {
  position: relative;
  display: block;
  flex: 0 1 340px;
  width: 340px;
  min-width: 300px;
  max-width: 368px;
  min-height: 255px;
  padding: 30px 26px 22px;
  overflow: hidden;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 10px;
  background: linear-gradient(145deg, rgba(17, 31, 47, 0.9), rgba(11, 23, 36, 0.93));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
  color: var(--fg2-text);
  transition: 0.18s ease;
  cursor: pointer;
}

a.dash-card:hover {
  transform: translateY(-2px);
  border-color: rgba(130, 170, 220, 0.38);
}

.dash-card::before {
  content: '';
  position: absolute;
  inset: 0;
  opacity: 0.62;
  pointer-events: none;
}

.dash-card > * {
  position: relative;
  z-index: 1;
}

.tone-blue::before {
  background: radial-gradient(520px 260px at 15% 0%, rgba(23, 101, 193, 0.26), transparent 55%);
}

.tone-cyan::before {
  background: radial-gradient(520px 260px at 20% 0%, rgba(17, 143, 137, 0.24), transparent 56%);
}

.tone-purple::before {
  background: radial-gradient(520px 260px at 20% 0%, rgba(95, 55, 176, 0.27), transparent 56%);
}

.tone-orange::before {
  background: radial-gradient(520px 260px at 20% 0%, rgba(161, 97, 26, 0.22), transparent 56%);
}

.tone-indigo::before {
  background: radial-gradient(520px 260px at 20% 0%, rgba(75, 53, 166, 0.22), transparent 56%);
}

/* Per-card border colours from the mockup (.project-card, .designer-card, ...). */
.dash-card--projects {
  border-color: rgba(75, 163, 255, 0.65);
}

.dash-card--designer {
  border-color: rgba(73, 223, 208, 0.55);
}

.dash-card--git {
  border-color: rgba(155, 114, 255, 0.58);
}

.dash-card--ai {
  border-color: rgba(75, 201, 231, 0.46);
}

.dash-card--settings {
  border-color: rgba(255, 174, 87, 0.52);
}

.dash-card--quick {
  border-color: rgba(129, 104, 255, 0.48);
}

.dash-card-icon {
  display: block;
  width: 54px;
  height: 54px;
  margin-bottom: 20px;
}

.dash-card-icon svg {
  width: 54px;
  height: 54px;
}

.dash-card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 850;
  font-size: 23px;
}

.dash-card-desc {
  display: block;
  min-height: 48px;
  margin-top: 14px;
  font-size: 14px;
  line-height: 1.7;
  color: #a5b6c9;
  white-space: pre-line;
}

.dash-card-action {
  height: 36px;
  margin-top: 22px;
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: 1px solid currentColor;
  border-radius: 18px;
  background: rgba(8, 18, 30, 0.46);
  font-size: 12px;
  color: #a9cfff;
  opacity: 0.96;
}

.dash-card--projects .dash-card-action {
  color: #62b4ff;
  border-color: rgba(75, 163, 255, 0.35);
}

.dash-card--designer .dash-card-action {
  color: #4ddfd2;
  border-color: rgba(73, 223, 208, 0.35);
}

.dash-card--git .dash-card-action {
  color: #b092ff;
  border-color: rgba(155, 114, 255, 0.36);
}

.dash-card--ai .dash-card-action {
  color: #62b4ff;
  border-color: rgba(75, 163, 255, 0.35);
}

.dash-card--settings .dash-card-action {
  color: #ffc072;
  border-color: rgba(255, 174, 87, 0.35);
}

.dash-card--quick .dash-card-action {
  color: #ab95ff;
  border-color: rgba(129, 104, 255, 0.34);
}

.dash-action-circle {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.09);
  font-size: 16px;
}

/* Inert card: no hover lift, no pointer, action pill reads "준비 중". */
.dash-card.is-unavailable {
  cursor: default;
}

.dash-card.is-unavailable .dash-card-action {
  justify-content: center;
  border-style: dashed;
  opacity: 0.7;
}

@media (max-width: 1380px) {
  .dash-card {
    min-height: 240px;
    padding: 26px 22px 20px;
  }
}

@media (max-width: 1120px) {
  .dash-card {
    flex: 1 1 calc((100% - 14px) / 2);
    width: auto;
    max-width: none;
  }
}

@media (max-width: 760px) {
  .dash-card {
    flex: 1 1 100%;
    min-width: 0;
  }
}
</style>
