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

    <!-- Add/edit dialog: never dismissed by a backdrop click (T4 §3) -->
    <div v-if="formOpen" ref="formOverlay" class="modal-bg" tabindex="-1" @keydown.esc.stop="closeForm">
      <div class="modal-box modal-lg" role="dialog" aria-modal="true" :aria-label="editIndex === null ? t('settings.ai.add_provider') : t('settings.ai.edit_provider')">
        <div class="modal-hd">
          <span class="modal-title">
            {{ editIndex === null ? t('settings.ai.add_provider') : t('settings.ai.edit_provider') }}
          </span>
          <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeForm"><AppIcon name="x" aria-hidden="true" /></button>
        </div>
        <!-- v3 deck ②③: the body is a flat list of .form-group (no .form-section wrapper,
             which would add its own 24px bottom margin), 실행 방식 / 종류 share one .form-row,
             and every control fills the dialog width instead of carrying its own max-width. -->
        <div class="modal-bd">
          <div class="form-group">
            <label class="form-label">{{ t('settings.ai.label_name') }}</label>
            <input ref="formInitialFocus" class="form-ctrl" v-model="form.name" :placeholder="t('settings.ai.placeholder_name')" />
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
            <input class="form-ctrl mono" v-model="form.cli_command" :placeholder="t('settings.ai.placeholder_cli_command')" />
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

          <div v-if="form.exec_type === 'cli' && permissionRule" class="form-group">
            <label class="form-label">
              <input
                type="checkbox"
                v-model="form.skip_permissions"
                @change="onPermissionSkipToggle"
              />
              {{ t('settings.ai.label_skip_permissions') }}
            </label>
            <p class="form-hint">{{ t('settings.ai.skip_permissions_hint') }}</p>
            <p v-if="form.skip_permissions" class="form-hint ai-skip-warn">
              {{ t('settings.ai.skip_permissions_warn') }}
            </p>
          </div>

          <div class="form-group">
            <label class="form-label">
              <input type="checkbox" v-model="form.enabled" /> {{ t('settings.ai.label_enabled') }}
            </label>
          </div>

          <p v-if="formError" class="text-sm" style="color:var(--danger, #d64545);">{{ formError }}</p>
        </div>
        <div class="modal-ft">
          <button class="btn btn-secondary" @click="closeForm">{{ t('common.cancel') }}</button>
          <button class="btn btn-primary" @click="confirmForm">
            <AppIcon name="floppy-disk" /> {{ t('common.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Command/info view dialog: informational only, closes on backdrop click -->
    <div v-if="cmdRow" ref="cmdOverlay" class="modal-bg" tabindex="-1" @click.self="closeCmd" @keydown.esc.stop="closeCmd">
      <div class="modal-box" role="dialog" aria-modal="true" :aria-label="commandTitle">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="terminal" style="margin-right:6px; color:var(--primary);" /> {{ commandTitle }}
          </span>
          <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeCmd"><AppIcon name="x" aria-hidden="true" /></button>
        </div>
        <div class="modal-bd">
          <template v-if="cmdRow.exec_type === 'cli'">
            <div class="form-group">
              <label class="form-label">{{ t('settings.ai.label_cli_command') }}</label>
              <div class="code-block mono">{{ cmdRow.cli_command || '—' }}</div>
            </div>
            <p v-if="skipsPermissions(cmdRow)" class="form-hint ai-skip-warn">
              {{ t('settings.ai.skip_permissions_badge') }} — {{ t('settings.ai.skip_permissions_cmd_warn') }}
            </p>
          </template>
          <template v-else>
            <div class="form-group">
              <label class="form-label">{{ t('settings.ai.label_api_model') }}</label>
              <div class="code-block mono">{{ cmdRow.api_model || '—' }}</div>
            </div>
            <div v-if="cmdRow.api_base_url" class="form-group">
              <label class="form-label">{{ t('settings.ai.label_api_base_url') }}</label>
              <div class="code-block mono">{{ cmdRow.api_base_url }}</div>
            </div>
            <div class="form-group">
              <label class="form-label">{{ t('settings.ai.label_api_key') }}</label>
              <p class="form-hint" style="margin:0;">{{ keyStateLabel(cmdRow) }}</p>
            </div>
          </template>
        </div>
        <div class="modal-ft">
          <button class="btn btn-secondary" @click="closeCmd">{{ t('common.close') }}</button>
        </div>
      </div>
    </div>

    <!-- Delete confirmation dialog: never dismissed by a backdrop click -->
    <div v-if="deleteRow" ref="deleteOverlay" class="modal-bg" tabindex="-1" @keydown.esc.stop="closeDelete">
      <div class="modal-box" role="dialog" aria-modal="true" :aria-label="t('settings.ai.delete_provider_title')" style="width:440px;">
        <div class="modal-hd">
          <span class="modal-title">{{ t('settings.ai.delete_provider_title') }}</span>
          <button class="modal-close" type="button" :aria-label="t('common.close')" @click="closeDelete"><AppIcon name="x" aria-hidden="true" /></button>
        </div>
        <div class="modal-bd">
          <p style="margin:0 0 8px;">{{ t('settings.ai.delete_provider_body', { name: deleteRow.name }) }}</p>
          <p class="form-hint" style="margin:0;">{{ t('settings.ai.delete_provider_hint') }}</p>
        </div>
        <div class="modal-ft">
          <button class="btn btn-secondary" @click="closeDelete">{{ t('common.cancel') }}</button>
          <button class="btn btn-danger" @click="confirmDelete">
            <AppIcon name="trash" /> {{ t('common.delete') }}
          </button>
        </div>
      </div>
    </div>
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
import { computed, nextTick, reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import AppIcon from '@shared/AppIcon.vue';
import { hasPermissionSkip, permissionSkipRule, setPermissionSkip } from './aiPermissionSkip';
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
  // 0371 NR0007 §5: mirrors what the command string says, never a stored field of its own.
  skip_permissions: false,
});

const draggedIndex = ref(null);
const dragOverIndex = ref(null);
const deleteIndex = ref(null);
const cmdIndex = ref(null);
const formInitialFocus = ref(null);
// Escape is handled on the overlay, so the overlay has to be able to hold focus: without a
// focusable overlay a real key event fires on <body> and never reaches this handler (the
// info/confirm dialogs put focus nowhere, and clicking the backdrop of the form dialog blurs
// its input). tabindex="-1" + focus() is the same pattern TimeMachineDialog.vue uses.
const formOverlay = ref(null);
const cmdOverlay = ref(null);
const deleteOverlay = ref(null);
let lastTrigger = null;

function rememberTrigger(event) {
  lastTrigger = event?.currentTarget || null;
}

function restoreTriggerFocus() {
  nextTick(() => lastTrigger?.focus?.());
}

const kindOptions = computed(() => props.catalog.kinds?.[form.exec_type] || []);
const permissionRule = computed(() => permissionSkipRule(props.catalog, form.kind));
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
});

