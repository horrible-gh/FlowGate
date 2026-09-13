<template>
  <!--
    Provider add/edit dialog — flowgate.default.0560 T0020 (4.5순위).

    NR0005 §13 grouped `AiProviderListEditor.vue` under "내부 dialog instance를 분해" because
    one file held three of them. D0008 §4 decides how: each instance becomes its own dialog
    component and the parent keeps open state and data. This is instance 1 of 3, mapped to
    `form` by D0008 §6.

    `closeOnBackdrop` is NOT overridden. The old overlay had no `@click.self`, so a backdrop
    click never closed it, and `form`'s variant default is already `false` (dialogTypes.ts) —
    passing it again would only restate the default (T0020 §2.1).

    The old markup made the overlay focusable and bound an ESC handler to it, because a
    handler bound to an element only fires while that element holds focus. The common layer
    judges ESC on one document listener regardless of focus (L0009 §2), so the overlay's
    focus trick and its own key handler are gone rather than kept alongside it. The
    `data-dialog-autofocus` attribute below
    keeps what that manual focus was really for: the name field, not the surface — without it
    the initial-focus tree would start at the primary button (L0009 §4).
  -->
  <DialogShell
    :open="open"
    variant="form"
    size="lg"
    :return-focus-to="returnFocusTo"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader
        :title="isEdit ? t('settings.ai.edit_provider') : t('settings.ai.add_provider')"
        @close="emit('close')"
      />
    </template>

    <!-- v3 deck ②③: the body is a flat list of .form-group (no .form-section wrapper,
         which would add its own 24px bottom margin), 실행 방식 / 종류 share one .form-row,
         and every control fills the dialog width instead of carrying its own max-width. -->
    <template #default>
      <div class="form-group">
        <label class="form-label">{{ t('settings.ai.label_name') }}</label>
        <input class="form-ctrl" v-model="form.name" data-dialog-autofocus :placeholder="t('settings.ai.placeholder_name')" />
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">{{ t('settings.ai.label_exec_type') }}</label>
          <select class="form-ctrl" v-model="form.exec_type">
            <option v-for="e in catalog.exec_types" :key="e" :value="e">{{ execTypeLabel(e) }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">{{ t('settings.ai.label_kind') }}</label>
          <select class="form-ctrl" v-model="form.kind">
            <option v-for="k in kindOptions" :key="k" :value="k">{{ kindLabel(k) }}</option>
          </select>
        </div>
      </div>

      <div v-if="form.exec_type === 'cli'" class="form-group">
        <label class="form-label">{{ t('settings.ai.label_cli_command') }}</label>
        <!-- 0519 T0009/rej_01M1YTRN0SD379SG: the magic tool is one small button at the
             right edge of the command input. It always fills the unmanned-run form (every
             flag, skip permissions on) for the kind on screen, and leaves `model_name` in
             it for the user to replace by hand. There is no separate "skip permission
             confirmation" control to show or explain — FlowGate always runs a CLI
             unattended (nobody is there to answer that CLI's own prompt), so the flag is
             an internal detail of the generated command, not a user-facing choice. -->
        <div class="cli-cmd-row">
          <input class="form-ctrl mono" v-model="form.cli_command" :placeholder="t('settings.ai.placeholder_cli_command')" />
          <button
            v-if="magicToolAvailable"
            type="button"
            class="cli-magic-btn"
            :title="t('settings.ai.magic_tool')"
            :aria-label="t('settings.ai.magic_tool')"
            :disabled="magicFilling"
            @click="emit('fill-command')"
          >
            <AppIcon name="magic-wand" aria-hidden="true" />
          </button>
        </div>
        <!-- rej_01M1YY7MQRQZAZG1: the magic tool leaves the literal `model_name` in the
             command for the user to replace by hand (§ above) — this hint is the only
             place that tells them so. It reflects the command text itself, so it appears
             the moment a fill (or a hand edit) leaves the placeholder in, and clears the
             moment the placeholder is replaced with a real model name. -->
        <p v-if="showModelPlaceholderHint" class="form-hint ai-model-hint" role="status">{{ t('settings.ai.magic_model_hint') }}</p>
        <p v-if="magicError" class="text-sm ai-magic-error" role="alert">{{ magicError }}</p>
      </div>
      <template v-else>
        <div class="form-group">
          <label class="form-label">{{ t('settings.ai.label_api_model') }}</label>
          <input class="form-ctrl mono" v-model="form.api_model" :placeholder="t('settings.ai.placeholder_api_model')" />
        </div>
        <div class="form-group">
          <label class="form-label">{{ t('settings.ai.label_api_base_url') }}</label>
          <input class="form-ctrl mono" v-model="form.api_base_url" :placeholder="t('settings.ai.placeholder_api_base_url')" />
        </div>
        <div class="form-group">
          <label class="form-label">{{ t('settings.ai.label_api_key') }}</label>
          <input
            class="form-ctrl mono"
            type="password"
            v-model="form.keyInput"
            :placeholder="t('settings.ai.placeholder_api_key')"
            :disabled="form.keyClear"
            autocomplete="new-password"
          />
          <p v-if="editingHasKey" class="form-hint">
            {{ t('settings.ai.key_set_hint', { hint: editingKeyHint }) }} —
            {{ t('settings.ai.key_keep_hint') }}
            <label style="margin-left:8px;">
              <input type="checkbox" v-model="form.keyClear" /> {{ t('settings.ai.key_clear') }}
            </label>
            <span v-if="form.keyClear" class="text-s"> {{ t('settings.ai.key_cleared') }}</span>
          </p>
        </div>
      </template>

      <div class="form-group">
        <label class="form-label">
          <input type="checkbox" v-model="form.enabled" /> {{ t('settings.ai.label_enabled') }}
        </label>
      </div>

      <p v-if="formError" class="text-sm" style="color:var(--danger, #d64545);">{{ formError }}</p>
    </template>

    <template #footer>
      <DialogFooter :actions="actions">
        <template #action-save>
          <AppIcon name="floppy-disk" /> {{ t('common.save') }}
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import AppIcon from '@shared/AppIcon.vue';
import DialogFooter from '@main/components/dialogs/DialogFooter.vue';
import DialogHeader from '@main/components/dialogs/DialogHeader.vue';
import DialogShell from '@main/components/dialogs/DialogShell.vue';

// The draft row, its validation error and the magic-tool state stay with
// `AiProviderListEditor` — it is the component that turns a finished draft into a row of the
// provider list, and D0008 §1 keeps that feature work out of the common layer. This dialog
// renders that state and reports the two things the user can ask for: save, and fill.
const props = defineProps({
  open: { type: Boolean, default: false },
  isEdit: { type: Boolean, default: false },
  form: { type: Object, required: true },
  catalog: { type: Object, required: true },
  formError: { type: String, default: '' },
  editingHasKey: { type: Boolean, default: false },
  editingKeyHint: { type: String, default: '' },
  magicToolAvailable: { type: Boolean, default: false },
  magicFilling: { type: Boolean, default: false },
  magicError: { type: String, default: '' },
  showModelPlaceholderHint: { type: Boolean, default: false },
  execTypeLabel: { type: Function, required: true },
  kindLabel: { type: Function, required: true },
  returnFocusTo: { type: Object, default: null },
});

const emit = defineEmits(['close', 'submit', 'fill-command']);

const { t } = useI18n();

const kindOptions = computed(() => props.catalog.kinds?.[props.form.exec_type] || []);

/**
 * `[취소] [저장]` — the same two buttons as before, but their order is the layer's now
 * (`footerRolePriority` puts `cancel` immediately left of `primary`, D0008 §6).
 */
const actions = computed(() => [
  {
    id: 'cancel',
    label: t('common.cancel'),
    role: 'cancel',
    onSelect: () => emit('close'),
  },
  {
    id: 'save',
    label: t('common.save'),
    role: 'primary',
    onSelect: () => emit('submit'),
  },
]);
</script>

<style scoped>
/* Moved here with the markup these rules style (T0020): a scoped rule in
   `AiProviderListEditor.vue` cannot reach elements this component renders, and the surface is
   teleported out of that subtree besides. The overlay/box/header/footer rows are the common
   layer's; what is left is this form's own body. */

/* rej_01M1Z03ZN4DXP4N9: the model_name reminder rode on plain .form-hint (var(--text-m),
   the same faint gray used for ordinary captions), so it read as decorative filler instead
   of "you still have to act before saving". It shares the app's warning color/weight with
   .alert-warning and .wf-next-action (client/shared/app.css) — the other spots that flag an
   unfinished, must-fix-before-you-move-on state. */
.ai-model-hint {
  color: var(--warning);
  font-weight: 600;
}

/* 0519 T0009: the magic tool rides at the right edge of the command input, sized like the
   row buttons in the list so it reads as an affordance on the field rather than a section. */
.cli-cmd-row { display: flex; align-items: stretch; gap: 6px; }
.cli-cmd-row .form-ctrl { flex: 1 1 auto; min-width: 0; }
.cli-magic-btn {
  flex: 0 0 auto; width: 30px; border-radius: var(--r-sm); border: 1px solid var(--border);
  background: var(--surface); display: flex; align-items: center; justify-content: center;
  padding: 0; color: var(--text-m); cursor: pointer; transition: all var(--tr);
}
.cli-magic-btn:hover:not(:disabled) { background: var(--bg); color: var(--primary); border-color: var(--border-d); }
.cli-magic-btn:disabled { opacity: .45; cursor: default; }
.ai-magic-error { color: var(--danger, #d64545); margin: 6px 0 0; }
</style>
