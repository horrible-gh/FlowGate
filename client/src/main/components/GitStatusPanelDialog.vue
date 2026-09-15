<template>
  <!--
    "관제소" — the project Git status panel, reached from the safety-net menu.

    flowgate.default.0560 T0020 (4.5순위, NR0011 원장 ID 39). T0018/TR0019 already moved this
    instance onto the common layer, but left the `<DialogShell>` block inside
    `GitActionMenu.vue`. NR0005 §13's 4.5순위 is about the other half of D0008 §4 — "각
    instance는 독립 dialog component로 분리하고, 부모 View는 open state와 데이터 전달만
    담당한다" — so the block lives here now and `GitActionMenu.vue` keeps `panelOpen` and the
    project id it hands down.

    Nothing about the dialog's behaviour changes with the move: `readonly` (D0008 §6), no
    footer (it never had one — the X is the only way out, T0018 §2.2-2), and
    `:close-on-backdrop="false"` stays explicit because 0412 T0004 fixed backdrop-no-close
    for this overlay (NR0011 BD=X). `readonly`'s variant default is `false` too since 0560
    T0035, so this now restates rather than overrides the table — kept explicit anyway.
  -->
  <DialogShell
    :open="open"
    variant="readonly"
    size="lg"
    :close-on-backdrop="false"
    surface-class="git-panel-dialog"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader
        :title="t('main.git_status.title')"
        icon="tree-structure"
        @close="emit('close')"
      />
    </template>
    <template #default>
      <GitStatusPanel
        v-if="projectId"
        :project-id="projectId"
        @open-group="(groupId: string) => emit('open-group', groupId)"
      />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import GitStatusPanel from './GitStatusPanel.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'

defineProps<{
  open: boolean
  /** The feature's data, owned by `GitActionMenu` and only handed down here (D0008 §1). */
  projectId: string | null
}>()

const emit = defineEmits<{ close: []; 'open-group': [groupId: string] }>()

const { t } = useI18n()
</script>

<!--
  Unscoped on purpose: `surface-class` lands on the dialog SURFACE, which DialogShell
  renders and teleports out of this component's subtree, so a scoped rule could never
  reach it. The width is the same 620px track `.git-panel-modal` measured; the body's
  own scroll cap is `.fg-dialog-body`'s (`overflow: auto` + the panel max-height), which
  is what `.git-panel-modal-bd { max-height: 70vh }` used to do by hand.
-->
<style>
.fg-dialog-surface.git-panel-dialog {
  width: 620px;
}
</style>
