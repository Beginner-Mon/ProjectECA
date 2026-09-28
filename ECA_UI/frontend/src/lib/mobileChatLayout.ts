export const CHAT_DRAG_THRESHOLD = 6
export const CHAT_RAIL_GAP = 8

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
