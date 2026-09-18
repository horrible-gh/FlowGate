<template>
  <div class="fg-dialog-header">
    <div class="fg-dialog-header__main">
      <span v-if="icon || $slots.icon" class="fg-dialog-header__icon">
        <slot name="icon">
          <AppIcon v-if="icon" :name="icon" />
        </slot>
      </span>
      <div class="fg-dialog-header__text">
        <!-- The id comes from DialogShell, so `aria-labelledby` always points at this
             element and never at an id nothing rendered (L0009 §2 "ARIA"). Using the
             `title` slot replaces the text, not the accessibility source. -->
        <h2 :id="titleId" class="fg-dialog-header__title">
          <slot name="title">{{ title }}</slot>
        </h2>
        <p v-if="subtitle || $slots.subtitle" class="fg-dialog-header__subtitle">
          <slot name="subtitle">{{ subtitle }}</slot>
        </p>
      </div>
    </div>
    <div class="fg-dialog-header__actions">
      <slot name="actions" />
      <button
        v-if="closeable"
        type="button"
        class="fg-dialog-header__close"
        :aria-label="t('common.close')"
        @click="emit('close')"
      >
        <AppIcon name="x" />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Common dialog title bar.
 *
 * flowgate.default.0560 T0012 §2.4 / design: D0008 §2 "DialogHeader",
 * logic: L0009 §2 "DialogHeader 로직 계약".
 *
 * The X button owns no state. It emits `close`, the wrapper turns that into
 * `request_close(dialog, 'header')`, and the feature decides — the same single path
 * ESC and backdrop take. A blocking dialog is handed `closeable=false` from above and
 * simply has no X.
 */
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'

import AppIcon from '@shared/AppIcon.vue'

import { dialogAriaKey, dialogTitleId } from './dialogTypes'

export interface DialogHeaderProps {
  title: string
  icon?: string
  subtitle?: string
  closeable?: boolean
}

// `closeable` is opt-out (L0009 §1 boolean props 기본값): omitted means a closable
// header, never Vue's implicit `false`.
withDefaults(defineProps<DialogHeaderProps>(), {
  closeable: true,
  icon: undefined,
  subtitle: undefined,
})

const emit = defineEmits<{ close: [] }>()

const { t } = useI18n()

// Standalone use (outside a DialogShell) still needs a stable id for the title element.
const aria = inject(dialogAriaKey, { titleId: dialogTitleId('standalone'), descriptionId: '' })
// Read through a computed: the shell publishes the id as a getter, so caller-supplied
// `titleId` changes keep flowing through instead of freezing at setup time.
const titleId = computed(() => aria.titleId)
</script>