// The command string is the truth: typing the flag by hand ticks the box, and switching to
// a kind whose flag is spelled differently re-reads it. The checkbox handler below edits the
// command, this puts the box back in step with the result.
watch(() => [form.kind, form.cli_command], () => {
  form.skip_permissions = hasPermissionSkip(props.catalog, form.kind, form.cli_command);
});

function onPermissionSkipToggle() {
  form.cli_command = setPermissionSkip(
    props.catalog, form.kind, form.cli_command, form.skip_permissions,
  );
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
  nextTick(() => deleteOverlay.value?.focus?.());
}

function closeDelete() {
  deleteIndex.value = null;
  restoreTriggerFocus();
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
  nextTick(() => cmdOverlay.value?.focus?.());
}

function closeCmd() {
  cmdIndex.value = null;
  restoreTriggerFocus();
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
  // A new provider always starts with permission confirmation ON (0371 NR0007 §5).
  form.skip_permissions = false;
  formError.value = '';
  formOpen.value = true;
  nextTick(() => formInitialFocus.value?.focus?.());
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
  // Read from the stored command, not assumed: an existing row is never rewritten, so the
  // box has to show what that row actually does.
  form.skip_permissions = hasPermissionSkip(props.catalog, form.kind, form.cli_command);
  formError.value = '';
  formOpen.value = true;
  nextTick(() => formInitialFocus.value?.focus?.());
}

function closeForm() {
  formOpen.value = false;
  editIndex.value = null;
  formError.value = '';
  restoreTriggerFocus();
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
.ai-skip-warn {
  color: var(--danger, #d64545);
}

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
