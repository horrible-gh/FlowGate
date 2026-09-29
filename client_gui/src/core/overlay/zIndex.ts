// Shared overlay z-index allocation (T0006 §9/§11).
//
// ModalShell and ModelessShell both need a value that clears the existing
// `--fg2-overlay-z` layer (Dropdown menus / Tooltip bubbles, 9999) and that a later
// overlay — or a Modeless panel brought back to front — always lands above whatever
// opened before it. Both shells duplicated the same "one counter, always increase"
// logic, so it is extracted here instead of kept twice (T0006 §9 rule: 2+ real
// duplicates -> shared module).
const OVERLAY_Z_BASE = 10000

let top = OVERLAY_Z_BASE

/** Returns a z-index higher than every value returned before it. Call again on the
 *  same overlay to bring it back to front (e.g. ModelessShell on panel mousedown). */
export function allocateOverlayZIndex(): number {
  top += 1
  return top
}
