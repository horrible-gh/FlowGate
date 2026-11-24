import { reactive } from 'vue'

// Whether the project list is open as a floating window (the Designer taskbar's [Open project]), minimized or not.
// ProjectDialog writes it; the taskbar reads it to light its button. Plain state, so the taskbar needs no store.
export const projectWindowUi = reactive({ open: false })
