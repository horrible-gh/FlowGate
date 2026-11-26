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

          <!-- Title AI: ai_title_provider only (v02test.0096 F96-6, moved here from AI 관리 › 기타 설정) -->
          <div class="fgs-sec" data-card="title-ai">
            <div class="fgs-sec-head"><h3>{{ t('gui.ai.providers.title_ai') }}</h3></div>
            <div class="fgs-rows">
              <div v-if="titleConfirmed === null" class="fgs-row sub">
                <span class="fgs-error" data-testid="title-ai-missing">{{ t('gui.settings.general.errors.bad_response') }}</span>
              </div>
              <div v-else class="fgs-row">
                <div class="fgs-row-label">
                  <label for="sys-title-ai">{{ t('gui.ai.providers.title_ai_system') }}</label>
                </div>
                <div class="fgs-row-ctrl">
                  <span v-if="titleSaved && !titleDirty && !titleError" class="fgs-muted" data-testid="title-ai-saved">{{ t('gui.settings.saved') }}</span>
                  <select
                    id="sys-title-ai"
                    v-model="titleValue"
                    class="fgs-input title-ai"
                    :class="{ dirty: titleDirty }"
                    :disabled="titleSaving"
                    data-testid="title-ai-value"
                  >
                    <option v-for="opt in titleOptions" :key="opt.value" :value="opt.value" :disabled="opt.disabled">{{ opt.label }}</option>
                  </select>
                  <button
                    type="button"
                    class="fgs-btn pri"
                    :disabled="!titleCanSave"
                    data-testid="title-ai-save"
                    @click="saveTitle"
                  >{{ titleSaving ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
                </div>
              </div>
              <div v-if="titleError" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="title-ai-error">{{ titleError }}</span>
              </div>
            </div>
          </div>

          <!-- AI attachment read limit: attach_read_max_mb only (MB, 1~64, applies without a restart) -->
          <div class="fgs-sec" data-card="attach-read">
            <div class="fgs-sec-head"><h3>{{ t('gui.settings.general.attach_read.title') }}</h3></div>
            <div class="fgs-rows">
              <div v-if="attachConfirmed === null" class="fgs-row sub">
                <span class="fgs-error" data-testid="attach-read-missing">{{ t('gui.settings.general.errors.bad_response') }}</span>
              </div>
              <div v-else class="fgs-row">
                <div class="fgs-row-label">
                  <label for="sys-attach-read-mb">{{ t('gui.settings.general.attach_read.label') }}</label>
                  <small>{{ t('gui.settings.general.attach_read.hint') }}</small>
                </div>
                <div class="fgs-row-ctrl">
                  <span v-if="attachSaved && !attachDirty && !attachError" class="fgs-muted" data-testid="attach-read-saved">{{ t('gui.settings.saved') }}</span>
                  <input
                    id="sys-attach-read-mb"
                    v-model="attachValue"
                    class="fgs-input num"
                    :class="{ dirty: attachDirty }"
                    inputmode="numeric"
                    autocomplete="off"
                    :disabled="attachSaving"
                    data-testid="attach-read-value"
                  />
                  <button
                    type="button"
                    class="fgs-btn pri"
                    :disabled="!attachCanSave"
                    data-testid="attach-read-save"
                    @click="saveAttach"
                  >{{ attachSaving ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
                </div>
              </div>
              <div v-if="attachDirty && attachProblem" class="fgs-row sub">
                <span class="fgs-error" data-testid="attach-read-invalid">{{ attachProblem }}</span>
              </div>
              <div v-if="attachError" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="attach-read-error">{{ attachError }}</span>
              </div>
            </div>
          </div>
        </template>

        <!-- System default limits for AI attachment approval requests (inherited unless the project overrides them). -->
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
        <p v-if="scratchSaving || repeatSaving || attachSaving || titleSaving" class="conf-text fgs-muted" data-testid="leave-saving">{{ t('gui.settings.general.leave.saving') }}</p>
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
//   · title AI (v02test.0096 F96-6): ai_title_provider alone — 'off', 'executor' or a system AI id — with the save /
//     load of the AI screen (features/ai/api saveTitleProvider / fetchTitleProvider). Its PATCH answer carries no
//     row, so the re-read alone confirms it.
// A save is complete only when the PATCH answer carries exactly the sent value(s) AND a re-read
// GET agrees; otherwise the input stays as typed and an error is shown. Invalid input is never sent.
// The Saved notice belongs to the value that was saved: editing again takes it away.
// Unsaved changes guard (D §3.2): while an editor holds an unsaved input or a save is still out, leaving
// by the app (another route) asks first and a refresh / tab close gets the browser's own question.
// It never holds the screen once the session or the administrator role is gone (sign-out, a lost
// token, the role taken away): those moves go through at once (../components/useUnsavedLeaveGuard,
// shared with System Agent since the earlier implementation).
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchSystemAi, fetchTitleProvider, saveTitleProvider } from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'
import { TITLE_SEPARATOR, titleChoices, type TitleChoice } from '@shared/myAiPreferences'
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
import { getSystemSettings, getSystemStorageRoot, saveAttachReadMaxMb, saveRepeatCountMax, saveScratchRetention } from '../api'
import type { ScratchUnit, SystemSettingRow, SystemStorageRoot } from '../types'

const SCRATCH_UNITS: readonly ScratchUnit[] = ['minute', 'hour', 'day']
const MINUTES_PER_UNIT: Record<ScratchUnit, bigint> = { minute: 1n, hour: 60n, day: 1440n }
// The server turns the pair into a Python timedelta(minutes=…); beyond 999999999 days it overflows
// and answers 422 "too large". The same bound here keeps such a pair from being sent at all.
const MAX_SCRATCH_MINUTES = 999999999n * 1440n + 1439n
const REPEAT_MIN = 1
const REPEAT_MAX = 30
const ATTACH_READ_MIN_MB = 1
const ATTACH_READ_MAX_MB = 64
const ATTACH_READ_KEY = 'attach_read_max_mb'
const TITLE_KEY = 'ai_title_provider'

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
    scratchConfirmed.value = scratch
    scratchValue.value = scratch.value
    scratchUnit.value = scratch.unit
    repeatConfirmed.value = repeat
    repeatValue.value = repeat
    // Its own editor: a server without this row only disables this card, not the other two.
    const attach = rowValue(rows, ATTACH_READ_KEY)
    attachConfirmed.value = attach
    attachValue.value = attach ?? ''
    // Same for the title AI: an empty or missing row only disables its card.
    const title = rowValue(rows, TITLE_KEY) || null
    titleConfirmed.value = title
    titleValue.value = title ?? ''
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

// ── AI attachment read limit (MB) ──
const attachConfirmed = ref<string | null>(null)
const attachValue = ref('')
const attachSaving = ref(false)
const attachError = ref('')
const attachSaved = ref(false)

const attachDirty = computed(() => attachConfirmed.value !== null && attachValue.value.trim() !== attachConfirmed.value)

const attachProblem = computed(() => {
  const value = attachValue.value.trim()
  const n = /^[0-9]+$/.test(value) ? Number(value) : NaN
  return Number.isInteger(n) && n >= ATTACH_READ_MIN_MB && n <= ATTACH_READ_MAX_MB
    ? ''
    : t('gui.settings.general.attach_read.invalid', { min: ATTACH_READ_MIN_MB, max: ATTACH_READ_MAX_MB })
})

const attachCanSave = computed(() => attachDirty.value && !attachProblem.value && !attachSaving.value)

async function saveAttach() {
  if (!attachCanSave.value) return
  const value = String(Number(attachValue.value.trim()))
  attachSaving.value = true
  attachError.value = ''
  attachSaved.value = false
  try {
    const answered = rowValue(await saveAttachReadMaxMb(value), ATTACH_READ_KEY)
    if (answered !== value) {
      attachError.value = t('gui.settings.general.errors.unconfirmed')
      await rereadAttach()
      return
    }
    const reread = await rereadAttach()
    if (reread !== value) {
      attachError.value = t('gui.settings.general.errors.reread_mismatch')
      return
    }
    attachSaved.value = true
  } catch (err) {
    attachError.value = err instanceof Error ? err.message : String(err)
  } finally {
    attachSaving.value = false
  }
}

async function rereadAttach(): Promise<string | null> {
  try {
    const value = rowValue(await getSystemSettings(), ATTACH_READ_KEY)
    if (value !== null) attachConfirmed.value = value
    return value
  } catch {
    return null
  }
}

// ── Title AI (ai_title_provider) ──
const titleConfirmed = ref<string | null>(null)
const titleValue = ref('')
const titleSaving = ref(false)
const titleError = ref('')
const titleSaved = ref(false)
// System AI for the choices, in the system order. A failed read leaves only 끄기 / 답한 AI 그대로 (and the stored value).
const systemAi = ref<ProviderItem[]>([])

async function loadSystemAi() {
  try {
    systemAi.value = (await fetchSystemAi()).providers ?? []
  } catch {
    systemAi.value = []
  }
}

// 끄기 / 답한 AI 그대로 / separator / the enabled system AI. An off AI is left out (the server refuses it), except the
// stored one, which titleChoices always offers so the select never claims another value.
const titleOptions = computed<TitleChoice[]>(() => {
  const current = titleConfirmed.value
  const ai = systemAi.value.filter((p) => p.enabled || p.id === current).map((p) => ({ value: p.id, label: p.name }))
  return titleChoices(current, [
    { value: 'off', label: t('gui.ai.providers.title_ai_off') },
    { value: 'executor', label: t('gui.ai.providers.title_ai_executor') },
  ], ai)
})

const titleDirty = computed(() => titleConfirmed.value !== null && titleValue.value !== titleConfirmed.value)
const titleCanSave = computed(() => titleDirty.value && !!titleValue.value && titleValue.value !== TITLE_SEPARATOR && !titleSaving.value)

async function saveTitle() {
  if (!titleCanSave.value) return
  const value = titleValue.value
  titleSaving.value = true
  titleError.value = ''
  titleSaved.value = false
  try {
    await saveTitleProvider(value)
    const reread = await rereadTitle()
    if (reread !== value) {
      titleError.value = t('gui.settings.general.errors.reread_mismatch')
      return
    }
    titleSaved.value = true
  } catch (err) {
    titleError.value = err instanceof Error ? err.message : String(err)
  } finally {
    titleSaving.value = false
  }
}

async function rereadTitle(): Promise<string | null> {
  try {
    const value = await fetchTitleProvider()
    if (value !== null) titleConfirmed.value = value
    return value
  } catch {
    return null
  }
}

// A success notice is for the value that was saved, not for what is typed afterwards.
watch([scratchValue, scratchUnit], () => {
  scratchSaved.value = false
})
watch(repeatValue, () => {
  repeatSaved.value = false
})
watch(attachValue, () => {
  attachSaved.value = false
})
watch(titleValue, () => {
  titleSaved.value = false
})

// ── Unsaved changes guard ──
const unsaved = computed(() =>
  scratchDirty.value || repeatDirty.value || attachDirty.value || titleDirty.value
  || scratchSaving.value || repeatSaving.value || attachSaving.value || titleSaving.value)
const { open: leaveOpen, answer: answerLeave } = useUnsavedLeaveGuard(() => unsaved.value)

onMounted(() => {
  void loadRoot()
  void loadSettings()
  void loadSystemAi()
})
</script>

<style scoped>
.fgs-input.num {
  width: 110px;
}

.fgs-input.unit {
  width: 100px;
}

.fgs-input.title-ai {
  width: 220px;
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
