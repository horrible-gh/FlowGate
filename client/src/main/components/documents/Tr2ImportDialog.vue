<template>
  <div v-show="open" class="import-backdrop" data-testid="tr2-import-dialog" @click.self="requestClose">
    <section ref="dialog" class="import-dialog" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1">
      <header><h2>{{ title }}</h2><button type="button" class="close" :disabled="busy" :aria-label="t('main.tr2_body.actions.cancel')" @click="requestClose">×</button></header>
      <div class="import-body"><slot /></div>
      <footer v-if="!hasActions"><button type="button" :disabled="busy" @click="requestClose">{{ t('main.tr2_body.actions.cancel') }}</button></footer>
    </section>
  </div>
</template>
<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
const props = defineProps<{ open: boolean; busy: boolean; title: string; hasActions?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const dialog = ref<HTMLElement | null>(null)
let returnFocus: HTMLElement | null = null
let previousOverflow: string | null = null
function requestClose() { if (!props.busy) emit('close') }
function restore() {
  if (previousOverflow !== null) { document.body.style.overflow = previousOverflow; previousOverflow = null }
  if (returnFocus?.isConnected) returnFocus.focus()
  returnFocus = null
  document.removeEventListener('keydown', onKey)
}
function onKey(event: KeyboardEvent) {
  if (!props.open) return
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); requestClose(); return }
  if (event.key !== 'Tab') return
  const nodes = Array.from(dialog.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled):not([type=hidden]), [tabindex="0"], summary') ?? []).filter(node => node.getClientRects().length > 0)
  const first = nodes[0], last = nodes.at(-1)
  if (!first) { event.preventDefault(); dialog.value?.focus(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
watch(() => props.open, async open => {
  if (!open) { restore(); return }
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', onKey)
  await nextTick()
  if (props.open) dialog.value?.focus()
}, { immediate: true })
onBeforeUnmount(restore)
</script>
<style scoped>
.import-backdrop { position: fixed; inset: 0; z-index: 1200; background: #12233a8c; display: flex; align-items: center; justify-content: center; padding: 18px; box-sizing: border-box; }
.import-dialog:focus { outline: none; }
.import-dialog { width: min(970px, 100%); max-height: min(91dvh, 960px); display: flex; flex-direction: column; min-width: 0; background: var(--surface, #fff); color: var(--text, #202b3a); border: 1px solid var(--border, #e3e9f1); border-radius: 14px; box-shadow: 0 18px 70px #1c2f4c40; }
header { display: flex; align-items: center; justify-content: space-between; padding: 20px 23px 17px; border-bottom: 1px solid var(--border, #e3e9f1); gap: 12px; }
h2 { margin: 0; font-size: 17px; }
.import-body { min-height: 0; overflow: auto; padding: 20px 23px; overscroll-behavior: contain; }
footer { display: flex; justify-content: flex-end; padding: 14px 23px; background: #fafbfe; border-top: 1px solid var(--border, #e3e9f1); }
button { padding: 7px 11px; border: 1px solid var(--border, #e3e9f1); border-radius: 8px; background: var(--surface, #fff); color: inherit; font: inherit; font-size: 12px; cursor: pointer; }
button.close { font-size: 22px; border: 0; padding: 0 6px; }
button:disabled { opacity: .45; cursor: not-allowed; }
button:focus-visible { outline: 2px solid var(--primary, #356ae6); outline-offset: 2px; }
@media(max-width: 560px) { .import-backdrop { padding: 12px; } .import-dialog { max-height: calc(100dvh - 24px); } header, .import-body, footer { padding: 14px; } }
</style>
