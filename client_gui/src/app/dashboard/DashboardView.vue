<template>
  <div class="dash">
    <section class="dash-main" aria-labelledby="dash-hero-title">
      <DashboardHero />

      <section class="dash-card-grid" :aria-label="t('gui.dashboard.cards_aria')">
        <DashboardActionCard v-for="card in DASHBOARD_CARDS" :key="card.key" :card="card" />
      </section>

      <DashboardTip />
    </section>

    <aside class="dash-rightbar" :aria-label="t('gui.dashboard.side_aria')">
      <DashboardSidePanel v-for="panel in DASHBOARD_PANELS" :key="panel" :panel="panel" />
      <div class="dash-footer-mark">{{ t('gui.dashboard.footer_mark') }}</div>
    </aside>
  </div>
</template>

<script setup lang="ts">
// Dashboard page (T0011 §7): composes the hero, the screen shortcut cards, the tip and the
// right-hand summary panels. Layout only — each part is its own component.
import { useI18n } from 'vue-i18n'
import DashboardActionCard from './DashboardActionCard.vue'
import DashboardHero from './DashboardHero.vue'
import DashboardSidePanel from './DashboardSidePanel.vue'
import DashboardTip from './DashboardTip.vue'
import { DASHBOARD_CARDS, DASHBOARD_PANELS } from './dashboardContent'

const { t } = useI18n()
</script>

<style scoped>
.dash {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--fg2-rightbar-w);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.dash-main {
  min-width: 0;
  overflow: auto;
  padding: 38px 28px 26px 36px;
}

.dash-card-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: stretch;
  align-content: flex-start;
}

.dash-rightbar {
  min-width: 0;
  overflow: auto;
  padding: 18px;
  border-left: 1px solid var(--fg2-line);
  background: linear-gradient(180deg, rgba(8, 17, 28, 0.74), rgba(7, 15, 24, 0.86));
}

.dash-footer-mark {
  margin: 16px 4px 6px;
  text-align: right;
  font-size: 11px;
  font-style: italic;
  color: #64758a;
}

@media (min-width: 1800px) {
  .dash-main {
    padding-left: 44px;
    padding-right: 34px;
  }

  .dash-card-grid {
    column-gap: 18px;
  }
}

@media (max-width: 1380px) {
  .dash-card-grid {
    gap: 14px;
  }
}

@media (max-width: 1120px) {
  .dash {
    grid-template-columns: minmax(0, 1fr);
    overflow: visible;
  }

  .dash-main {
    overflow: visible;
  }

  .dash-rightbar {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    overflow: visible;
    border-left: 0;
    border-top: 1px solid var(--fg2-line);
  }

  .dash-footer-mark {
    grid-column: 1 / -1;
  }
}

@media (max-width: 760px) {
  .dash-main {
    padding: 26px 16px;
  }

  .dash-card-grid {
    gap: 12px;
  }

  .dash-rightbar {
    grid-template-columns: minmax(0, 1fr);
    padding: 14px;
  }
}
</style>
