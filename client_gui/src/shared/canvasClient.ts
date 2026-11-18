// One browser-tab identity shared by Canvas and document settings requests.
function newCanvasClientId(): string {
  const bytes = new Uint8Array(9)
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes)
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  return `tab_${[...bytes].map((b) => b.toString(36).padStart(2, '0')).join('')}`
}

/** This tab's `x-canvas-client-id` (P0008 §1.1: 1~64 of [A-Za-z0-9_-], kept while the tab lives). */
export const CANVAS_CLIENT_ID = newCanvasClientId()
export const canvasHeaders = () => ({ headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } })

