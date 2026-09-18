<template>
  <!--
    Git archive catalogue — flowgate.default.0560 T0018 (4순위, NR0011 원장 ID 42).
    0339 R0001: reversible Git archive catalogue. Permanent deletion is intentionally
    reachable only from this second-stage screen.

    D0008 §4 split this out of `MainPanel.vue`; D0008 maps it to `readonly`.

    T0018 §2.3-5 — read this name carefully. `readonly` here means only that the FOOTER has
    no primary and no danger action: the one footer button is [닫기]. The BODY is not
    read-only at all — it carries [복원] per row and a [영구삭제] behind an acknowledgement
    checkbox, both of which call the server. D0008 §1 keeps feature actions and API calls out
    of the common layer, so that is not a design violation; it is written down here so that a
    later reader does not take `variant="readonly"` as a statement that this screen is safe.

    `:close-on-backdrop="false"` stays explicit (T0018 §2.2-3): 0412 T0004 removed
    backdrop-close here and NR0011 records BD=X. `readonly` defaults to `false` too since
    0560 T0035, so this now restates rather than overrides the table — kept explicit so
    the contract does not depend on the default staying put.
  -->
  <DialogShell
    :open="visible"
    variant="readonly"
    size="xl"
    surface-class="git-archive-dialog"
    :close-on-backdrop="false"
    :busy="busy"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader :title="t('main.git_archive_panel.title')" icon="archive" @close="emit('close')">
        <template #actions>
          <span class="badge badge-yellow">{{ items.length }}</span>
        </template>
      </DialogHeader>
    </template>

    <template #default>
      <div class="git-archive-body">
        <div class="git-archive-intro">
          <AppIcon name="arrow-counter-clockwise" />
          <span>{{ t('main.git_archive_panel.intro') }}</span>
        </div>
        <div v-if="loading" class="git-archive-state">
          <AppIcon name="spinner" spin /> {{ t('main.git_archive_panel.loading') }}
        </div>
        <div v-else-if="errorMessage" class="git-archive-state git-archive-state--error">
          {{ errorMessage }}
          <button class="btn btn-secondary btn-sm" type="button" @click="emit('retry')">
            {{ t('main.git_archive_panel.retry') }}
          </button>
        </div>
        <div v-else-if="items.length === 0" class="git-archive-state">
          {{ t('main.git_archive_panel.empty') }}
        </div>
        <div v-else class="git-archive-list">
          <article v-for="item in items" :key="item.group_id" class="git-archive-row">
            <input
              :checked="picked.includes(item.group_id)"
              type="checkbox"
              :disabled="busy || item.status !== 'archived'"
              :title="t('main.git_archive_panel.pick')"
              @change="togglePicked(item.group_id, ($event.target as HTMLInputElement).checked)"
            />
            <div class="git-archive-row__body">
              <strong>
                {{ item.group_id }}: {{ item.title }}
                <span class="badge badge-gray">{{ t('main.git_archive_panel.archived') }}</span>
              </strong>
              <span class="git-archive-branch">{{ item.branch }}</span>
              <span>
                {{ formatTime(item.archived_at) }} ·
                {{ t('main.git_archive_panel.commits') }} {{ item.commit_count ?? 0 }} ·
                {{ t('main.git_archive_panel.files') }} {{ item.changed_file_count ?? 0 }} ·
                {{ t('main.git_archive_panel.base') }} <code class="git-archive-sha">{{ shortSha(item.base_sha) }}</code>
              </span>
              <span v-if="item.reason" class="git-archive-reason">{{ t('main.git_archive_panel.reason') }}: {{ item.reason }}</span>
              <code class="git-archive-refs">
                {{ item.head_ref }} → {{ shortSha(item.head_sha) }}
                <template v-if="item.stash_ref"><br />{{ item.stash_ref }} → {{ shortSha(item.stash_sha) }}</template>
              </code>
            </div>
            <button
              class="btn btn-primary btn-sm"
              type="button"
              :disabled="busy || item.status !== 'archived'"
              @click="emit('restore', item)"
            >
              <AppIcon name="arrow-counter-clockwise" /> {{ t('main.git_archive_panel.restore') }}
            </button>
          </article>
        </div>
        <div v-if="items.length" class="git-archive-purge">
          <strong><AppIcon name="warning" /> {{ t('main.git_archive_panel.purge_title') }}</strong>
          <p>{{ t('main.git_archive_panel.purge_desc') }}</p>
          <label>
            <input
              :checked="purgeConfirmed"
              type="checkbox"
              :disabled="busy"
              @change="emit('update:purgeConfirmed', ($event.target as HTMLInputElement).checked)"
            />
            {{ t('main.git_archive_panel.purge_confirm') }}
          </label>
          <button
            class="btn btn-danger btn-sm"
            type="button"
            :disabled="busy || !purgeConfirmed || picked.length === 0"
            @click="emit('purge')"
          >
            <AppIcon name="trash" /> {{ t('main.git_archive_panel.purge') }} ({{ picked.length }})
          </button>
        </div>
      </div>
    </template>

    <template #footer>
      <DialogFooter :actions="actions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AppIcon from '@shared/AppIcon.vue'

