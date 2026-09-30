<template>
  <button type="button" class="hdr-btn gui-switch-btn" @click="onClick">
    <AppIcon name="arrows-left-right" /><span>{{ label }}</span>
  </button>
</template>

<script setup lang="ts">
// T0009 §2/§9: one switch control shared by v0.1's AppHeader and v0.2's Topbar so the
// two stay symmetric by construction instead of by copy-pasted markup. It is a plain
// labeled button (not icon-only), keyboard-focusable by default, styled with the same
// .hdr-btn secondary-action class Settings/Logout already use.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from './AppIcon.vue'
import { switchGuiGeneration, type GuiGeneration } from './guiSwitch'

const props = defineProps<{ target: GuiGeneration }>()
const { t } = useI18n()

const label = computed(() =>
  props.target === 'app' ? t('nav.switch_to_app') : t('nav.switch_to_legacy'),
)

function onClick() {
  switchGuiGeneration(props.target)
}
</script>
