<template>
  <SettingsShell scope="system">
    <template #nav>
      <SettingsSubnav :items="systemNav" active="general" />
    </template>

    <section class="fgs-panel" data-panel="system-general">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="gear" size="lg" /></span>
        <div class="fgs-ptitle">
          <h2>{{ t('gui.settings.system_nav.general') }}</h2>
          <p>{{ t('gui.settings.page_sub.system_general') }}</p>
        </div>
      </div>

      <div class="fgs-panel-body">
        <!-- Storage Root: GET /system/storage-root, read-only -->
        <div class="fgs-sec" data-card="storage-root">
          <div class="fgs-sec-head">
            <h3>{{ t('gui.settings.general.root.title') }}</h3>
            <div class="meta">{{ t('gui.settings.general.root.readonly_hint') }}</div>
          </div>
          <div class="fgs-rows">
            <div v-if="rootLoading" class="fgs-row"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div>
            <div v-else-if="rootError" class="fgs-row">
              <ErrorState compact :title="t('gui.settings.general.errors.root_failed')" :message="rootError" data-testid="root-error">
                <template #action>
                  <Button variant="secondary" @click="loadRoot">{{ t('gui.common.retry') }}</Button>
                </template>
              </ErrorState>
            </div>
            <template v-else-if="root">
              <div class="fgs-row">
                <div class="fgs-row-label">
                  <strong>{{ t('gui.settings.general.root.effective') }}</strong>
                  <small>{{ t('gui.settings.general.root.source') }} · <span data-testid="root-source">{{ sourceLabel(root.effective_source) }}</span></small>
                </div>
                <div class="fgs-row-ctrl fgs-mono" data-testid="root-effective">{{ root.effective_value }}</div>
              </div>
              <div class="fgs-row">
                <div class="fgs-row-label"><strong>{{ t('gui.settings.general.root.configured') }}</strong></div>
                <div class="fgs-row-ctrl fgs-mono" data-testid="root-configured">{{ root.configured_value ?? t('gui.settings.general.root.unset') }}</div>
              </div>
              <div v-if="root.env_override" class="fgs-row sub">
                <span class="fgs-hint" data-testid="root-env-override"><SettingsIcon name="info" size="sm" />{{ t('gui.settings.general.root.env_override') }}</span>
              </div>
            </template>
          </div>
        </div>

        <div v-if="settingsLoading" class="fgs-rows"><div class="fgs-row"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div></div>
        <div v-else-if="settingsError" class="fgs-rows">
          <div class="fgs-row">
            <ErrorState compact :title="t('gui.settings.general.errors.settings_failed')" :message="settingsError" data-testid="settings-error">
              <template #action>
                <Button variant="secondary" @click="loadSettings">{{ t('gui.common.retry') }}</Button>
              </template>
            </ErrorState>
          </div>
        </div>
        <template v-else>
          <!-- Scratch retention period: scratch_ttl_value + scratch_ttl_unit, both in one PATCH -->
          <div class="fgs-sec" data-card="scratch">
            <div class="fgs-sec-head"><h3>{{ t('gui.settings.general.scratch.title') }}</h3></div>
            <div class="fgs-rows">
              <div class="fgs-row">
                <div class="fgs-row-label">
                  <label for="sys-scratch-value">{{ t('gui.settings.general.scratch.label') }}</label>
                  <small>{{ t('gui.settings.general.scratch.hint') }}</small>
                </div>
                <div class="fgs-row-ctrl">
                  <span v-if="scratchSaved && !scratchDirty && !scratchError" class="fgs-muted" data-testid="scratch-saved">{{ t('gui.settings.saved') }}</span>
                  <input
                    id="sys-scratch-value"
                    v-model="scratchValue"
                    class="fgs-input num"
                    :class="{ dirty: scratchDirty }"
                    inputmode="numeric"
                    autocomplete="off"
                    :disabled="scratchSaving"
                    data-testid="scratch-value"
                  />
                  <select v-model="scratchUnit" class="fgs-input unit" :aria-label="t('gui.settings.general.scratch.unit')" :disabled="scratchSaving" data-testid="scratch-unit">
                    <option v-for="unit in SCRATCH_UNITS" :key="unit" :value="unit">{{ t(`gui.settings.general.scratch.units.${unit}`) }}</option>
                  </select>
                  <button
                    type="button"
                    class="fgs-btn pri"
                    :disabled="!scratchCanSave"
                    data-testid="scratch-save"
                    @click="saveScratch"
                  >{{ scratchSaving ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
                </div>
              </div>
              <div v-if="scratchDirty && scratchProblem" class="fgs-row sub">
                <span class="fgs-error" data-testid="scratch-invalid">{{ scratchProblem }}</span>
              </div>
              <div v-if="scratchError" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="scratch-error">{{ scratchError }}</span>
              </div>
            </div>
          </div>

          <!-- AI iteration limit: ai_repeat_count_max only -->
          <div class="fgs-sec" data-card="repeat">
            <div class="fgs-sec-head"><h3>{{ t('gui.settings.general.repeat.title') }}</h3></div>
            <div class="fgs-rows">
              <div class="fgs-row">
                <div class="fgs-row-label">
                  <label for="sys-repeat-max">{{ t('gui.settings.general.repeat.label') }}</label>
                  <small>{{ t('gui.settings.general.repeat.hint') }}</small>
                </div>
                <div class="fgs-row-ctrl">
                  <span v-if="repeatSaved && !repeatDirty && !repeatError" class="fgs-muted" data-testid="repeat-saved">{{ t('gui.settings.saved') }}</span>
                  <input
                    id="sys-repeat-max"
                    v-model="repeatValue"
                    class="fgs-input num"
                    :class="{ dirty: repeatDirty }"
                    inputmode="numeric"
                    autocomplete="off"
                    :disabled="repeatSaving"
                    data-testid="repeat-value"
                  />
                  <button
                    type="button"
                    class="fgs-btn pri"
                    :disabled="!repeatCanSave"
                    data-testid="repeat-save"
                    @click="askRepeat"
                  >{{ repeatSaving ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
                </div>
              </div>
              <div v-if="repeatDirty && repeatProblem" class="fgs-row sub">
                <span class="fgs-error" data-testid="repeat-invalid">{{ repeatProblem }}</span>
              </div>
              <div v-if="repeatLowering" class="fgs-row sub">
                <span class="fgs-hint" data-testid="repeat-lower-hint"><SettingsIcon name="alert" size="sm" />{{ t('gui.settings.general.repeat.lower_impact') }}</span>
              </div>
              <div v-if="repeatError" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="repeat-error">{{ repeatError }}</span>
              </div>
            </div>
          </div>

          <!-- 검증: verify_max_runs, verify_run_timeout_sec — saved separately (flowgate.v02test.0053 0003-T) -->
          <div class="fgs-sec" data-card="verify">
            <div class="fgs-sec-head">
              <h3>{{ t('gui.settings.general.verify.title') }}</h3>
              <div class="meta">{{ t('gui.settings.general.verify.card_hint') }}</div>
            </div>
            <div class="fgs-rows">
              <div v-for="item in verifyEditors" :key="item.name" class="fgs-row-group">
                <div class="fgs-row">
                  <div class="fgs-row-label">
                    <label :for="`sys-verify-${item.name}`">{{ t(`gui.settings.general.verify.${item.name}.label`) }}</label>
                    <small>{{ t(`gui.settings.general.verify.${item.name}.hint`) }}</small>
                  </div>
                  <div class="fgs-row-ctrl">
                    <span v-if="item.editor.saved.value && !item.editor.dirty.value && !item.editor.error.value" class="fgs-muted" :data-testid="`verify-${item.name}-saved`">{{ t('gui.settings.saved') }}</span>
                    <input
                      :id="`sys-verify-${item.name}`"
                      v-model="item.editor.value.value"
                      class="fgs-input num"
                      :class="{ dirty: item.editor.dirty.value }"
                      inputmode="numeric"
                      autocomplete="off"
                      :disabled="item.editor.saving.value"
                      :data-testid="`verify-${item.name}-value`"
                    />
                    <span class="fgs-muted">{{ t(`gui.settings.general.verify.${item.name}.unit`) }}</span>
                    <button
                      type="button"
                      class="fgs-btn pri"
                      :disabled="!item.editor.canSave.value"
                      :data-testid="`verify-${item.name}-save`"
                      @click="item.editor.save()"
                    >{{ item.editor.saving.value ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
                  </div>
                </div>
                <div v-if="item.editor.dirty.value && item.editor.problem.value" class="fgs-row sub">
                  <span class="fgs-error" :data-testid="`verify-${item.name}-invalid`">{{ item.editor.problem.value }}</span>
                </div>
                <div v-if="item.editor.error.value" class="fgs-row sub">
                  <span class="fgs-error" role="alert" :data-testid="`verify-${item.name}-error`">{{ item.editor.error.value }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- flowgate.chat.0017 CH0002: System default limits for AI attachment approval requests (inherited unless the project overrides them). -->
        <AttachmentRequestSettingsCard scope="system" />
      </div>
    </section>

    <!-- Confirm lowering the iteration limit: cancel sends no requests -->
    <ModalShell :open="lowerConfirmOpen" size="sm" :aria-label="t('gui.settings.general.repeat.lower_title')" @close="lowerConfirmOpen = false">
      <div class="confirm" data-testid="repeat-lower-confirm">
        <div class="conf-ico warn"><SettingsIcon name="alert" size="xl" /></div>
        <div class="conf-who">{{ t('gui.settings.general.repeat.lower_title_values', { from: repeatConfirmed, to: repeatValue.trim() }) }}</div>
        <p class="conf-text">{{ t('gui.settings.general.repeat.lower_impact') }}</p>
        <p class="conf-text fgs-muted">{{ t('gui.settings.general.repeat.lower_no_rewrite') }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" data-testid="repeat-lower-cancel" @click="lowerConfirmOpen = false">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="repeatSaving" data-testid="repeat-lower-ok" @click="confirmLower">{{ t('gui.settings.general.save') }}</button>
        </div>
      </template>
    </ModalShell>

    <!-- Unsaved changes guard: continuing to edit cancels navigation and sends no requests -->
    <ModalShell :open="leaveOpen" size="sm" :aria-label="t('gui.settings.general.leave.title')" @close="answerLeave(false)">
      <div class="confirm" data-testid="leave-confirm">
        <div class="conf-ico warn"><SettingsIcon name="alert" size="xl" /></div>
        <div class="conf-who">{{ t('gui.settings.general.leave.title') }}</div>
        <p class="conf-text">{{ t('gui.settings.general.leave.unsaved') }}</p>
        <p v-if="scratchSaving || repeatSaving || verifySaving" class="conf-text fgs-muted" data-testid="leave-saving">{{ t('gui.settings.general.leave.saving') }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" data-testid="leave-stay" @click="answerLeave(false)">{{ t('gui.settings.general.leave.stay') }}</button>
          <button type="button" class="fgs-btn danger" data-testid="leave-discard" @click="answerLeave(true)">{{ t('gui.settings.general.leave.discard') }}</button>
        </div>
      </template>
    </ModalShell>
  </SettingsShell>
</template>

<script setup lang="ts">
// 시스템 › 일반 (flowgate.v02.0076 T#3, D §2.5). Only the existing contracts:
//
// - Storage Root: GET /system/storage-root — the stored value, the effective Root, where it comes from
//   and whether FLOWGATE_STORAGE_DIR overrides it. Read only: no input, no save, no move, and
//   storage_root never goes into a PATCH. /system/settings/storage-root (a different contract with
//   other field names) is not used, not even as a fallback.
// - Scratch retention period and AI iteration limit come from the rows of GET /system/settings and are two
//   separate saves on PATCH /system/settings:
//   · Scratch: scratch_ttl_value and scratch_ttl_unit together, as strings, in ONE request — the
//     server validates the pair and writes it in one store transaction (V13 checked the deployment
//     store rolls both keys back). Never split into two PATCHes, never converted to minutes.
//   · iteration limit: ai_repeat_count_max alone, 1~30. Lowering it first shows the impact on stored and
//     paused runs (re-evaluated against the current ceiling on run/resume); this screen never edits
//     a run or resumes one.
// A save is complete only when the PATCH answer carries exactly the sent value(s) AND a re-read
// GET agrees; otherwise the input stays as typed and an error is shown. Invalid input is never sent.
// The Saved notice belongs to the value that was saved: editing again takes it away.
// Unsaved changes guard (D §3.2): while an editor holds an unsaved input or a save is still out, leaving
// by the app (another route) asks first and a refresh / tab close gets the browser's own question.
// It never holds the screen once the session or the administrator role is gone (sign-out, a lost
// token, the role taken away): those moves go through at once (../components/useUnsavedLeaveGuard,
// shared with System Agent since T#4).
import { computed, onMounted, ref, watch, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import SettingsIcon from '../SettingsIcon.vue'
import AttachmentRequestSettingsCard from '../components/AttachmentRequestSettingsCard.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useSystemNav } from '../components/systemNav'
import { useUnsavedLeaveGuard } from '../components/useUnsavedLeaveGuard'
import { getSystemSettings, getSystemStorageRoot, saveRepeatCountMax, saveScratchRetention, saveVerifySetting, type VerifySettingKey } from '../api'
import type { ScratchUnit, SystemSettingRow, SystemStorageRoot } from '../types'

const SCRATCH_UNITS: readonly ScratchUnit[] = ['minute', 'hour', 'day']
const MINUTES_PER_UNIT: Record<ScratchUnit, bigint> = { minute: 1n, hour: 60n, day: 1440n }
// The server turns the pair into a Python timedelta(minutes=…); beyond 999999999 days it overflows
// and answers 422 "too large". The same bound here keeps such a pair from being sent at all.
const MAX_SCRATCH_MINUTES = 999999999n * 1440n + 1439n
const REPEAT_MIN = 1
const REPEAT_MAX = 30

const { t } = useI18n()
const systemNav = useSystemNav()

// ── 저장 Root ──
const root = ref<SystemStorageRoot | null>(null)
const rootLoading = ref(true)
const rootError = ref('')

function isRoot(body: unknown): body is SystemStorageRoot {
  const b = body as Partial<SystemStorageRoot> | null
  return !!b && typeof b.effective_value === 'string' && typeof b.effective_source === 'string'
    && typeof b.env_override === 'boolean' && (b.configured_value === null || typeof b.configured_value === 'string')
}

async function loadRoot() {
  rootLoading.value = true
  rootError.value = ''
  try {
    const body = await getSystemStorageRoot()
    if (!isRoot(body)) throw new Error(t('gui.settings.general.errors.bad_response'))
    root.value = body
  } catch (err) {
    root.value = null
    rootError.value = err instanceof Error ? err.message : String(err)
  } finally {
    rootLoading.value = false
  }
}

function sourceLabel(source: string): string {
  const key = `gui.settings.general.root.sources.${source}`
  return ['env', 'project_override', 'system_setting', 'default'].includes(source) ? t(key) : source
}

// ── settings rows ──
const settingsLoading = ref(true)
const settingsError = ref('')

interface Pair { value: string; unit: string }

function rowValue(rows: SystemSettingRow[] | undefined, key: string): string | null {
  const row = Array.isArray(rows) ? rows.find((r) => r?.setting_key === key) : undefined
  return row && typeof row.setting_value === 'string' ? row.setting_value : null
}

function scratchIn(rows: SystemSettingRow[] | undefined): Pair | null {
  const value = rowValue(rows, 'scratch_ttl_value')
  const unit = rowValue(rows, 'scratch_ttl_unit')
  return value === null || unit === null ? null : { value, unit }
}

/** Load both editors from one GET; a missing row is a failed load, not a default. */
async function loadSettings() {
  settingsLoading.value = true
  settingsError.value = ''
  try {
    const rows = await getSystemSettings()
    const scratch = scratchIn(rows)
    const repeat = rowValue(rows, 'ai_repeat_count_max')
    if (!scratch || repeat === null) throw new Error(t('gui.settings.general.errors.bad_response'))
    // flowgate.v02test.0053 0003-T: the server always answers the two 검증 rows (its shipped default when unset);
    // a server that does not know them yet leaves them at the shipped default, never a failed load.
    for (const item of verifyEditors) item.editor.load(rowValue(rows, item.key))
    scratchConfirmed.value = scratch
    scratchValue.value = scratch.value
    scratchUnit.value = scratch.unit
    repeatConfirmed.value = repeat
    repeatValue.value = repeat
  } catch (err) {
    settingsError.value = err instanceof Error ? err.message : String(err)
  } finally {
    settingsLoading.value = false
  }
}

// ── Scratch retention period ──
const scratchConfirmed = ref<Pair | null>(null)
const scratchValue = ref('')
const scratchUnit = ref('')
const scratchSaving = ref(false)
const scratchError = ref('')
const scratchSaved = ref(false)

const scratchDirty = computed(() =>
  !!scratchConfirmed.value
  && (scratchValue.value.trim() !== scratchConfirmed.value.value || scratchUnit.value !== scratchConfirmed.value.unit))

/** The server's own rule (scratch_retention.parse): a positive integer, minute/hour/day, no overflow. */
const scratchProblem = computed(() => {
  const value = scratchValue.value.trim()
  if (!/^[1-9][0-9]*$/.test(value)) return t('gui.settings.general.scratch.invalid_value')
  if (!(SCRATCH_UNITS as readonly string[]).includes(scratchUnit.value)) return t('gui.settings.general.scratch.invalid_unit')
  if (BigInt(value) * MINUTES_PER_UNIT[scratchUnit.value as ScratchUnit] > MAX_SCRATCH_MINUTES) {
    return t('gui.settings.general.scratch.too_large')
  }
  return ''
})

const scratchCanSave = computed(() => scratchDirty.value && !scratchProblem.value && !scratchSaving.value)

async function saveScratch() {
  if (!scratchCanSave.value) return
  const value = scratchValue.value.trim()
  const unit = scratchUnit.value as ScratchUnit
  scratchSaving.value = true
  scratchError.value = ''
  scratchSaved.value = false
  try {
    const answered = scratchIn(await saveScratchRetention(value, unit))
    if (!answered || answered.value !== value || answered.unit !== unit) {
      scratchError.value = t('gui.settings.general.errors.unconfirmed')
      await rereadScratch()
      return
    }
    const reread = await rereadScratch()
    if (!reread || reread.value !== value || reread.unit !== unit) {
      scratchError.value = t('gui.settings.general.errors.reread_mismatch')
      return
    }
    scratchSaved.value = true
  } catch (err) {
    scratchError.value = err instanceof Error ? err.message : String(err)
  } finally {
    scratchSaving.value = false
  }
}

/** Re-read after a save; the stored pair becomes the confirmed one, the input is left as typed. */
async function rereadScratch(): Promise<Pair | null> {
  try {
    const pair = scratchIn(await getSystemSettings())
    if (pair) scratchConfirmed.value = pair
    return pair
  } catch {
    return null
  }
}

// ── AI iteration limit ──
const repeatConfirmed = ref<string | null>(null)
const repeatValue = ref('')
const repeatSaving = ref(false)
const repeatError = ref('')
const repeatSaved = ref(false)
const lowerConfirmOpen = ref(false)

const repeatDirty = computed(() => repeatConfirmed.value !== null && repeatValue.value.trim() !== repeatConfirmed.value)

const repeatProblem = computed(() => {
  const value = repeatValue.value.trim()
  const n = /^[0-9]+$/.test(value) ? Number(value) : NaN
  return Number.isInteger(n) && n >= REPEAT_MIN && n <= REPEAT_MAX
    ? ''
    : t('gui.settings.general.repeat.invalid', { min: REPEAT_MIN, max: REPEAT_MAX })
})

const repeatCanSave = computed(() => repeatDirty.value && !repeatProblem.value && !repeatSaving.value)

/** A valid new ceiling below the confirmed one (a confirmed value that is not a number counts as no). */
const repeatLowering = computed(() => {
  if (!repeatDirty.value || repeatProblem.value) return false
  const before = Number(repeatConfirmed.value)
  return Number.isInteger(before) && Number(repeatValue.value.trim()) < before
})

function askRepeat() {
  if (!repeatCanSave.value) return
  if (repeatLowering.value) {
    lowerConfirmOpen.value = true
    return
  }
  void saveRepeat()
}

function confirmLower() {
  if (repeatSaving.value) return
  void saveRepeat()
}

async function saveRepeat() {
  if (!repeatCanSave.value) return
  const value = String(Number(repeatValue.value.trim()))
  repeatSaving.value = true
  repeatError.value = ''
  repeatSaved.value = false
  try {
    const answered = rowValue(await saveRepeatCountMax(value), 'ai_repeat_count_max')
    lowerConfirmOpen.value = false
    if (answered !== value) {
      repeatError.value = t('gui.settings.general.errors.unconfirmed')
      await rereadRepeat()
      return
    }
    const reread = await rereadRepeat()
    if (reread !== value) {
      repeatError.value = t('gui.settings.general.errors.reread_mismatch')
      return
    }
    repeatSaved.value = true
  } catch (err) {
    lowerConfirmOpen.value = false
    repeatError.value = err instanceof Error ? err.message : String(err)
  } finally {
    repeatSaving.value = false
  }
}

async function rereadRepeat(): Promise<string | null> {
  try {
    const value = rowValue(await getSystemSettings(), 'ai_repeat_count_max')
    if (value !== null) repeatConfirmed.value = value
    return value
  } catch {
    return null
  }
}

// ── verification defaults (flowgate.v02test.0053 0003-T, 1-A) ──
// Two separate saves, each its own PATCH with its one key: 검증 최대 횟수 (`verify_max_runs`, a whole number from 1) and
// 검증 1per-run time limit (`verify_run_timeout_sec`, the screen types minutes 1~30 and the API stores seconds). A save is
// complete only when the PATCH answer and a re-read GET carry exactly the sent value, as for the repeat ceiling.
interface NumberEditor {
  readonly value: Ref<string>
  readonly saving: Ref<boolean>
  readonly error: Ref<string>
  readonly saved: Ref<boolean>
  readonly dirty: Ref<boolean>
  readonly problem: Ref<string>
  readonly canSave: Ref<boolean>
  load(row: string | null): void
  save(): Promise<void>
}

function numberEditor(config: {
  key: VerifySettingKey
  min: number
  /** Omitted: no upper bound (the count is "a whole number of 1 or more"). */
  max?: number
  problem: () => string
  shownOf: (row: string) => string
  rowOf: (shown: number) => string
  fallback: string
}): NumberEditor {
  const confirmed = ref<string | null>(null)
  const value = ref('')
  const saving = ref(false)
  const error = ref('')
  const saved = ref(false)
  const dirty = computed(() => confirmed.value !== null && value.value.trim() !== confirmed.value)
  const number = computed(() => (/^[0-9]+$/.test(value.value.trim()) ? Number(value.value.trim()) : NaN))
  const problem = computed(() => (Number.isSafeInteger(number.value) && number.value >= config.min && (config.max === undefined || number.value <= config.max) ? '' : config.problem()))
  const canSave = computed(() => dirty.value && !problem.value && !saving.value)

  function load(row: string | null) {
    confirmed.value = config.shownOf(row ?? config.fallback)
    value.value = confirmed.value
  }

  async function reread(): Promise<string | null> {
    try {
      const row = rowValue(await getSystemSettings(), config.key)
      if (row !== null) confirmed.value = config.shownOf(row)
      return row
    } catch {
      return null
    }
  }

  async function save() {
    if (!canSave.value) return
    const sent = config.rowOf(number.value)
    saving.value = true
    error.value = ''
    saved.value = false
    try {
      const answered = rowValue(await saveVerifySetting(config.key, sent), config.key)
      if (answered !== sent) {
        error.value = t('gui.settings.general.errors.unconfirmed')
        await reread()
        return
      }
      if ((await reread()) !== sent) {
        error.value = t('gui.settings.general.errors.reread_mismatch')
        return
      }
      saved.value = true
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      saving.value = false
    }
  }

  watch(value, () => {
    saved.value = false
  })
  return { value, saving, error, saved, dirty, problem, canSave, load, save }
}

const VERIFY_MINUTES_MAX = 30
const verifyEditors = [
  {
    name: 'runs',
    key: 'verify_max_runs' as const,
    editor: numberEditor({
      key: 'verify_max_runs', min: 1, fallback: '3',
      problem: () => t('gui.settings.general.verify.runs.invalid', { min: 1 }),
      shownOf: (row) => row,
      rowOf: (shown) => String(shown),
    }),
  },
  {
    name: 'minutes',
    key: 'verify_run_timeout_sec' as const,
    editor: numberEditor({
      key: 'verify_run_timeout_sec', min: 1, max: VERIFY_MINUTES_MAX, fallback: '180',
      problem: () => t('gui.settings.general.verify.minutes.invalid', { min: 1, max: VERIFY_MINUTES_MAX }),
      shownOf: (row) => (/^[0-9]+$/.test(row) ? String(Math.ceil(Number(row) / 60)) : row),
      rowOf: (shown) => String(shown * 60),
    }),
  },
]
const verifySaving = computed(() => verifyEditors.some((item) => item.editor.saving.value))
const verifyDirty = computed(() => verifyEditors.some((item) => item.editor.dirty.value))

// A success notice is for the value that was saved, not for what is typed afterwards.
watch([scratchValue, scratchUnit], () => {
  scratchSaved.value = false
})
watch(repeatValue, () => {
  repeatSaved.value = false
})

// ── Unsaved changes guard ──
const unsaved = computed(() => scratchDirty.value || repeatDirty.value || scratchSaving.value || repeatSaving.value || verifyDirty.value || verifySaving.value)
const { open: leaveOpen, answer: answerLeave } = useUnsavedLeaveGuard(() => unsaved.value)

onMounted(() => {
  void loadRoot()
  void loadSettings()
})
</script>

<style scoped>
.fgs-input.num {
  width: 110px;
}

.fgs-input.unit {
  width: 100px;
}

.confirm {
  display: grid;
  justify-items: center;
  gap: 12px;
  padding: 10px 0 4px;
  text-align: center;
}

.conf-ico {
  width: 64px;
  height: 64px;
  border-radius: 18px;
  display: grid;
  place-items: center;
}

.conf-ico.warn { background: #2a210e; color: #e2c76f; }

.conf-who {
  font-size: 15px;
  font-weight: 700;
}

.conf-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}
</style>
