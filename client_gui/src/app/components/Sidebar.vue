<template>
  <aside class="gui-sidebar">
    <nav class="gui-sidebar-nav" :aria-label="t('gui.nav.aria')">
      <RouterLink v-for="item in APP_NAV_ITEMS" :key="item.key" :to="item.path" custom v-slot="{ href, navigate }">
        <a
          :href="href"
          class="gui-nav-item"
          :class="{ active: activeKey === item.key }"
          :aria-current="activeKey === item.key ? 'page' : undefined"
          :data-nav-key="item.key"
          @click="onNavClick(item, navigate, $event)"
        >
          <span class="gui-nav-icon" :style="{ color: item.accent }"><NavGlyph :name="item.glyph" /></span>
          <span class="gui-nav-text">
            <span class="gui-nav-title">{{ t(`gui.nav.${item.key}.title`) }}</span>
            <span class="gui-nav-sub">{{ t(`gui.nav.${item.key}.sub`) }}</span>
          </span>
        </a>
      </RouterLink>
    </nav>

    <div class="gui-sidebar-bottom">
      <!-- No v0.2 help destination exists yet: shown as a disabled control, not a dead link. -->
      <Button variant="ghost" size="lg" class="gui-help" disabled :title="t('gui.sidebar.help_unavailable')">
        <span class="gui-help-mark" aria-hidden="true">?</span>
        <span>{{ t('gui.sidebar.help') }}</span>
      </Button>
      <div class="gui-version">
        {{ t('gui.sidebar.version') }}<br />{{ t('gui.sidebar.build', { build: BUILD_STAMP }) }}
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, useRoute } from 'vue-router'
import Button from '@/components/ui/Button.vue'
import { useProjectStore } from '@/features/project'
import { APP_NAV_ITEMS, type AppNavItem, type AppNavKey } from '../navigation'
import NavGlyph from './NavGlyph.vue'

const { t } = useI18n()
const route = useRoute()
const projectStore = useProjectStore()

// Build date injected by vite.config.ts `define` — the real build, not a hand-kept string.
const BUILD_STAMP = __FG_GUI_BUILD__

// Active state follows the router (T0011 §12): each screen route carries meta.navKey, so a
// deeper path such as /app/projects/x still highlights its section, and an unknown /app/*
// path highlights nothing.
const activeKey = computed(() => (route.meta.navKey as AppNavKey | undefined) ?? null)

// A plain, unmodified left click — the same check vue-router's own <RouterLink> click
// handler performs before calling preventDefault(). Anything else (ctrl/meta/shift/alt,
// a non-primary button) must fall through to the native <a href> so opening the /projects
// placeholder in a new tab still works.
function isPlainClick(event: MouseEvent): boolean {
  return (
    event.button === 0 &&
    !event.metaKey &&
    !event.altKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.defaultPrevented
  )
}

// T0010 (T#3) §1/§2: Project has its own Dialog, not a routed /projects screen. Clicking
// this entry must keep the current route and open the single Project Dialog through the
// feature's public interface (D0004 §4.1) — not navigate to the /projects "준비 중"
// placeholder that still exists underneath for direct/deep links.
function onNavClick(item: AppNavItem, navigate: (e: MouseEvent) => void, event: MouseEvent): void {
  if (item.key === 'projects' && isPlainClick(event)) {
    event.preventDefault()
    projectStore.openDialog()
    return
  }
  navigate(event)
}
</script>

<style scoped>
.gui-sidebar {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 24px 18px 20px;
  border-right: 1px solid var(--fg2-line);
  background: linear-gradient(180deg, rgba(8, 18, 30, 0.95), rgba(8, 17, 28, 0.88));
}

.gui-sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.gui-nav-item {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
  padding: 13px 14px;
  border-radius: 8px;
  color: #e6eef8;
  transition: 0.16s ease;
}

.gui-nav-item:hover {
  background: rgba(255, 255, 255, 0.035);
}

.gui-nav-item.active {
  background: linear-gradient(90deg, rgba(57, 114, 183, 0.38), rgba(44, 82, 128, 0.24));
  border: 1px solid rgba(86, 155, 235, 0.18);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.gui-nav-icon {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  display: grid;
  place-items: center;
}

.gui-nav-icon svg {
  width: 28px;
  height: 28px;
}

.gui-nav-text {
  display: block;
  min-width: 0;
}

.gui-nav-title,
.gui-nav-sub {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gui-nav-title {
  font-weight: 750;
  font-size: 15px;
}

.gui-nav-sub {
  margin-top: 3px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.gui-sidebar-bottom {
  margin-top: auto;
  padding-top: 18px;
  border-top: 1px solid var(--fg2-line);
}

.gui-help {
  width: 100%;
  display: flex;
  justify-content: flex-start;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  border: 0;
  background: none;
  color: #c7d5e6;
  font-size: 14px;
  text-align: left;
  cursor: not-allowed;
}

/* Button's own `.ui-btn` (font-weight: 650) and `:disabled` (dimmed opacity) rules would
   otherwise leak into this always-disabled control, which HEAD never bolded or dimmed.
   Combined selectors guarantee the override wins regardless of build order. */
.gui-help.ui-btn {
  font-weight: normal;
}

.gui-help.ui-btn:disabled {
  opacity: 1;
}

.gui-help-mark {
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  display: grid;
  place-items: center;
  border: 1px solid #8fa4bd;
  border-radius: 50%;
}

.gui-version {
  padding: 18px 12px 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--fg2-muted);
}

/* The mockup hides the sidebar below 760px, which would leave no way to navigate
   (T0011 §14). Instead it collapses into a horizontally scrollable strip of titles. */
@media (max-width: 760px) {
  .gui-sidebar {
    flex-direction: row;
    overflow-x: auto;
    overflow-y: hidden;
    padding: 8px 12px;
    border-right: 0;
    border-bottom: 1px solid var(--fg2-line);
  }

  .gui-sidebar-nav {
    flex-direction: row;
  }

  .gui-nav-item {
    flex: 0 0 auto;
    gap: 8px;
    padding: 8px 12px;
  }

  .gui-nav-icon {
    width: 22px;
    height: 22px;
    flex-basis: 22px;
  }

  .gui-nav-icon svg {
    width: 20px;
    height: 20px;
  }

  .gui-nav-sub,
  .gui-sidebar-bottom {
    display: none;
  }
}
</style>
