<template>
  <section class="ai-policy" data-test="ai-policy">
    <nav class="policy-nav" :aria-label="t('gui.aiManage.policy.sections_aria')">
      <div class="policy-nav-title">{{ t('gui.aiManage.policy.heading') }}</div>
      <button
        v-for="section in sections"
        :key="section.key"
        type="button"
        class="policy-nav-item"
        :class="{ 'is-active': section.key === active }"
        :aria-current="section.key === active ? 'page' : undefined"
        :data-section="section.key"
        @click="active = section.key"
      >
        {{ t(section.label) }}
      </button>
    </nav>
    <div class="policy-body" :data-section="current.key">
      <!-- Each sub-item keeps its unsaved text while another is open. -->
      <KeepAlive><component :is="current.component" :key="current.key" /></KeepAlive>
    </div>
  </section>
</template>

<script setup lang="ts">
// v02test.0096 F96-3 "방침": the policies the AI follows, one sub-item per policy listed on the left (POLICY_SECTIONS).
// Today that is 질의 alone — the question policy box with its "AI와 정하기", moved here unchanged in what it saves.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { installAiManageMessages } from '../manage/messages'
import { POLICY_SECTIONS } from './policySections'

const props = defineProps<{ section?: string }>()
const composer = useI18n({ useScope: 'global' }); installAiManageMessages(composer)
const { t } = composer
const sections = POLICY_SECTIONS
const active = ref(sections.some((s) => s.key === props.section) ? props.section! : sections[0].key)
const current = computed(() => sections.find((s) => s.key === active.value) ?? sections[0])
</script>

<style scoped>
.ai-policy { container-type: inline-size; display: grid; grid-template-columns: 132px minmax(0, 1fr); gap: 14px; height: 100%; min-height: 0; }
.policy-nav { display: flex; flex-direction: column; gap: 2px; padding-right: 12px; border-right: 1px solid var(--fg2-line); }
.policy-nav-title { padding: 2px 10px 6px; font-size: 11px; font-weight: 700; color: var(--fg2-muted); }
.policy-nav-item { padding: 7px 10px; border: 0; border-radius: var(--fg2-control-radius-sm, 6px); background: transparent; color: var(--fg2-text);
  font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.policy-nav-item:hover { background: color-mix(in srgb, var(--fg2-text, #fff) 6%, transparent); }
.policy-nav-item.is-active { background: var(--fg2-status-info-bg); color: var(--fg2-status-info-fg); font-weight: 700; }
.policy-body { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.policy-body > :deep(*) { flex: 1 1 auto; min-height: 0; }
/* A narrow window: the sub-items go in one row above the editor. */
@container (max-width: 520px) {
  .ai-policy { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); gap: 8px; }
  .policy-nav { flex-direction: row; align-items: center; flex-wrap: wrap; padding: 0 0 8px; border-right: 0; border-bottom: 1px solid var(--fg2-line); }
  .policy-nav-title { padding: 0 6px 0 0; }
}
</style>
