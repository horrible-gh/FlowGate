<template>
  <div>
    <table v-if="providers.length" class="tbl">
      <thead>
        <tr>
          <th style="width:48px; text-align:center;">{{ t('settings.ai.col_default') }}</th>
          <th>{{ t('settings.ai.col_name') }}</th>
          <th style="width:90px;">{{ t('settings.ai.col_exec_type') }}</th>
          <th style="width:110px;">{{ t('settings.ai.col_kind') }}</th>
          <th>{{ t('settings.ai.col_connection') }}</th>
          <th style="width:90px;">{{ t('settings.ai.col_enabled') }}</th>
          <th v-if="!readonly" style="width:180px;">{{ t('settings.ai.col_action') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(p, i) in providers" :key="p.id || `row-${i}`">
          <td style="text-align:center;">
            <input
              type="radio"
              :checked="i === defaultIndex"
              :disabled="readonly"
              :aria-label="t('settings.ai.default_select_aria')"
              @change="emit('update:defaultIndex', i)"
            />
          </td>
          <td>
            {{ p.name }}
            <span v-if="i === defaultIndex" class="badge badge-blue" style="margin-left:6px;">
              {{ t('settings.ai.default_badge') }}
            </span>
          </td>
          <td><span class="badge">{{ execTypeLabel(p.exec_type) }}</span></td>
          <td>{{ kindLabel(p.kind) }}</td>
          <td>
            <span class="mono ai-conn">{{ connectionSummary(p) }}</span>
            <span v-if="p.exec_type === 'api'" class="text-xs text-s" style="display:block;">
              {{ keyStateLabel(p) }}
            </span>
            <span
              v-if="skipsPermissions(p)"
              class="ai-skip-flag"
              :title="t('settings.ai.skip_permissions_warn')"
            >
              {{ t('settings.ai.skip_permissions_badge') }}
            </span>
          </td>
          <td>
            <span class="badge" :class="p.enabled ? 'badge-green' : 'badge-gray'">
              {{ p.enabled ? t('settings.ai.enabled') : t('settings.ai.disabled_label') }}
            </span>
          </td>
          <td v-if="!readonly" style="white-space:nowrap;">
            <button class="btn btn-secondary btn-sm" :disabled="i === 0" :title="t('settings.ai.move_up')" @click="move(i, -1)">
              <AppIcon name="arrow-up" />
            </button>
            <button class="btn btn-secondary btn-sm" :disabled="i === providers.length - 1" :title="t('settings.ai.move_down')" @click="move(i, 1)">
              <AppIcon name="arrow-down" />
            </button>
            <button class="btn btn-secondary btn-sm" :title="t('common.edit')" @click="openEdit(i)">
              <AppIcon name="pencil-simple" />
            </button>
            <button class="btn btn-secondary btn-sm" :title="t('common.delete')" @click="remove(i)">
              <AppIcon name="trash" />
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="alert alert-info">
      <AppIcon name="info" /> {{ t('settings.ai.empty') }}
    </div>

    <div v-if="!readonly && !formOpen" style="margin-top:12px;">
      <button class="btn btn-secondary" @click="openAdd">
        <AppIcon name="plus" /> {{ t('settings.ai.add_provider') }}
      </button>
    </div>

    <div v-if="formOpen" class="card" style="margin-top:12px;">
      <div class="card-hd">
        <span class="card-title">
          {{ editIndex === null ? t('settings.ai.add_provider') : t('settings.ai.edit_provider') }}
        </span>
      </div>
      <div class="card-bd pad">
        <div class="form-section">
          <div class="form-group">
            <label class="form-label">{{ t('settings.ai.label_name') }}</label>
            <input class="form-ctrl" v-model="form.name" :placeholder="t('settings.ai.placeholder_name')" style="max-width:420px;" />
          </div>
          <div class="form-group">
            <label class="form-label">{{ t('settings.ai.label_exec_type') }}</label>
            <select class="form-ctrl" v-model="form.exec_type" style="max-width:200px;">
              <option v-for="e in catalog.exec_types" :key="e" :value="e">{{ execTypeLabel(e) }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">{{ t('settings.ai.label_kind') }}</label>
            <select class="form-ctrl" v-model="form.kind" style="max-width:200px;">
              <option v-for="k in kindOptions" :key="k" :value="k">{{ kindLabel(k) }}</option>
            </select>
          </div>

          <div v-if="form.exec_type === 'cli'" class="form-group">
            <label class="form-label">{{ t('settings.ai.label_cli_command') }}</label>
            <input class="form-ctrl mono" v-model="form.cli_command" :placeholder="t('settings.ai.placeholder_cli_command')" style="max-width:520px;" />
          </div>
          <template v-else>
            <div class="form-group">
              <label class="form-label">{{ t('settings.ai.label_api_model') }}</label>
              <input class="form-ctrl mono" v-model="form.api_model" :placeholder="t('settings.ai.placeholder_api_model')" style="max-width:420px;" />
            </div>
            <div class="form-group">
              <label class="form-label">{{ t('settings.ai.label_api_base_url') }}</label>
              <input class="form-ctrl mono" v-model="form.api_base_url" :placeholder="t('settings.ai.placeholder_api_base_url')" style="max-width:520px;" />
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
                style="max-width:420px;"
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
          <div class="flex" style="gap:8px;">
            <button class="btn btn-primary" @click="confirmForm">{{ t('common.confirm') }}</button>
            <button class="btn btn-secondary" @click="closeForm">{{ t('common.cancel') }}</button>
          </div>
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
import { computed, reactive, ref, watch } from 'vue';
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

const kindOptions = computed(() => props.catalog.kinds?.[form.exec_type] || []);
const permissionRule = computed(() => permissionSkipRule(props.catalog, form.kind));
const editingRow = computed(() => (editIndex.value === null ? null : props.providers[editIndex.value]));
const editingHasKey = computed(() => !!editingRow.value?.api_key_set && editingRow.value?.api_key !== '');
const editingKeyHint = computed(() => editingRow.value?.api_key_hint || '');

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

function kindLabel(kind) {
  const key = `settings.ai.kind.${kind}`;
  return te(key) ? t(key) : kind;
}

function connectionSummary(p) {
  if (p.exec_type === 'cli') return p.cli_command || '—';
  const model = p.api_model || '—';
  return p.api_base_url ? `${model} @ ${p.api_base_url}` : model;
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

function remove(index) {
  const next = props.providers.filter((_, i) => i !== index);
  commit(next);
  if (props.defaultIndex === index) emit('update:defaultIndex', next.length ? 0 : -1);
  else if (props.defaultIndex > index) emit('update:defaultIndex', props.defaultIndex - 1);
  if (editIndex.value === index) closeForm();
}

function openAdd() {
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
}

function openEdit(index) {
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
}

function closeForm() {
  formOpen.value = false;
  editIndex.value = null;
  formError.value = '';
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
.ai-conn {
  font-size: .8rem;
  color: var(--text-m);
  word-break: break-all;
}
.ai-skip-warn {
  color: var(--danger, #d64545);
}
.ai-skip-flag {
  display: block;
  margin-top: 2px;
  font-size: .75rem;
  color: var(--danger, #d64545);
}
</style>
