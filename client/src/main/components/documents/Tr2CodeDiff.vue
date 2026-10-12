<template>
  <div class="code-diff" data-testid="tr2-code-diff" tabindex="0" :aria-label="t('main.tr2_body.files.heading')">
    <div v-if="before !== undefined" class="tr2-code-block code-before">
      <b>{{ t('main.tr2_body.files.before') }}</b>
      <div class="code-scroll" tabindex="0" :aria-label="t('main.tr2_body.files.before')">
        <div v-for="(line, index) in before.split('\n')" :key="index" class="code-line"><span class="line-number" aria-hidden="true">{{ index + 1 }}</span><span class="line-sign" aria-hidden="true">−</span><code>{{ line }}</code></div>
      </div>
    </div>
    <div class="tr2-code-block code-after">
      <b>{{ t(before === undefined ? 'main.tr2_body.files.after_only' : 'main.tr2_body.files.after') }}</b>
      <div class="code-scroll" tabindex="0" :aria-label="t('main.tr2_body.files.after')">
        <div v-for="(line, index) in after.split('\n')" :key="index" class="code-line"><span class="line-number" aria-hidden="true">{{ index + 1 }}</span><span class="line-sign" aria-hidden="true">+</span><code>{{ line }}</code></div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
defineProps<{ before?: string; after: string }>()
const { t } = useI18n()
</script>
<style scoped>
.code-diff { font-size: 12px; background: #fcfdff; min-width: 0; border: 1px solid var(--border, #e3e9f1); border-radius: 9px; overflow: auto; max-height: 340px; overscroll-behavior: contain; }
.tr2-code-block { min-width: 0; padding: 0; background: transparent; border-radius: 0; white-space: normal; }
.tr2-code-block > b { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.code-scroll { overflow-x: auto; }
.code-diff:focus-visible, .code-scroll:focus-visible { outline: 2px solid var(--primary, #356ae6); outline-offset: -2px; }
.code-line { display: flex; width: max-content; min-width: 100%; min-height: 27px; font: 12px/1.6 ui-monospace, 'Cascadia Code', Consolas, monospace; }
.code-before .code-line { background: #fff0f0; color: #a43240; }
.code-after .code-line { background: #eaf8f0; color: #146c49; }
.line-number { flex: 0 0 35px; box-sizing: border-box; padding: 0 12px 0 0; text-align: right; color: #a7b3c1; border-right: 1px solid #e9edf4; user-select: none; }
.line-sign { flex: 0 0 26px; text-align: center; padding-top: 0; font-weight: 800; user-select: none; }
code { display: block; padding: 3px 10px 3px 0; white-space: pre; overflow-wrap: normal; word-break: normal; font: inherit; tab-size: 4; }
</style>
