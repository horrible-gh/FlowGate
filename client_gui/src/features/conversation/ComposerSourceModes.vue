<template>
  <!-- flowgate.v02.0047 — AI source mode as icons (R0001 §2.3-8): "읽기" / "실행" / "편집" / 1single edit, each
       with its name, tooltip and pressed state (실행 = commands only, no source/document edit —
       the order comes from api.ts SOURCE_MODES). Server-confirmed only: the highlight moves once
       the PATCH answers. the earlier change the earlier implementation (WP answer 2): `locked` — the Flow's Graph run is
       running / paused / stopping — only permits switching to read_only. Execute / edit / edit-once stay off and say why
       (the current one still shows pressed). -->
  <div
    v-if="modes.length > 0"
    class="csm"
    :class="{ 'is-locked': locked }"
    role="group"
    data-test="conv-source-mode"
    :data-locked="locked ? 'true' : 'false'"
    :aria-label="t('gui.conversation.composer.source_mode')"
    :title="locked ? t('gui.conversation.composer.source_locked') : undefined"
  >
    <button
      v-for="mode in modes"
      :key="mode"
      type="button"
      :class="{ 'is-on': current === mode }"
      :aria-pressed="current === mode"
      :aria-label="t(`gui.conversation.composer.source.${mode}`)"
      :title="locked && mode !== 'read_only' ? t('gui.conversation.composer.source_locked') : t(`gui.conversation.composer.source_tip.${mode}`)"
      :data-mode="mode"
      :disabled="disabled || (locked && mode !== 'read_only') || pending !== null"
      @click="emit('set', mode)"
    >
      <span v-if="pending === mode" aria-hidden="true">…</span>
      <svg v-else-if="mode === 'read_only'" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
        <circle cx="12" cy="12" r="2.5" />
      </svg>
      <svg v-else-if="mode === 'execute'" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M8 5.5v13l10.5-6.5L8 5.5Z" />
      </svg>
      <svg v-else-if="mode === 'edit'" viewBox="0 0 24 24" aria-hidden="true">
        <path d="m15 5 4 4M4 20l4.5-.9L19 8.5a2.8 2.8 0 0 0-4-4L4.5 15 4 20Z" />
      </svg>
      <span v-else class="csm-once" aria-hidden="true">✎¹</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { SourceAccessMode } from './types'

defineProps<{
  modes: readonly SourceAccessMode[]
  current: SourceAccessMode
  pending: SourceAccessMode | null
  disabled?: boolean
  /** the Flow's Graph run blocks execute / edit modes; switching to read_only stays available. */
  locked?: boolean
}>()

const emit = defineEmits<{ set: [mode: SourceAccessMode] }>()

const { t } = useI18n()
</script>

<style scoped>
.csm {
  flex: 0 0 auto;
  display: flex;
  height: 27px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  overflow: hidden;
}

.csm button {
  width: 26px;
  height: 25px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-right: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.csm button:last-child {
  border-right: 0;
}

.csm button:hover:not(:disabled) {
  color: var(--fg2-text);
}

.csm button {
  position: relative;
}

.csm button.is-on {
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
}

/* Selected button: icon takes a per-mode colour (theme status tokens) + a short underline as a
   non-colour cue. The underline sits inside the 26px cell, so the width does not change. */
.csm button.is-on[data-mode='read_only'] { color: var(--fg2-status-info-fg); }
.csm button.is-on[data-mode='execute'] { color: var(--fg2-status-warning-fg); }
.csm button.is-on[data-mode='edit_once'] { color: #b79cff; }
.csm button.is-on[data-mode='edit'] { color: var(--fg2-status-danger-fg); }

.csm button.is-on::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: 3px;
  width: 10px;
  height: 2px;
  transform: translateX(-50%);
  border-radius: 1px;
  background: currentColor;
}

.csm button.is-on svg {
  stroke-width: 2.2;
}

.csm button:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.csm button:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

.csm svg {
  width: 14px;
  height: 14px;
  stroke: currentColor;
  stroke-width: 1.8;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.csm-once {
  font-size: 13px;
  letter-spacing: -0.5px;
}
</style>
