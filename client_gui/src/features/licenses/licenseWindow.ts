// F86 — the one place that opens the open-source license window. Every screen (the chat model list, 시스템 › 음성 입력,
// the sidebar's 오픈소스 고지) calls `openLicenseWindow` so the same single window comes up; App.vue mounts it once.
// A tab is a voice engine id from the voice catalog or `LICENSE_PROJECT_TAB` for the FlowGate-wide notices.
import { reactive, readonly } from 'vue'

export const LICENSE_PROJECT_TAB = 'project'

const state = reactive({
  open: false,
  tab: LICENSE_PROJECT_TAB as string,
  /** Grows on every open request, so an already open window comes to the front again. */
  seq: 0,
})

export const licenseWindowState = readonly(state)

/** Opens (or raises) the license window on an engine tab, or on the FlowGate-wide tab when none is given. */
export function openLicenseWindow(tab: string = LICENSE_PROJECT_TAB): void {
  state.tab = tab || LICENSE_PROJECT_TAB
  state.open = true
  state.seq += 1
}

export function selectLicenseTab(tab: string): void {
  state.tab = tab || LICENSE_PROJECT_TAB
}

export function closeLicenseWindow(): void {
  state.open = false
}
