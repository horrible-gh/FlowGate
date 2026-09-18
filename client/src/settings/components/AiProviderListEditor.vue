<template>
  <div>
    <div v-if="providers.length" class="ai-list">
      <div
        v-for="(p, i) in providers"
        :key="p.id || `row-${i}`"
        class="ai-row"
        :class="{ 'is-dragging': draggedIndex === i, 'drag-over': dragOverIndex === i && draggedIndex !== i }"
        :draggable="!readonly"
        @dragstart="onDragStart(i)"
        @dragover.prevent="onDragOver(i)"
        @drop.prevent="onDrop(i)"
        @dragend="onDragEnd"
      >
        <AppIcon v-if="!readonly" name="dots-six-vertical" class="ai-drag-handle" />
        <span class="ai-rank">{{ i + 1 }}</span>
        <input
          type="radio"
          :checked="i === defaultIndex"
          :disabled="readonly"
          :aria-label="t('settings.ai.default_select_aria')"
          @change="emit('update:defaultIndex', i)"
        />
        <span class="ai-name">{{ p.name }}</span>
        <span class="ai-kind">{{ kindLabel(p.kind) }}</span>
        <span class="ai-icons">
          <i
            v-if="i === defaultIndex"
            class="ai-badge-icon ai-badge-default"
            role="img"
            :aria-label="t('settings.ai.default_badge')"
            :data-tip="t('settings.ai.default_badge')"
            :title="t('settings.ai.default_badge')"
          >
            <svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true">
              <path d="M128,24 L157,100 L238,104 L174,152 L196,230 L128,184 L60,230 L82,152 L18,104 L99,100 Z" />
            </svg>
          </i>
          <i
            class="ai-badge-icon"
            :class="p.exec_type === 'cli' ? 'ai-badge-mode-cli' : 'ai-badge-mode-api'"
            role="img"
            :aria-label="execModeLabel(p)"
            :data-tip="execModeLabel(p)"
            :title="execModeLabel(p)"
          >
            <svg v-if="p.exec_type === 'cli'" viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="20" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M72,88 L120,128 L72,168" />
              <line x1="140" y1="168" x2="192" y2="168" />
            </svg>
            <svg v-else viewBox="0 0 256 256" fill="currentColor" aria-hidden="true">
              <circle cx="96" cy="144" r="34" />
              <circle cx="148" cy="120" r="44" />
              <circle cx="192" cy="146" r="30" />
              <rect x="86" y="140" width="122" height="40" rx="20" />
            </svg>
          </i>
          <i
            class="ai-badge-icon"
            :class="p.enabled ? 'ai-badge-active' : 'ai-badge-inactive'"
            role="img"
            :aria-label="p.enabled ? t('settings.ai.enabled') : t('settings.ai.disabled_label')"
            :data-tip="p.enabled ? t('settings.ai.enabled') : t('settings.ai.disabled_label')"
            :title="p.enabled ? t('settings.ai.enabled') : t('settings.ai.disabled_label')"
          >
            <svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="20" stroke-linecap="round" aria-hidden="true">
              <line x1="128" y1="40" x2="128" y2="120" />
              <path d="M76,72 a76,76 0 1 0 104,0" fill="none" />
            </svg>
          </i>
          <i
            v-if="skipsPermissions(p)"
            class="ai-badge-icon ai-badge-skip"
            role="img"
            :aria-label="t('settings.ai.skip_permissions_badge')"
            :data-tip="t('settings.ai.skip_permissions_badge')"
            :title="t('settings.ai.skip_permissions_badge')"
          >
            <svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true">
              <path d="M92,108 v-24 a36,36 0 0 1 72,0" fill="none" stroke="currentColor" stroke-width="18" stroke-linecap="round" />
              <rect x="70" y="108" width="116" height="88" rx="14" />
            </svg>
          </i>
        </span>
        <span class="ai-row-spacer"></span>
        <div v-if="!readonly" class="ai-row-btns">
          <button class="ai-row-btn" :disabled="i === 0" :title="t('settings.ai.move_up')" :aria-label="t('settings.ai.move_up')" @click="move(i, -1)">
            <AppIcon name="arrow-up" aria-hidden="true" />
          </button>
          <button class="ai-row-btn" :disabled="i === providers.length - 1" :title="t('settings.ai.move_down')" :aria-label="t('settings.ai.move_down')" @click="move(i, 1)">
            <AppIcon name="arrow-down" aria-hidden="true" />
          </button>
          <span class="ai-btn-div"></span>
          <button class="ai-row-btn" :title="t('settings.ai.view_command')" :aria-label="t('settings.ai.view_command')" @click="openCmd(i, $event)">
            <AppIcon name="terminal" aria-hidden="true" />
          </button>
          <button class="ai-row-btn" :title="t('common.edit')" :aria-label="t('common.edit')" @click="openEdit(i, $event)">
            <AppIcon name="pencil-simple" aria-hidden="true" />
          </button>
          <button class="ai-row-btn del" :title="t('common.delete')" :aria-label="t('common.delete')" @click="openDeleteConfirm(i, $event)">
            <AppIcon name="trash" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
    <div v-else class="alert alert-info">
      <AppIcon name="info" /> {{ t('settings.ai.empty') }}
    </div>

    <!-- v3 deck ①: on the system screen the add button lives in the card header
         (AiSettingsView.vue passes :show-add-button="false" and calls openAdd() through a
         template ref). The project screen's card header carries a mode badge instead, so it
         keeps this inline button. -->
    <div v-if="!readonly && showAddButton" style="margin-top:12px;">
      <button class="btn btn-secondary" @click="openAdd">
        <AppIcon name="plus" /> {{ t('settings.ai.add_provider') }}
      </button>
    </div>

    <!-- 0560 T0020 (4.5순위): the three dialogs this file used to carry inline are three
         components now — D0008 §4 "각 instance는 독립 dialog component로 분리하고, 부모 View는
         open state와 데이터 전달만 담당한다". What stays here is that half: the open flags
         (`formOpen` / `cmdRow` / `deleteRow`), the draft and the functions that turn it into a
         row of the list. `return-focus-to` is the trigger button the row was opened from — the
         common teardown puts focus back there, so the hand-rolled focus restore this file
         used to run after every close is gone (L0009 §2 "Open / Close lifecycle"). -->
    <AiProviderFormDialog
      :open="formOpen"
      :is-edit="editIndex !== null"
      :form="form"
      :catalog="catalog"
      :form-error="formError"
      :editing-has-key="editingHasKey"
      :editing-key-hint="editingKeyHint"
      :magic-tool-available="magicToolAvailable"
      :magic-filling="magicFilling"
      :magic-error="magicError"
      :show-model-placeholder-hint="showModelPlaceholderHint"
      :exec-type-label="execTypeLabel"
      :kind-label="kindLabel"
      :return-focus-to="lastTrigger"
      @close="closeForm"
      @submit="confirmForm"
      @fill-command="fillCliCommand"
    />

    <AiProviderCommandInfoDialog
      :open="cmdRow !== null"
      :row="cmdRow"
      :title="commandTitle"
      :key-state-label="keyStateLabel"
      :skips-permissions="skipsPermissions"
      :return-focus-to="lastTrigger"
      @close="closeCmd"
    />

    <AiProviderDeleteConfirmDialog
      :open="deleteRow !== null"
      :row="deleteRow"
      :return-focus-to="lastTrigger"
      @close="closeDelete"
      @confirm="confirmDelete"
    />
  </div>
