<template>
  <!-- flowgate.v02.0056 F1 — the review history button of a document line (0056 CH §2): a clock icon that
       opens the document's 검수·반려 이력 (AI reviews · rejections · AI responses) in its modeless window.
       Display only: the caller hears `click`; the name and tooltip are the Frame's common wording, with
       the number of entries after it when the caller knows it (`count`). -->
  <IconButton
    class="inspector-history-button"
    :label="label"
    :title="title ?? label"
    variant="secondary"
    size="sm"
    :disabled="disabled"
    data-icon="history"
    @click="emit('click')"
  >
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3.4 8a4.6 4.6 0 1 0 1.35-3.25" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
      <path d="M3 2.9v2.6h2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M8 5.3V8l1.9 1.25" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
    <span v-if="count !== null && count > 0" class="inspector-history-count" data-test="inspector-history-count">{{ count }}</span>
  </IconButton>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../../../components/ui/IconButton.vue'

const props = withDefaults(
  defineProps<{
    /** Entries in the history when known (a small number on the icon); null when not read. */
    count?: number | null
    disabled?: boolean
    /** Hover text when it is not the common wording (why a disabled one cannot open). */
    title?: string | null
  }>(),
  { count: null, disabled: false, title: null },
)

const emit = defineEmits<{ click: [] }>()

const { t } = useI18n()

const label = computed(() =>
  props.count !== null && props.count > 0
    ? t('gui.designer.inspector.slot.history_count', { n: props.count })
    : t('gui.designer.inspector.slot.history'),
)
</script>

<style scoped>
.inspector-history-button {
  position: relative;
}

.inspector-history-count {
  position: absolute;
  top: 1px;
  right: 1px;
  min-width: 13px;
  height: 13px;
  padding: 0 3px;
  border-radius: 7px;
  background: #2d5f9b;
  color: #e8f1ff;
  font-size: 9px;
  font-weight: 700;
  line-height: 13px;
  text-align: center;
}
</style>
