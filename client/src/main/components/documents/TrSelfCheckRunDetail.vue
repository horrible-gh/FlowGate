<template>
  <div data-testid="tr-self-check-run-detail">
    <p>Status: {{ run.status }} · Exit code: {{ run.exit_code ?? '—' }} · Timed out: {{ run.timed_out ? 'yes' : 'no' }}</p>
    <!-- 0638 T#2: a run started by the TR(new) worker before the TR existed and attached to it on registration. -->
    <p v-if="run.linked_at" class="form-hint" data-testid="tr-self-check-linked">TR 등록 전 실행 · {{ run.linked_at }}에 이 TR로 연결됨</p>
    <p v-if="run.source_changed_during_run || run.worktree_state_changed" class="alert alert-warning">Source or worktree state changed during this run.</p>
    <p v-if="run.error_code">{{ run.error_code }}</p>
    <label class="form-label">stdout</label><pre class="code-block">{{ run.stdout_tail || '' }}</pre>
    <label class="form-label">stderr</label><pre class="code-block">{{ run.stderr_tail || '' }}</pre>
  </div>
</template>

<script lang="ts">
export interface SelfCheckRun {
  self_check_run_id: string;
  status: string;
  group_id?: string | null;
  tr_doc_id?: string | null;
  created_at?: string | null;
  linked_at?: string | null;
  program?: string | null;
  cancel_requested?: boolean;
  exit_code?: number | null;
  timed_out?: boolean;
  stdout_tail?: string | null;
  stderr_tail?: string | null;
  source_changed_during_run?: boolean;
  worktree_state_changed?: boolean;
  error_code?: string | null;
}
</script>

<script setup lang="ts">
defineProps<{ run: SelfCheckRun }>();
</script>
