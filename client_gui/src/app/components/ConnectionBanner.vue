<template>
  <!-- 0060: the one place that says the server cannot be reached. Shown while shared/api.ts's recovery loop runs
       (a read failed after its retries) and gone as soon as a read succeeds again. Panels keep their last content
       and read again by themselves meanwhile (shared/selfHeal). When the loss lasts beyond STUCK_AFTER_MS the
       app is most likely not getting back on its own (22:09: the browser refused the server's renewed
       certificate, which no retry from this page can get past), so the banner says so and offers [새로 고침]. -->
  <Transition name="fg-conn-banner">
    <div
      v-if="lost"
      class="fg-conn-banner"
      :class="{ 'is-stuck': stuck }"
      :role="stuck ? 'alert' : 'status'"
      :aria-live="stuck ? 'assertive' : 'polite'"
      data-test="connection-banner"
      :title="stuck ? undefined : t('gui.connection_banner.hint')"
    >
      <span class="fg-conn-banner-dot" aria-hidden="true" />
      <span>{{ stuck ? t('gui.connection_banner.unknown_error') : t('gui.connection_banner.reconnecting') }}</span>
      <button
        v-if="stuck"
        type="button"
        class="fg-conn-banner-reload"
        data-test="connection-banner-reload"
        @click="reloadPage"
      >
        {{ t('gui.connection_banner.reload') }}
      </button>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { connectionLost } from '@shared/connectionStatus'

const props = defineProps<{
  /** Reloads the page; tests pass their own (jsdom cannot replace window.location.reload). */
  reload?: () => void
}>()

const { t } = useI18n()

/** Shown only after the loss lasts this long, so a blip that heals within it never flashes the banner. */
const SHOW_AFTER_MS = 1500
/** A loss lasting this long (from its start) turns the banner into "unknown error" with a reload button. */
const STUCK_AFTER_MS = 20_000

const shown = ref(false)
const stuck = ref(false)
let showTimer: ReturnType<typeof setTimeout> | null = null
let stuckTimer: ReturnType<typeof setTimeout> | null = null

function clearTimers() {
  if (showTimer !== null) clearTimeout(showTimer)
  if (stuckTimer !== null) clearTimeout(stuckTimer)
  showTimer = null
  stuckTimer = null
}

watch(
  () => connectionLost.value,
  (value) => {
    clearTimers()
    if (!value) {
      shown.value = false
      stuck.value = false
      return
    }
    showTimer = setTimeout(() => {
      showTimer = null
      shown.value = connectionLost.value
    }, SHOW_AFTER_MS)
    stuckTimer = setTimeout(() => {
      stuckTimer = null
      stuck.value = connectionLost.value
    }, STUCK_AFTER_MS)
  },
  { immediate: true },
)

onBeforeUnmount(clearTimers)

function reloadPage() {
  if (props.reload) props.reload()
  else window.location.reload()
}

const lost = computed(() => shown.value)
</script>

<style scoped>
.fg-conn-banner {
  position: fixed;
  top: 8px;
  left: 50%;
  z-index: 10000;
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: calc(100vw - 32px);
  padding: 6px 14px;
  transform: translateX(-50%);
  border: 1px solid var(--fg2-warning-border, #e0b252);
  border-radius: 999px;
  background: var(--fg2-warning-bg, #fff6e0);
  color: var(--fg2-warning-text, #6b4a00);
  font-family: var(--fg2-font);
  font-size: 12px;
  line-height: 1.4;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.12);
  pointer-events: auto;
}

.fg-conn-banner.is-stuck {
  padding-right: 6px;
}

.fg-conn-banner-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: currentColor;
  animation: fg-conn-banner-pulse 1.2s ease-in-out infinite;
}

.is-stuck .fg-conn-banner-dot {
  animation: none;
}

.fg-conn-banner-reload {
  flex: 0 0 auto;
  padding: 2px 10px;
  border: 1px solid currentColor;
  border-radius: 999px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.fg-conn-banner-reload:hover {
  background: rgba(0, 0, 0, 0.06);
}

.fg-conn-banner-reload:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 1px;
}

@keyframes fg-conn-banner-pulse {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}

.fg-conn-banner-enter-active,
.fg-conn-banner-leave-active {
  transition: opacity 0.2s ease;
}

.fg-conn-banner-enter-from,
.fg-conn-banner-leave-to {
  opacity: 0;
}
</style>
