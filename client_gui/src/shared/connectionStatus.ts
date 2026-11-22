// 0060: one place that says the server cannot be reached. shared/api.ts sets it while its recovery
// loop runs (a GET failed after its retries, until a GET or the recovery probe succeeds); the App Shell
// shows one banner for it, and the panels keep their last good content instead of each showing its own
// red failure while the banner is up.
import { readonly, ref } from 'vue'

const lost = ref(false)

/** True while the server is unreachable and the app is reconnecting by itself. */
export const connectionLost = readonly(lost)

/** @internal — shared/api.ts only (and tests). */
export function setConnectionLost(value: boolean): void {
  lost.value = value
}
