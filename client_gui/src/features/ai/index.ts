// Public interface of features/ai (WP0002 / T0003)
export { default as AiSettingsView } from './AiSettingsView.vue'
export * from './types'
export * from './api'
// Shared role controls and first-run coordinator, consumed by Designer and account settings.
export { default as AiRoleSelect } from './roles/AiRoleSelect.vue'
export { default as AiRoleSetupModeless } from './roles/AiRoleSetupModeless.vue'
export { installAiRoleMessages } from './roles/messages'
export * from './roles/firstRunSetup'