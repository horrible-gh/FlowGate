// flowgate.v02.0060 — menu placement (0006-D rev2 §6.2): keep the whole menu inside the viewport and
// open a submenu on the right of its parent, or on the left when the right side is short.

import type { Point } from './types'
interface Size { width: number; height: number }
/** The mockup uses an 8px viewport margin and a 4px submenu gap. */
export function menuPosition(point: Point, size: Size, viewport: Size): Point {
  const axis = (value: number, length: number, limit: number) => {
    const margin = Math.min(8, Math.max(0, (limit - length) / 2))
    return Math.max(margin, Math.min(value, limit - length - margin))
  }
  return { x: axis(point.x, size.width, viewport.width), y: axis(point.y, size.height, viewport.height) }
}
export function submenuPosition(parent: { left: number; right: number; top: number }, size: Size, viewport: Size): Point {
  return menuPosition({
    x: parent.right + size.width + 8 > viewport.width ? parent.left - size.width - 4 : parent.right + 4,
    y: parent.top,
  }, size, viewport)
}

