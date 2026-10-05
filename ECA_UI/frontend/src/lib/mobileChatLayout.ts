export const CHAT_DRAG_THRESHOLD = 6
export const CHAT_RAIL_GAP = 8
/** The rail's distance from the right edge (MobileNavBar `right-2`). */
export const RAIL_RIGHT = 8
/** The conversation card's side margin inside the dock (MobileChatDock `mx-3`). */
export const DOCK_MARGIN = 12

/**
 * Phone held sideways: the rail sits BESIDE the dock instead of above it.
 * Stacked, the rail's ~190 px left ~40 px for messages on a 375 px tall
 * screen (audit 30-09). Layout viewport, not visual: an on-screen keyboard
 * must not flip a portrait phone into the landscape arrangement.
 */
export function isLandscape(layoutWidth: number, layoutHeight: number) {
  return layoutWidth > layoutHeight
}

/** How far the dock's right edge must stay from the screen edge so the rail
 *  fits beside it: rail offset + rail + gap, minus the card margin already there. */
export function dockRightInset(railWidth: number) {
  return Math.max(0, RAIL_RIGHT + railWidth + CHAT_RAIL_GAP - DOCK_MARGIN)
}

export function clampChatHeight(height: number, maximum: number) {
  return Math.max(0, Math.min(height, Math.max(0, maximum)))
}

export function chatHeightLimit(layoutHeight: number, visibleHeight: number, baseHeight: number, railHeight: number, topInset = 0) {
  return Math.max(0, Math.min(layoutHeight * 0.35, visibleHeight - baseHeight - railHeight - topInset - CHAT_RAIL_GAP * 2))
}

/** All coordinates are in the layout viewport, including visualViewport.offsetTop. */
export function chatRailTop(viewportTop: number, viewportHeight: number, railHeight: number, dockTop: number | null, topInset = 0) {
  const centered = viewportTop + (viewportHeight - railHeight) / 2
  const aboveChat = dockTop === null ? centered : dockTop - CHAT_RAIL_GAP - railHeight
  return Math.max(viewportTop + topInset + CHAT_RAIL_GAP, Math.min(centered, aboveChat))
}

/** Once a drag starts, returning near its origin must not turn it back into a tap. */
export function moveChatDrag(startHeight: number, startY: number, currentY: number, maximum: number, wasDragging: boolean) {
  const delta = startY - currentY
  const dragging = wasDragging || Math.abs(delta) >= CHAT_DRAG_THRESHOLD
  return { dragging, height: clampChatHeight(dragging ? startHeight + delta : startHeight, maximum) }
}