import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'

/** Moved here from `MainPanel.vue` with the markup that reads it; MainPanel imports it back. */
export interface GitArchiveItem {
  status: 'archiving' | 'archived'
  project_id: string
  group_id: string
  title: string
  branch: string
  archived_at: string | null
  reason: string | null
  base_sha: string | null
  head_sha: string
  head_ref: string
  stash_sha: string | null
  stash_ref: string | null
  commit_count: number
  changed_file_count: number
}

const props = defineProps<{
  visible: boolean
  items: GitArchiveItem[]
  picked: string[]
  purgeConfirmed: boolean
  loading: boolean
  busy: boolean
  errorMessage: string
  formatTime: (value: string | null) => string
  shortSha: (value: string | null) => string
}>()

const emit = defineEmits<{
  'update:picked': [value: string[]]
  'update:purgeConfirmed': [value: boolean]
  close: []
  retry: []
  restore: [item: GitArchiveItem]
  purge: []
}>()

const { t } = useI18n()

function togglePicked(groupId: string, checked: boolean) {
  const next = props.picked.filter((id) => id !== groupId)
  if (checked) next.push(groupId)
  emit('update:picked', next)
}

/**
 * `[닫기]` alone — D0008 §3-7 forbids forcing the Form footer shape onto a read-only
 * surface, so there is no primary and no cancel here. The button is `dismiss`, the third
 * cancel meaning of D0008 §3: nothing in this footer commits or aborts anything, it puts a
 * catalogue away. `dismiss` also stays clickable while `busy` (L0009 §2 "Busy / Blocking"),
 * which matches the pre-migration button — it was disabled during a restore/purge, but
 * `closeGitArchive()` already refuses to close mid-flight, so the guard is still in force
 * where it actually lives.
 */
const actions = computed<DialogAction[]>(() => [
  {
    id: 'close',
    label: t('main.git_archive_panel.close'),
    role: 'dismiss',
    disabled: props.busy,
    onSelect: () => emit('close'),
  },
])
</script>

<style scoped>
/* 0339: archive stays amber/reversible; only purge uses the destructive red. */
.git-archive-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.git-archive-intro {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface-h);
  color: var(--text-s);
  font-size: .78rem;
  line-height: 1.6;
}
.git-archive-intro :deep(svg) {
  flex: 0 0 auto;
  margin-top: 2px;
  color: #d97706;
}
.git-archive-state {
  display: flex;
  min-height: 120px;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-m);
}
.git-archive-state--error {
  color: var(--danger);
}
.git-archive-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.git-archive-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
}
.git-archive-row__body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
  color: var(--text-m);
  font-size: .73rem;
}
.git-archive-row__body strong {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  overflow-wrap: anywhere;
  color: var(--text);
  font-size: .8rem;
}
.git-archive-row__body code {
  font-family: 'JetBrains Mono', monospace;
}
.git-archive-row__body .git-archive-sha {
  padding: 0;
  background: none;
  color: var(--text-s);
}
.git-archive-row__body .git-archive-refs {
  overflow-wrap: anywhere;
  padding: 5px 7px;
  border-radius: 5px;
  background: var(--surface-h);
  color: var(--text-s);
  line-height: 1.55;
  white-space: normal;
}
.git-archive-reason {
  color: var(--text-s);
}
.git-archive-branch {
  color: var(--primary);
  font-family: 'JetBrains Mono', monospace;
}
.git-archive-purge {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px 16px;
  margin-top: 4px;
  padding: 12px;
  border: 1px dashed rgba(220, 38, 38, .45);
  border-radius: var(--r);
  background: rgba(254, 242, 242, .45);
  color: var(--text-s);
  font-size: .76rem;
}
.git-archive-purge strong,
.git-archive-purge p {
  grid-column: 1 / -1;
  margin: 0;
}
.git-archive-purge strong {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--danger);
}
.git-archive-purge label {
  display: flex;
  align-items: center;
  gap: 7px;
}
@media (max-width: 680px) {
  .git-archive-row {
    grid-template-columns: auto minmax(0, 1fr);
  }
  .git-archive-row > .btn {
    grid-column: 2;
    justify-self: start;
  }
  .git-archive-purge {
    grid-template-columns: 1fr;
  }
  .git-archive-purge .btn {
    justify-self: start;
  }
}
</style>

<!--
  Unscoped: `surface-class` lands on the dialog SURFACE, which DialogShell renders and
  teleports out of this component's subtree. Same `min(920px, …)` track
  `.git-archive-modal` measured. The height cap is the `panel` surface's own 88vh — the
  old `min(820px, 100dvh - 32px)` was the same intent written by hand.
-->
<style>
.fg-dialog-surface.git-archive-dialog {
  width: min(920px, calc(100vw - 32px));
}
</style>
