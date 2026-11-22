<template>
  <!-- 0060: the one place that says the server cannot be reached. Shown while shared/api.ts's recovery loop runs
       (a read failed after its retries) and gone as soon as a read succeeds again. Panels keep their last content
       and read again by themselves meanwhile (shared/selfHeal); nothing here needs a click or F5. -->
  <Transition name="fg-conn-banner">
    <div
      v-if="lost"
      class="fg-conn-banner"
      role="status"
      aria-live="polite"
      data-test="connection-banner"
      :title="t('gui.connection_banner.hint')"
    >
      <span class="fg-conn-banner-dot" aria-hidden="true" />
      <span>{{ t('gui.connection_banner.reconnecting') }}</span>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { connectionLost } from '@shared/connectionStatus'

const { t } = useI18n()

/** Shown only after the loss lasts this long, so a blip that heals within it never flashes the banner. */
const SHOW_AFTER_MS = 1500

const shown = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

watch(
  () => connectionLost.value,
  (value) => {
    if (timer !== null) clearTimeout(timer)
    timer = null
    if (!value) {
      shown.value = false
      return
    }
    timer = setTimeout(() => {
      timer = null
      shown.value = connectionLost.value
    }, SHOW_AFTER_MS)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (timer !== null) clearTimeout(timer)
})

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

.fg-conn-banner-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: currentColor;
  animation: fg-conn-banner-pulse 1.2s ease-in-out infinite;
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
