<template>
  <!-- flowgate.default.0675 T0004: one placed activity row's card, whichever kind it is,
       so every anchor slot in ConversationView draws it the same way. The card stays the
       row's direct child (the .conv-activity width rule targets it). -->
  <ChatCommandCard
    v-if="item.kind === 'command'"
    :command="item.command"
    :can-decide="canDecide"
    :deciding="decidingId === item.command.request_id"
    @decide="(decision) => onDecide(decision)"
  />
  <ChatRunChangeCard v-else :change="item.change" @open="(path) => onOpen(path)" />
</template>

<script setup lang="ts">
import ChatCommandCard from './ChatCommandCard.vue'
import ChatRunChangeCard from './ChatRunChangeCard.vue'
import type { ActivityItem, ChatCommand, RunChange } from './chatActivityTypes'

const props = defineProps<{
  item: ActivityItem
  canDecide?: boolean
  decidingId?: string | null
}>()

const emit = defineEmits<{
  decide: [command: ChatCommand, decision: 'approve' | 'reject' | 'cancel']
  open: [change: RunChange, path: string | null]
}>()

function onDecide(decision: 'approve' | 'reject' | 'cancel'): void {
  if (props.item.kind === 'command') emit('decide', props.item.command, decision)
}

function onOpen(path: string | null): void {
  if (props.item.kind === 'change') emit('open', props.item.change, path)
}
</script>
