<template>
  <!-- flowgate.v02.0030 — "팔레트 종류 추가" dialog (03-designer-shell `.palette-modal`, R0001 §2.2):
       분류 / 이름 / 아이콘 / 색상. Backdrop, ESC and focus belong to ModalShell; this dialog only
       builds the item and emits it — the Palette decides where it goes. -->
  <ModalShell :open="open" size="md" close-on-backdrop @close="emit('close')">
    <template #header="{ titleId }">
      <div class="palette-add-head">
        <h2 :id="titleId" class="palette-add-title">{{ t('gui.designer.palette.add_dialog.title') }}</h2>
        <IconButton :label="t('gui.designer.palette.add_dialog.close')" variant="ghost" size="sm" @click="emit('close')">✕</IconButton>
      </div>
    </template>

    <form class="palette-add-form" data-test="designer-palette-add-form" @submit.prevent="submit">
      <div class="palette-add-row">
        <label class="palette-add-label" :for="`${uid}-category`">{{ t('gui.designer.palette.add_dialog.category') }}</label>
        <Select
          :id="`${uid}-category`"
          v-model="category"
          :options="categoryOptions"
          data-test="designer-palette-add-category"
        />
      </div>

      <div class="palette-add-row">
        <label class="palette-add-label" :for="`${uid}-name`">{{ t('gui.designer.palette.add_dialog.name') }}</label>
        <div class="palette-add-field">
          <Input
            :id="`${uid}-name`"
            :model-value="name"
            :placeholder="t('gui.designer.palette.add_dialog.name_placeholder')"
            :invalid="showNameError"
            data-modal-autofocus
            data-test="designer-palette-add-name"
            @update:model-value="onNameInput"
            @keydown.enter.prevent="submit"
          />
          <p v-if="showNameError" class="palette-add-error" data-test="designer-palette-add-name-error">
            {{ t('gui.designer.palette.add_dialog.name_required') }}
          </p>
        </div>
      </div>

      <div class="palette-add-row palette-add-row--top">
        <span :id="`${uid}-icon`" class="palette-add-label">{{ t('gui.designer.palette.add_dialog.icon') }}</span>
        <div class="palette-icon-picker" role="radiogroup" :aria-labelledby="`${uid}-icon`" data-test="designer-palette-add-icons">
          <button
            v-for="key in PALETTE_ICON_CHOICES"
            :key="key"
            type="button"
            class="palette-icon-choice"
            :class="{ 'is-selected': icon === key }"
            role="radio"
            :aria-checked="icon === key"
            :aria-label="t(`gui.designer.palette.icons.${key}`)"
            :title="t(`gui.designer.palette.icons.${key}`)"
            :data-icon="key"
            @click="icon = key"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path :d="paletteIconPath(key)" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
      </div>

      <div class="palette-add-row">
        <label class="palette-add-label" :for="`${uid}-color`">{{ t('gui.designer.palette.add_dialog.color') }}</label>
        <Select :id="`${uid}-color`" v-model="color" :options="colorOptions" data-test="designer-palette-add-color" />
      </div>
    </form>

    <template #footer>
      <div class="palette-add-actions">
        <Button variant="secondary" size="sm" data-test="designer-palette-add-cancel" @click="emit('close')">
          {{ t('gui.designer.palette.add_dialog.cancel') }}
        </Button>
        <Button variant="primary" size="sm" data-test="designer-palette-add-submit" @click="submit">
          {{ t('gui.designer.palette.add_dialog.submit') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../components/ui/Button.vue'
import IconButton from '../../../components/ui/IconButton.vue'
import Input from '../../../components/ui/Input.vue'
import ModalShell from '../../../components/ui/ModalShell.vue'
import Select from '../../../components/ui/Select.vue'
import {
  DEFAULT_PALETTE_COLOR,
  PALETTE_CATEGORIES,
  PALETTE_COLOR_CHOICES,
  createCustomPaletteItem,
  type CustomPaletteItem,
  type PaletteCategoryKey,
  type PaletteItem,
} from './paletteCatalog'
import { DEFAULT_PALETTE_ICON, PALETTE_ICON_CHOICES, paletteIconPath } from './paletteIcons'

const props = defineProps<{
  open: boolean
  /** Items already in the Palette — the new id is chosen above theirs. */
  existing: readonly PaletteItem[]
  /** Category preselected when the dialog opens. */
  initialCategory?: PaletteCategoryKey
}>()

const emit = defineEmits<{
  close: []
  add: [item: CustomPaletteItem]
}>()

const { t } = useI18n()

// Field ids for label/for pairs; unique per instance so two Palettes never share them.
const uid = `palette-add-${Math.random().toString(36).slice(2, 10)}`

const category = ref<string>(PALETTE_CATEGORIES[0])
const name = ref('')
const icon = ref<string>(DEFAULT_PALETTE_ICON)
const color = ref<string>(DEFAULT_PALETTE_COLOR)
const showNameError = ref(false)

// Every open starts from a clean form (03-designer-shell resets name and icon on open).
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return
    category.value = props.initialCategory ?? PALETTE_CATEGORIES[0]
    name.value = ''
    icon.value = DEFAULT_PALETTE_ICON
    color.value = DEFAULT_PALETTE_COLOR
    showNameError.value = false
  },
  { immediate: true },
)

