// flowgate.v02.0060 — ⑤ palette menu: palette card only (0006-D rev2 §5.3 ⑤). The palette blank area is
// not a menu target (PM decision, rev2), so this part has no blank kind and no card submenu.

import { action, makeMenu } from './model'
import type { PaletteTarget, PartInput } from './types'
export interface PaletteAvailability { readonly canPlace: boolean }
export function paletteMenu({ target, available, connected }: PartInput<PaletteTarget, PaletteAvailability>) {
  return makeMenu(target, [action('palette.addCard', 'addCard', '＋', !available.canPlace)], connected)
}

