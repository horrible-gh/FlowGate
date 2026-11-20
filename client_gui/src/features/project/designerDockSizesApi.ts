// flowgate.v02test.0056 (R 0056.0001 §2) — reading and saving the Designer's three Dock sizes.
//
// They are the logged-in person's own settings: GET/PATCH /api/v1/me/ui-settings carries them as
// designer_left_width / designer_right_width / designer_bottom_height (the server keeps one row per
// person, so every browser of the same account shows the same sizes). The Designer Shell reaches
// these through the store (loadDesignerDockSizes / saveDesignerDockSizes).
//
// Neither call ever throws. A failed read means "draw the defaults"; a failed save leaves the
// screen as dragged, shows nothing, and the next save sends the sizes again (R §2.2).

import { getRequest, patchRequest } from '@shared/api'

const UI_SETTINGS_PATH = '/api/v1/me/ui-settings'

export interface DesignerDockSizesInput {
  leftWidth: number
  rightWidth: number
  bottomHeight: number
}

/** What the server answered, as it came: the Designer decides what a usable size is. */
export interface StoredDesignerDockSizes {
  leftWidth: unknown
  rightWidth: unknown
  bottomHeight: unknown
}

/** The saved sizes, or null when they could not be read. */
export async function readDesignerDockSizes(): Promise<StoredDesignerDockSizes | null> {
  try {
    const body = (await getRequest<{ settings?: Record<string, unknown> }>(UI_SETTINGS_PATH))?.data
    const settings = body && typeof body === 'object' ? body.settings : null
    if (!settings || typeof settings !== 'object') return null
    return {
      leftWidth: settings.designer_left_width,
      rightWidth: settings.designer_right_width,
      bottomHeight: settings.designer_bottom_height,
    }
  } catch {
    return null
  }
}

/** Saves all three sizes; false when the save did not go through. */
export async function saveDesignerDockSizes(sizes: DesignerDockSizesInput): Promise<boolean> {
  try {
    await patchRequest(UI_SETTINGS_PATH, {
      designer_left_width: sizes.leftWidth,
      designer_right_width: sizes.rightWidth,
      designer_bottom_height: sizes.bottomHeight,
    })
    return true
  } catch {
    return false
  }
}
