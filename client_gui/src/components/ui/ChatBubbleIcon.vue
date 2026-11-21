<template>
  <!-- The Designer right dock's '대화' tab artwork: a filled sky-blue bubble with three dots. Shared so the flow tab's
       "chat AI responding" mark is the same icon. -->
  <svg class="chat-bubble-icon" viewBox="0 0 16 16" :width="size" :height="size" aria-hidden="true" focusable="false">
    <path d="M3 2.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H7l-4 3v-3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Z" fill="#61c6ff" stroke="#b9eaff" stroke-width="0.75" stroke-linejoin="round" />
    <g fill="#174c78">
      <circle cx="5" cy="7" r="0.85" />
      <circle cx="8" cy="7" r="0.85" />
      <circle cx="11" cy="7" r="0.85" />
    </g>
  </svg>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 14 })
</script>
