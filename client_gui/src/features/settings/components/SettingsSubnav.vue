<template>
  <div class="subnav-inner">
    <RouterLink v-if="back" :to="back.to" class="subnav-back">
      <SettingsIcon name="back" size="sm" />{{ back.label }}
    </RouterLink>
    <div class="subnav-head">
      <slot name="mark">
        <span class="fgs-tico sm" :class="headTone"><SettingsIcon :name="headIcon" /></span>
      </slot>
      <h3>{{ title }}</h3>
    </div>
    <div class="subnav-list">
      <template v-for="item in items" :key="item.key">
        <div v-if="item.group" class="subnav-group">{{ item.group }}</div>
        <component
          :is="item.to && !item.disabled ? RouterLinkComp : 'button'"
          v-else
          :to="item.to && !item.disabled ? item.to : undefined"
          :type="item.to && !item.disabled ? undefined : 'button'"
          class="subitem"
          :class="{ on: item.key === active, disabled: item.disabled }"
          :aria-current="item.key === active ? 'page' : undefined"
          :aria-disabled="item.disabled || undefined"
          :title="item.disabled ? notReady : undefined"
          :data-subnav="item.key"
          @click="!item.to && !item.disabled && emit('select', item.key)"
        >
          <span class="si"><SettingsIcon :name="item.icon!" /></span>
          <b>{{ item.label }}</b>
        </component>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
// Left sub-navigation of a settings scope (워크스페이스 목록/상세 탭, 시스템 메뉴). An item with
// `to` is a route link, an item without one emits `select` (in-page tabs), a disabled item is
// a scope that is not part of this step ("준비 중").
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SettingsIcon from '../SettingsIcon.vue'
import type { SettingsIconName } from '../icons'

export interface SubnavItem {
  key: string
  label?: string
  icon?: SettingsIconName
  to?: string
  disabled?: boolean
  /** A group heading row instead of an item. */
  group?: string
}

withDefaults(
  defineProps<{
    title: string
    items: SubnavItem[]
    active: string
    headIcon?: SettingsIconName
    headTone?: string
    back?: { to: string; label: string } | null
  }>(),
  { headIcon: 'globe', headTone: '', back: null },
)

const emit = defineEmits<{ select: [key: string] }>()
const { t } = useI18n()
const RouterLinkComp = RouterLink
const notReady = computed(() => t('gui.settings.not_ready'))
</script>

<style scoped>
.subnav-back {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 11px 16px;
  border-bottom: 1px solid var(--fg2-line);
  font-size: 12px;
  color: #83a5c8;
}

.subnav-back:hover {
  color: #cbe0f6;
  background: #0e2036;
}

.subnav-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--fg2-line);
}

.subnav-head h3 {
  margin: 0;
  font-size: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subnav-list {
  padding: 8px;
}

.subnav-group {
  padding: 12px 10px 6px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.9px;
  color: #6f87a0;
}

.subnav-group:first-child {
  padding-top: 6px;
}

.subitem {
  width: 100%;
  min-height: 46px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: #a7bad0;
  display: grid;
  grid-template-columns: 34px 1fr;
  align-items: center;
  text-align: left;
  padding: 0 10px;
  margin-bottom: 3px;
  font: inherit;
  cursor: pointer;
}

.subitem b {
  font-size: 13px;
  color: #d3deea;
}

.subitem .si {
  display: grid;
  place-items: center;
  color: #8fb1d3;
}

.subitem.on {
  background: #12345a;
  border-color: #225184;
}

.subitem.on .si {
  color: #fff;
}

.subitem:hover:not(.disabled) {
  background: #102945;
}

.subitem.disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
</style>
