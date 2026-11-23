<template>
  <div class="me-theme-panel" data-test="me-theme-panel">
    <div v-if="loading" role="status">{{ t('gui.common.loading') }}</div>
    <div v-else-if="loadFailed" role="alert">{{ t('gui.settings.self.errors.load_failed') }} <button type="button" @click="load">{{ t('gui.common.retry') }}</button></div>
    <div v-else class="fgs-row">
      <div class="fgs-row-label"><label :for="fieldId">{{ t('gui.taskbar.style') }}</label></div>
      <div class="fgs-row-ctrl">
        <select :id="fieldId" v-model="selected" class="fgs-input" data-test="taskbar-style" :disabled="saving" @change="save">
          <option v-for="style in TASKBAR_STYLES" :key="style" :value="style">{{ t(`gui.taskbar.style_${style}`) }}</option>
        </select>
        <button type="button" class="fgs-btn" :disabled="saving" @click="save">{{ t('gui.common.save') }}</button>
        <span v-if="saving" role="status">{{ t('gui.settings.saving') }}</span>
        <span v-else-if="saved" role="status">{{ t('gui.settings.saved') }}</span>
      </div>
    </div>
    <p v-if="saveFailed" class="fgs-error" role="alert">{{ t('gui.settings.self.errors.save_failed') }}</p>
  </div>
</template>
<script setup lang="ts">
// The theme setting itself, without a frame: the settings page and the Designer menu window
// both render this one component, so a change here shows in both places.
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import '../settings.css'
import { getUiSettings, saveTaskbarStyle } from '../meApi'
import { modelessStyle, normalizeTaskbarStyle, TASKBAR_STYLES, TASKBAR_STYLE_FIELD, type TaskbarStyle } from '@/core/overlay/modelessRegistry'
// The page and the window can be open together, so each instance needs its own field id.
withDefaults(defineProps<{ fieldId?: string }>(), { fieldId: 'taskbar-style' })
const { t } = useI18n()
const selected = ref<TaskbarStyle>('flowgate')
let confirmed: TaskbarStyle = 'flowgate'
const loading = ref(true)
const saving = ref(false)
const saved = ref(false)
const loadFailed = ref(false)
const saveFailed = ref(false)
async function load() {
  loading.value = true; loadFailed.value = false
  try {
    const body = await getUiSettings()
    selected.value = confirmed = normalizeTaskbarStyle(body.settings?.[TASKBAR_STYLE_FIELD])
    modelessStyle.value = confirmed
  } catch { loadFailed.value = true }
  finally { loading.value = false }
}
async function save() {
  if (saving.value) return
  saving.value = true; saved.value = false; saveFailed.value = false
  modelessStyle.value = selected.value
  try {
    const body = await saveTaskbarStyle(selected.value)
    selected.value = confirmed = normalizeTaskbarStyle(body.settings?.[TASKBAR_STYLE_FIELD])
    modelessStyle.value = confirmed
    saved.value = true
  } catch { selected.value = confirmed; modelessStyle.value = confirmed; saveFailed.value = true }
  finally { saving.value = false }
}
onMounted(load)
</script>
