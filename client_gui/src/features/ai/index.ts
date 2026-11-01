// Public interface of features/ai (WP0002 / T0003)
export { default as AiSettingsView } from './AiSettingsView.vue'
export * from './types'
export * from './api'
// Shared role controls and first-run coordinator, consumed by Designer and account settings.
export { default as AiRoleSelect } from './roles/AiRoleSelect.vue'
export { default as AiRoleSetupModeless } from './roles/AiRoleSetupModeless.vue'
export { installAiRoleMessages } from './roles/messages'
export * from './roles/firstRunSetup'
// flowgate.v02.0113 T#5: the run result windows (1st summary / 2nd details) and when they open, mounted by the Designer Shell.
export { default as RunResultWindow } from './runResult/RunResultWindow.vue'
export { default as RunResultDetailWindow } from './runResult/RunResultDetailWindow.vue'
export * from './runResult/runResultWatch'