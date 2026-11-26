<template>
  <!-- One "no permission" box for every 403 (shared/api.ts reports it, shared/permissions/forbidden.ts keeps it).
       It stays until it is closed or the person leaves for their projects, so a refused read is never a blank
       screen or a spinner that does not stop. -->
  <div
    v-if="event"
    class="fg-forbidden"
    role="alert"
    aria-live="assertive"
    data-test="forbidden-notice"
  >
    <div class="fg-forbidden-card">
      <h2 class="fg-forbidden-title" data-test="forbidden-title">{{ t('gui.permissions.forbidden.title') }}</h2>
      <p class="fg-forbidden-body" data-test="forbidden-body">{{ t('gui.permissions.forbidden.body') }}</p>
      <p v-if="event.path" class="fg-forbidden-detail" data-test="forbidden-detail">
        {{ t('gui.permissions.forbidden.detail', { path: event.path }) }}
      </p>
      <div class="fg-forbidden-actions">
        <button type="button" class="fg-forbidden-back" data-test="forbidden-back" @click="goHome">
          {{ t('gui.permissions.forbidden.back') }}
        </button>
        <button type="button" class="fg-forbidden-close" data-test="forbidden-close" @click="dismiss">
          {{ t('gui.permissions.forbidden.close') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useForbidden } from '@shared/permissions/forbidden'

const { t } = useI18n()
const router = useRouter()
const { event, dismiss } = useForbidden()

/** Leave the refused screen for the dashboard (the person's own projects) and close the box. */
function goHome(): void {
  dismiss()
  void router.push('/').catch(() => undefined)
}
</script>

<style scoped>
.fg-forbidden {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.35);
}
.fg-forbidden-card {
  width: min(440px, calc(100vw - 32px));
  padding: 20px 22px;
  border-radius: 10px;
  border: 1px solid var(--fg2-status-danger-border, #e0707f);
  background: var(--fg2-pop-surface, #fff);
  color: var(--fg2-text, #1d2939);
  font-family: var(--fg2-font);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25);
}
.fg-forbidden-title { margin: 0 0 8px; font-size: 16px; font-weight: 700; color: var(--fg2-status-danger-fg, #b42318); }
.fg-forbidden-body { margin: 0 0 8px; font-size: 13px; line-height: 1.55; }
.fg-forbidden-detail { margin: 0 0 14px; font-size: 11px; opacity: 0.65; word-break: break-all; }
.fg-forbidden-actions { display: flex; gap: 8px; justify-content: flex-end; }
.fg-forbidden-actions button {
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid var(--fg2-control-hover-border, #98a2b3);
  background: var(--fg2-control-surface-flat, #fff);
  color: inherit;
  font-size: 12px;
  cursor: pointer;
}
.fg-forbidden-back { font-weight: 600; }
</style>
