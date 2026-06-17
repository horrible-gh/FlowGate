<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box csm-modal">
        <div class="modal-hd">
          <span class="modal-title">
            <i class="fa-solid fa-terminal" style="color:var(--primary);"></i>
            {{ t('main.command_selector_modal.title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- Result view -->
        <template v-if="execResult">
          <div class="modal-bd csm-result-body">
            <div class="csm-result-section">
              <div class="csm-result-label">{{ t('main.command_selector_modal.result_command') }}</div>
              <pre class="csm-result-code">{{ execResult.resolved }}</pre>
            </div>
            <div v-if="execResult.return_code !== null" class="csm-result-section">
              <span
                class="csm-exit-code"
                :class="execResult.return_code === 0 ? 'csm-exit-ok' : 'csm-exit-fail'"
              >
                {{ t('main.command_selector_modal.result_exit_code', { code: execResult.return_code }) }}
              </span>
            </div>
            <div v-if="execResult.stdout" class="csm-result-section">
              <div class="csm-result-label">{{ t('main.command_selector_modal.result_stdout') }}</div>
              <pre class="csm-result-pre">{{ execResult.stdout }}</pre>
            </div>
            <div v-if="execResult.stderr" class="csm-result-section">
              <div class="csm-result-label csm-label-err">{{ t('main.command_selector_modal.result_stderr') }}</div>
              <pre class="csm-result-pre csm-result-pre--err">{{ execResult.stderr }}</pre>
            </div>
          </div>
          <div class="modal-ft">
            <button type="button" class="btn btn-primary" @click="close">
              {{ t('common.close') }}
            </button>
          </div>
        </template>

        <!-- Selection / executing view -->
        <template v-else>
          <div class="modal-bd csm-body">
            <div v-if="executing" class="csm-state">
              <i class="fa-solid fa-circle-notch fa-spin"></i>
              {{ t('main.command_selector_modal.executing') }}
            </div>
            <div v-else-if="loading" class="csm-state">{{ t('common.loading') }}</div>
            <div v-else-if="fetchError" class="csm-state csm-state--error">{{ fetchError }}</div>
            <div v-else-if="commands.length === 0" class="csm-state">
              {{ t('main.command_selector_modal.empty') }}
            </div>
            <ul v-else class="csm-list">
              <li
                v-for="cmd in commands"
                :key="cmd.command_id"
                class="csm-item"
                :class="{ selected: selectedId === cmd.command_id }"
                @click="selectedId = cmd.command_id"
              >
                <div class="csm-cmd-name">{{ cmd.name }}</div>
                <div v-if="cmd.template" class="csm-cmd-tpl">{{ cmd.template }}</div>
              </li>
            </ul>
          </div>
          <div class="modal-ft">
            <button type="button" class="btn btn-secondary" @click="close">
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="!selectedId || executing"
              @click="executeCommand"
            >
              <i class="fa-solid fa-play"></i>
              {{ t('main.command_selector_modal.btn_execute') }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'

const props = defineProps<{
  visible: boolean
  envOverrides?: Record<string, string> | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const { t } = useI18n()
const { showToast } = useToast()

interface Command {
  command_id: string
  name: string
  template?: string
}

interface ExecResult {
  command_id: string
  resolved: string
  stdout: string
  stderr: string
  return_code: number | null
  executed_at: string
}

const commands = ref<Command[]>([])
const loading = ref(false)
const fetchError = ref('')
const selectedId = ref<string | null>(null)
const executing = ref(false)
const execResult = ref<ExecResult | null>(null)

watch(() => props.visible, async (val) => {
  if (val) {
    selectedId.value = null
    fetchError.value = ''
    execResult.value = null
    await fetchCommands()
  }
})

async function fetchCommands() {
  loading.value = true
  fetchError.value = ''
  try {
    const res = await getRequest<Command[]>('/api/v1/commands')
    commands.value = (res.data as any)?.commands ?? []
  } catch (e: any) {
    fetchError.value = e?.response?.data?.detail ?? t('main.command_selector_modal.load_error')
  } finally {
    loading.value = false
  }
}

async function executeCommand() {
  if (!selectedId.value || executing.value) return
  executing.value = true
  try {
    const body: Record<string, unknown> = {}
    if (props.envOverrides && Object.keys(props.envOverrides).length > 0) {
      body.env_overrides = props.envOverrides
    }
    const res = await postRequest<ExecResult>(
      `/api/v1/commands/${encodeURIComponent(selectedId.value)}/execute`,
      body,
    )
    const result = res.data as ExecResult
    execResult.value = result
    if (result.return_code !== 0) {
      showToast(t('main.command_selector_modal.execute_failed', { code: result.return_code }), 'danger')
    }
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 504) {
      showToast(t('main.command_selector_modal.execute_timeout'), 'danger')
      const resolved = e?.response?.data?.resolved ?? ''
      execResult.value = { command_id: selectedId.value ?? '', resolved, stdout: '', stderr: '', return_code: null, executed_at: '' }
    } else {
      const msg = e?.response?.data?.detail ?? t('main.command_selector_modal.execute_error')
      showToast(msg, 'danger')
    }
  } finally {
    executing.value = false
  }
}

function close() {
  emit('update:visible', false)
}
</script>

<style scoped>
.csm-modal {
  max-width: 560px;
}
.csm-body {
  padding: 0;
  max-height: 360px;
  overflow-y: auto;
}
.csm-state {
  padding: 40px 24px;
  text-align: center;
  color: var(--text-m);
  font-size: .875rem;
  line-height: 1.6;
}
.csm-state--error {
  color: var(--danger);
}
.csm-list {
  list-style: none;
  margin: 0;
  padding: 8px;
}
.csm-item {
  padding: 10px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: background .15s;
}
.csm-item:hover {
  background: var(--hover, rgba(0, 0, 0, .05));
}
.csm-item.selected {
  background: var(--primary-light, #dbeafe);
}
.csm-cmd-name {
  font-weight: 600;
  font-size: .875rem;
}
.csm-cmd-tpl {
  font-size: .75rem;
  color: var(--text-m);
  margin-top: 2px;
  font-family: monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.csm-result-body {
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 480px;
  overflow-y: auto;
}
.csm-result-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.csm-result-label {
  font-size: .75rem;
  font-weight: 600;
  color: var(--text-m);
  text-transform: uppercase;
  letter-spacing: .04em;
}
.csm-label-err {
  color: var(--danger);
}
.csm-result-code {
  background: var(--bg-2, #f4f4f5);
  border-radius: 6px;
  padding: 10px 12px;
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas,
    'SF Mono', Menlo, Monaco,
    'BIZ UDGothic', 'Yu Gothic UI', 'Meiryo UI',
    'Malgun Gothic', 'Apple SD Gothic Neo',
    'Source Code Pro', 'Liberation Mono', 'Courier New',
    'MS Gothic',
    monospace;
  font-size: .8125rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
.csm-exit-code {
  display: inline-block;
  font-size: .8125rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
}
.csm-exit-ok {
  background: var(--success-light, #dcfce7);
  color: var(--success, #16a34a);
}
.csm-exit-fail {
  background: var(--danger-light, #fee2e2);
  color: var(--danger, #dc2626);
}
.csm-result-pre {
  background: var(--bg-2, #f4f4f5);
  border-radius: 6px;
  padding: 10px 12px;
  font-family: 'Cascadia Code', 'JetBrains Mono', Consolas,
    'SF Mono', Menlo, Monaco,
    'BIZ UDGothic', 'Yu Gothic UI', 'Meiryo UI',
    'Malgun Gothic', 'Apple SD Gothic Neo',
    'Source Code Pro', 'Liberation Mono', 'Courier New',
    'MS Gothic',
    monospace;
  font-size: .8125rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  max-height: 200px;
  overflow-y: auto;
}
.csm-result-pre--err {
  background: var(--danger-light, #fee2e2);
  color: var(--danger, #dc2626);
}
</style>
