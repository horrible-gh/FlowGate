// Public interface of features/ai
export { default as AiSettingsView } from './AiSettingsView.vue'
export * from './types'
export * from './api'
// Shared role controls and first-run coordinator, consumed by Designer and account settings.
export { default as AiRoleSelect } from './roles/AiRoleSelect.vue'
export { default as AiRoleSetupModeless } from './roles/AiRoleSetupModeless.vue'
export { installAiRoleMessages } from './roles/messages'
export * from './roles/firstRunSetup'
// the run result windows (1st summary / 2nd details) and when they open, mounted by the Designer Shell.
export { default as RunResultWindow } from './runResult/RunResultWindow.vue'
export { default as RunResultDetailWindow } from './runResult/RunResultDetailWindow.vue'
export * from './runResult/runResultWatch'
export { attachmentPendingLine } from './runResult/runResultView'
// v02test.0096: "AI 관리" as one Designer window (내 AI / 기타 설정 / 방침), opened from the taskbar menu cards.
export { default as AiManageWindow } from './windows/AiManageWindow.vue'
export { installAiManageMessages } from './manage/messages'
export type { AiManageTab, AiWindowTab } from './manage/aiManageTabs'