</template>

<script setup>
// flowgate.default.0164 (D0002 §6): the ordered provider list ("routing chain") editor
// shared by the global screen and the project custom tab. Purely local state — the parent
// owns load/save; rows carry an `api_key` property ONLY when the user set ('' = delete,
// value = replace) so the parent can omit the field to mean "keep" (P0003 write-only key).
//
// 0469 T4 (v3 deck 8bqoacqs): table -> single-column row list + CRUD/command-view dialogs +
// drag & drop. Reorder (arrow buttons and drag) always resolves the default row's new index
// by object identity in `next`, not by recomputing from `id`, so a row added earlier in the
// same tick (still `id: null` until the parent's immediate-save round-trip returns) keeps its
// default status too.
import { computed, reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import AppIcon from '@shared/AppIcon.vue';
import AiProviderCommandInfoDialog from './AiProviderCommandInfoDialog.vue';
import AiProviderDeleteConfirmDialog from './AiProviderDeleteConfirmDialog.vue';
import AiProviderFormDialog from './AiProviderFormDialog.vue';
import { hasPermissionSkip } from './aiPermissionSkip';
import {
  NAME_MAX,
  CLI_COMMAND_MAX,
  API_BASE_URL_MAX,
  API_MODEL_MAX,
  API_KEY_MAX,
  PROVIDERS_MAX,
} from './aiProviderLimits';

const { t, te } = useI18n();

const props = defineProps({
  providers: { type: Array, default: () => [] },
  defaultIndex: { type: Number, default: -1 },
  catalog: {
    type: Object,
    default: () => ({ exec_types: ['cli', 'api'], kinds: { cli: [], api: [] } }),
  },
  readonly: { type: Boolean, default: false },
  // The system screen renders its own add button in the card header (v3 deck ①) and turns
  // this one off; the project screen keeps it.
  showAddButton: { type: Boolean, default: true },
  // 0519 T0009: the parent owns which endpoint the magic tool calls (system vs. this
  // project's own permission + project_id) — this component never guesses a URL. Given
  // { kind, model_name, skip_permissions } it resolves to { cli_command }. null when a
  // screen has not wired one (e.g. a bare unit mount), which hides the button.
  buildPresetCommand: { type: Function, default: null },
});
const emit = defineEmits(['update:providers', 'update:defaultIndex']);

const formOpen = ref(false);
const editIndex = ref(null);
const formError = ref('');
const form = reactive({
  name: '',
  exec_type: 'cli',
  kind: 'claude',
  enabled: true,
  cli_command: '',
  api_base_url: '',
  api_model: '',
  keyInput: '',
  keyClear: false,
});

const draggedIndex = ref(null);
const dragOverIndex = ref(null);
const deleteIndex = ref(null);
const cmdIndex = ref(null);
// 0560 T0020: a ref, not a plain `let`. The three dialogs read it as `return-focus-to`, so
// focus goes back to the row button that opened them from the one common teardown procedure
// (L0009 §2). The trio the overlays used to carry - a focusable overlay, a manual focus()
// and an element-bound ESC handler - left with them: the common stack judges ESC on a single
// document listener, so nothing has to hold focus for a key to be heard any more.
const lastTrigger = ref(null);

function rememberTrigger(event) {
  lastTrigger.value = event?.currentTarget || null;
}

// 0519 T0009: magic tool state. The model name is deliberately not asked for — the command
// is filled with the literal `model_name` and the user edits it in the command box like any
// other part of the string. The CLI flags stay on the server (build_preset_command), so this
// asks the parent-supplied builder for the string instead of assembling one here.
const MAGIC_MODEL_PLACEHOLDER = 'model_name';
const magicToolAvailable = computed(() => (
  !!props.buildPresetCommand && !!props.catalog.cli_presets?.[form.kind]
));
const magicFilling = ref(false);
const magicError = ref('');
// rej_01M1YY7MQRQZAZG1: purely derived from the command text on screen, not from whether the
// magic tool was clicked — a hand-typed placeholder shows the same reminder, and replacing it
// (by hand or with a fresh fill that no longer contains it) clears the reminder either way.
const showModelPlaceholderHint = computed(() => (
  form.exec_type === 'cli' && form.cli_command.includes(MAGIC_MODEL_PLACEHOLDER)
));
// Bumped whenever an in-flight fill stops belonging to what is on screen (kind switch,
// leaving CLI, dialog close) so a late response cannot overwrite the command the user is
// looking at.
const magicGeneration = ref(0);

function resetMagicTool() {
  magicGeneration.value += 1;
  magicFilling.value = false;
  magicError.value = '';
}

watch(() => form.kind, () => resetMagicTool());

const editingRow = computed(() => (editIndex.value === null ? null : props.providers[editIndex.value]));
const editingHasKey = computed(() => !!editingRow.value?.api_key_set && editingRow.value?.api_key !== '');
const editingKeyHint = computed(() => editingRow.value?.api_key_hint || '');
const deleteRow = computed(() => (deleteIndex.value === null ? null : props.providers[deleteIndex.value]));
const cmdRow = computed(() => (cmdIndex.value === null ? null : props.providers[cmdIndex.value]));
const commandTitle = computed(() => (
  cmdRow.value ? t('settings.ai.command_title', { name: cmdRow.value.name }) : ''
));

watch(() => form.exec_type, (execType) => {
  const kinds = props.catalog.kinds?.[execType] || [];
  if (!kinds.includes(form.kind)) form.kind = kinds[0] || '';
  // Leaving CLI hides the button, but a kind published on both sides (claude is a CLI kind
  // and an API kind) leaves form.kind untouched, so the kind watcher never fires. Reset here
  // too: a fill still in flight belongs to the CLI form the user just walked away from.
  resetMagicTool();
});

// 0519 T0009: the only place that writes a generated command into form.cli_command, and only
// when the user clicks the magic tool. Single-flight (the button is disabled while filling)
// plus the generation guard cover every way a request can go stale mid-flight.
function magicRequestIsCurrent(request) {
  return magicGeneration.value === request.generation
    && form.exec_type === request.execType
    && form.kind === request.kind;
}

async function fillCliCommand() {
  if (magicFilling.value || !props.buildPresetCommand) return;
  const request = {
    generation: magicGeneration.value,
    execType: form.exec_type,
    kind: form.kind,
  };
  magicError.value = '';
  magicFilling.value = true;
  try {
    // rej_01M1YTRN0SD379SG: the magic tool is for unmanned runs, so it always fills the
    // skip-permissions form (every flag present) — this is an internal parameter of the
    // request, not something a checkbox on screen decides or reflects back.
    const result = await props.buildPresetCommand({
      kind: request.kind,
      model_name: MAGIC_MODEL_PLACEHOLDER,
      skip_permissions: true,
    });
    if (!magicRequestIsCurrent(request)) return; // stale: kind/exec_type changed or dialog closed
    form.cli_command = result?.cli_command || '';
  } catch {
    if (!magicRequestIsCurrent(request)) return;
    magicError.value = t('settings.ai.magic_tool_error');
  } finally {
    if (magicGeneration.value === request.generation) magicFilling.value = false;
  }
}

function skipsPermissions(p) {
  return p.exec_type === 'cli' && hasPermissionSkip(props.catalog, p.kind, p.cli_command);
}

function execTypeLabel(execType) {
  const key = `settings.ai.exec_type.${execType}`;
  return te(key) ? t(key) : execType;
}

function execModeLabel(p) {
  return p.exec_type === 'cli' ? t('settings.ai.exec_mode_cli') : t('settings.ai.exec_mode_api');
}

function kindLabel(kind) {
  const key = `settings.ai.kind.${kind}`;
  return te(key) ? t(key) : kind;
}

function keyStateLabel(p) {
  if (p.api_key === '') return t('settings.ai.key_cleared');
  if (p.api_key) return t('settings.ai.key_set_hint', { hint: p.api_key.slice(-4) });
  // 0371: a key IS stored but the server cannot decrypt it (master key changed), so
  // there is no hint to show — saying "registered (…)" would look like an ordinary row.
  if (p.api_key_unreadable) return t('settings.ai.key_unreadable');
  if (p.api_key_set) return t('settings.ai.key_set_hint', { hint: p.api_key_hint || '' });
  return t('settings.ai.key_none');
}

function commit(next) {
  emit('update:providers', next);
}

function move(index, delta) {
  const next = [...props.providers];
  const target = index + delta;
  [next[index], next[target]] = [next[target], next[index]];
  commit(next);
  if (props.defaultIndex === index) emit('update:defaultIndex', target);
  else if (props.defaultIndex === target) emit('update:defaultIndex', index);
}

// Drag & drop reorder is remove-then-insert (not an index swap): dropping row A on row B
// removes A and inserts it right before B's current slot, matching the `.drag-over` top
// border indicator. The default row's new index is resolved by object identity so it keeps
// pointing at the same row no matter how many other rows moved around it.
function onDragStart(index) {
  if (props.readonly) return;
  draggedIndex.value = index;
}

function onDragOver(index) {
  if (props.readonly || draggedIndex.value === null) return;
  dragOverIndex.value = index;
}

function onDrop(index) {
  if (props.readonly) return;
  const from = draggedIndex.value;
  draggedIndex.value = null;
  dragOverIndex.value = null;
  if (from === null || from === index) return;

  const defaultRow = props.defaultIndex >= 0 ? props.providers[props.defaultIndex] : null;
  const next = [...props.providers];
  const [item] = next.splice(from, 1);
  const insertAt = index > from ? index - 1 : index;
  next.splice(insertAt, 0, item);
  commit(next);
  if (defaultRow) {
    const newIndex = next.indexOf(defaultRow);
    if (newIndex !== props.defaultIndex) emit('update:defaultIndex', newIndex);
  }
}

function onDragEnd() {
  draggedIndex.value = null;
  dragOverIndex.value = null;
}

function openDeleteConfirm(index, event) {
  rememberTrigger(event);
  deleteIndex.value = index;
}

function closeDelete() {
  deleteIndex.value = null;
}

function confirmDelete() {
  const index = deleteIndex.value;
  if (index === null) return;
  const next = props.providers.filter((_, i) => i !== index);
  commit(next);
  if (props.defaultIndex === index) emit('update:defaultIndex', next.length ? 0 : -1);
  else if (props.defaultIndex > index) emit('update:defaultIndex', props.defaultIndex - 1);
  if (editIndex.value === index) closeForm();
  closeDelete();
}

function openCmd(index, event) {
  rememberTrigger(event);
  cmdIndex.value = index;
}

function closeCmd() {
  cmdIndex.value = null;
}

function openAdd(event) {
  rememberTrigger(event);
  editIndex.value = null;
  form.name = '';
  form.exec_type = props.catalog.exec_types?.[0] || 'cli';
  form.kind = props.catalog.kinds?.[form.exec_type]?.[0] || '';
  form.enabled = true;
  form.cli_command = '';
  form.api_base_url = '';
  form.api_model = '';
  form.keyInput = '';
  form.keyClear = false;
  formError.value = '';
  resetMagicTool();
  formOpen.value = true;
}

defineExpose({ openAdd });

function openEdit(index, event) {
  rememberTrigger(event);
  const p = props.providers[index];
  editIndex.value = index;
  form.name = p.name || '';
  form.exec_type = p.exec_type || 'cli';
  form.kind = p.kind || '';
  form.enabled = !!p.enabled;
  form.cli_command = p.cli_command || '';
  form.api_base_url = p.api_base_url || '';
  form.api_model = p.api_model || '';
  form.keyInput = '';
  form.keyClear = false;
  formError.value = '';
  resetMagicTool();
  formOpen.value = true;
}

function closeForm() {
  formOpen.value = false;
  editIndex.value = null;
  formError.value = '';
  // Invalidates any fill still in flight so its response cannot land after the dialog (and
  // the row it was meant for) are gone.
  resetMagicTool();
}

function tooLong(fieldKey, value, max) {
  if (value.length <= max) return '';
  return t('settings.ai.err_too_long', {
    field: t(`settings.ai.field.${fieldKey}`),
    max,
    len: value.length,
  });
}

function confirmForm() {
  if (!form.name.trim()) {
    formError.value = t('settings.ai.err_name_required');
    return;
  }
  if (!form.kind) {
    formError.value = t('settings.ai.err_kind_required');
    return;
  }
  if (form.exec_type === 'cli' && !form.cli_command.trim()) {
    formError.value = t('settings.ai.err_cli_command_required');
    return;
  }
  if (form.exec_type === 'api' && !form.api_model.trim()) {
    formError.value = t('settings.ai.err_api_model_required');
    return;
  }

  // Length/duplicate/count are checked here rather than with a maxlength attribute: silently
  // truncating a pasted CLI command can leave a shorter command that still runs.
  const name = form.name.trim();
  const overLimit =
    tooLong('name', name, NAME_MAX) ||
    (form.exec_type === 'cli'
      ? tooLong('cli_command', form.cli_command.trim(), CLI_COMMAND_MAX)
      : tooLong('api_model', form.api_model.trim(), API_MODEL_MAX) ||
        tooLong('api_base_url', form.api_base_url.trim(), API_BASE_URL_MAX) ||
        tooLong('api_key', form.keyInput, API_KEY_MAX));
  if (overLimit) {
    formError.value = overLimit;
    return;
  }
  const duplicate = props.providers.some(
    (p, i) => i !== editIndex.value && (p.name || '').trim().toLowerCase() === name.toLowerCase(),
  );
  if (duplicate) {
    formError.value = t('settings.ai.err_duplicate_name');
    return;
  }
  if (editIndex.value === null && props.providers.length >= PROVIDERS_MAX) {
    formError.value = t('settings.ai.err_too_many', { max: PROVIDERS_MAX });
    return;
  }

  const base = editingRow.value;
  const row = {
    id: base?.id ?? null,
    name,
    exec_type: form.exec_type,
    kind: form.kind,
    enabled: form.enabled,
    cli_command: form.exec_type === 'cli' ? form.cli_command.trim() : null,
    api_base_url: form.exec_type === 'api' && form.api_base_url.trim() ? form.api_base_url.trim() : null,
    api_model: form.exec_type === 'api' ? form.api_model.trim() : null,
    api_key_set: base?.api_key_set ?? false,
    api_key_hint: base?.api_key_hint ?? null,
    api_key_unreadable: base?.api_key_unreadable ?? false,
  };
  // Opaque local identity the parent assigns to a row the server has not issued an id for yet.
  // Carry it across the edit so an in-flight save's response can still find this row.
  if (base?._localKey !== undefined) row._localKey = base._localKey;
  // Key intent: keep (no property), replace (value), delete ('').
  if (base && base.api_key !== undefined) row.api_key = base.api_key; // carry unsaved intent
  if (form.exec_type === 'api') {
    if (form.keyClear) row.api_key = '';
    else if (form.keyInput) row.api_key = form.keyInput;
  }

  const next = [...props.providers];
  if (editIndex.value === null) {
    next.push(row);
    commit(next);
    if (props.defaultIndex < 0) emit('update:defaultIndex', next.length - 1);
  } else {
    next[editIndex.value] = row;
    commit(next);
  }
  closeForm();
}
</script>

<style scoped>
/* 0560 T0020: the rules that styled the three dialog bodies (`.ai-skip-warn`,
   `.ai-model-hint`, the magic-tool row) moved into the three dialog components with the
   markup they style — a scoped rule here cannot reach a child's elements, and those
   surfaces are teleported out of this subtree besides. What stays is the row list. */

/* v3 deck 8bqoacqs: table -> single-column row list, ported from WorkflowDecisionModal.vue's
   sequence-row rules (extra.css keeps the same selectors for parity with the mockup). */
.ai-list { display: block; }
.ai-row {
  display: flex; align-items: center; gap: 8px; padding: 9px 11px;
  border: 1px solid var(--border); border-radius: var(--r); background: var(--surface);
  margin-bottom: 4px; transition: box-shadow var(--tr);
}
.ai-row:last-child { margin-bottom: 0; }
.ai-row:hover { box-shadow: var(--sh-sm); }
.ai-drag-handle { color: var(--text-m); font-size: .78rem; opacity: .3; cursor: grab; flex-shrink: 0; transition: opacity var(--tr); }
.ai-row:hover .ai-drag-handle { opacity: .65; }
.ai-row.is-dragging { opacity: .35; box-shadow: none; background: rgba(37,99,235,.08); border-color: #2563eb; }
.ai-row.drag-over { border-top: 2px solid var(--primary); margin-top: -1px; }
.ai-rank {
  width: 22px; height: 22px; border-radius: 50%; background: var(--bg);
  border: 1px solid var(--border); display: flex; align-items: center; justify-content: center;
  font-size: .68rem; font-weight: 700; color: var(--text-s); flex-shrink: 0;
}
.ai-name { font-size: .84rem; font-weight: 600; color: var(--text); }
.ai-kind { font-size: .78rem; color: var(--text-s); }
.ai-row-spacer { flex: 1; }
.ai-row-btns { display: flex; gap: 2px; flex-shrink: 0; align-items: center; }
.ai-btn-div { width: 1px; height: 16px; background: var(--border); margin: 0 4px; }
.ai-row-btn {
  width: 26px; height: 26px; border-radius: var(--r-sm); border: 1px solid var(--border);
  background: var(--surface); display: flex; align-items: center; justify-content: center;
  font-size: .7rem; color: var(--text-m); cursor: pointer; transition: all var(--tr);
}
.ai-row-btn:hover { background: var(--bg); color: var(--text); border-color: var(--border-d); }
.ai-row-btn.del:hover { background: var(--danger-l); color: var(--danger); border-color: #fca5a5; }
.ai-row-btn:disabled { opacity: .25; pointer-events: none; }

/* Status badges as icons rather than a row of text badges (rej_01M1TBYZTTTC9VFX). Each icon
   keeps a hover tooltip (data-tip/title) AND an always-on role=img + aria-label so a screen
   reader gets the same information a sighted user only sees on hover. */
.ai-icons { display: flex; align-items: center; gap: 5px; flex-shrink: 0; }
.ai-badge-icon {
  position: relative; width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: #fff; flex-shrink: 0; cursor: default;
}
.ai-badge-icon svg { width: 13px; height: 13px; }
.ai-badge-icon::after {
  content: attr(data-tip); position: absolute; bottom: calc(100% + 7px); left: 50%;
  transform: translateX(-50%); background: #0f172a; color: #fff; padding: 4px 9px;
  border-radius: 6px; font-size: .68rem; font-weight: 500; white-space: nowrap;
  opacity: 0; pointer-events: none; transition: opacity .12s; box-shadow: var(--sh-sm); z-index: 60;
}
.ai-badge-icon::before {
  content: ''; position: absolute; bottom: calc(100% + 2px); left: 50%; transform: translateX(-50%);
  border: 5px solid transparent; border-top-color: #0f172a; opacity: 0; transition: opacity .12s; z-index: 60;
}
.ai-badge-icon:hover::after, .ai-badge-icon:hover::before { opacity: 1; }
.ai-badge-default { background: #d97706; }
.ai-badge-mode-cli { background: #7c3aed; }
.ai-badge-mode-api { background: #2563eb; }
.ai-badge-active { background: var(--success, #16a34a); }
.ai-badge-inactive { background: var(--text-m, #94a3b8); }
.ai-badge-skip { background: var(--danger, #d64545); }
</style>