const categoryOptions = computed(() =>
  PALETTE_CATEGORIES.map((key) => ({ value: key, label: t(`gui.designer.palette.categories.${key}`) })),
)

const colorOptions = computed(() =>
  PALETTE_COLOR_CHOICES.map((c) => ({ value: c.value, label: t(`gui.designer.palette.colors.${c.key}`) })),
)

function onNameInput(value: string) {
  name.value = value
  if (showNameError.value && value.trim()) showNameError.value = false
}

function submit() {
  const result = createCustomPaletteItem(
    { category: category.value, name: name.value, icon: icon.value, color: color.value },
    props.existing,
  )
  if (!result.ok) {
    showNameError.value = result.error === 'name_required'
    return
  }
  emit('add', result.item)
}
</script>

<style scoped>
.palette-add-head {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 10px 0 16px;
  border-bottom: 1px solid var(--fg2-line-strong);
  background: linear-gradient(180deg, rgba(59, 130, 246, 0.12), transparent);
}

.palette-add-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: var(--fg2-text);
  font-size: 13px;
  font-weight: 800;
}

.palette-add-form {
  padding: 10px 0;
}

.palette-add-row {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  margin: 9px 0;
}

.palette-add-row--top {
  align-items: start;
}

.palette-add-label {
  color: #8ba1b8;
  font-size: 11px;
}

.palette-add-field {
  min-width: 0;
}

.palette-add-error {
  margin: 4px 0 0;
  color: var(--fg2-status-danger-fg);
  font-size: 11px;
}

.palette-icon-picker {
  display: grid;
  grid-template-columns: repeat(auto-fill, 34px);
  gap: 7px;
  max-height: 164px;
  padding: 2px;
  overflow: auto;
}

.palette-icon-choice {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 7px;
  background: #0a192a;
  color: #8fa9c3;
  cursor: pointer;
}

.palette-icon-choice svg {
  display: block;
  width: 17px;
  height: 17px;
}

.palette-icon-choice:hover {
  background: #112941;
  color: #dce9f5;
}

.palette-icon-choice:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.palette-icon-choice.is-selected {
  border-color: #3d8cff;
  background: #15345a;
  color: #9dccff;
  box-shadow: 0 0 0 1px #3d8cff55 inset;
}

.palette-add-actions {
  display: flex;
  justify-content: flex-end;
  gap: 7px;
  padding: 12px 16px 14px;
  border-top: 1px solid var(--fg2-line);
}
</style>
