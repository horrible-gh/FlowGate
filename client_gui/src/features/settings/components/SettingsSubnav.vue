<template>
  <div class="subnav-inner">
    <RouterLink v-if="back" :to="back.to" class="subnav-back">
      <SettingsIcon name="back" size="sm" />{{ back.label }}
    </RouterLink>
    <div v-if="title" class="subnav-head">
      <slot name="mark">
        <span class="fgs-tico sm" :class="headTone"><SettingsIcon :name="headIcon" /></span>
      </slot>
      <h3>{{ title }}</h3>
    </div>
    <slot name="head" />
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
          <SettingsIcon :name="item.icon!" />
          <span>{{ item.label }}</span>
        </component>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
// The pages of one settings scope, drawn under that scope in the settings navigation
// (SettingsNav; mockup ⑩, flowgate.v02.0077). An item with `to` is a route link, an item without
// one emits `select` (in-page tabs), a disabled item is a scope that is not part of this step
// ("준비 중"), a `group` item is a small heading. `title` (with the `mark` slot) and `back` are only
// used by the retired Workspace views; the `head` slot holds a picker above the items.
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
    title?: string
    items: SubnavItem[]
    active: string
    headIcon?: SettingsIconName
    headTone?: string
    back?: { to: string; label: string } | null
  }>(),
  { title: '', headIcon: 'globe', headTone: '', back: null },
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
  padding: 8px 10px;
  font-size: 12px;
  color: #83a5c8;
  border-radius: 8px;
}

.subnav-back:hover {
  color: #cbe0f6;
  background: #132b44;
}

.subnav-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 10px 8px;
}

.subnav-head h3 {
  margin: 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subnav-group {
  padding: 10px 10px 4px;
  color: #647e99;
  font-size: 10.5px;
  font-weight: 800;
  letter-spacing: 0.5px;
}

.subitem {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: #9db0c4;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}

.subitem :deep(.fgs-icon) {
  width: 16px;
  height: 16px;
}

.subitem:hover:not(.disabled) {
  background: #132b44;
  color: #e7f2ff;
}

.subitem.on {
  background: #173a61;
  border-color: #265e9d;
  color: #eef7ff;
}

.subitem.disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

@media (max-width: 780px) {
  .subnav-list {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .subnav-list .subnav-group {
    display: none;
  }

  .subitem {
    width: auto;
    border-color: #22405f;
  }
}
</style>
