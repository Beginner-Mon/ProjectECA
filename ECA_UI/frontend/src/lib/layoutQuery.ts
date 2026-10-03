/**
 * When the app uses the phone layout (MobileChatLayout: bottom chat dock +
 * right rail) instead of the floating desktop nav bar and panels.
 *
 * Narrow screens, AND short touch screens: a phone held sideways is 800–930 px
 * wide but only ~390 px tall, and went to the desktop layout when this was
 * width-only — hover-style panels running off the bottom of the screen (audit
 * 30-09). `pointer: coarse` keeps short desktop windows (mouse, trackpad) on
 * the desktop layout.
 *
 * Mirrored by the `desktop:` Tailwind variant in index.css, which is exactly
 * its negation. Change both together.
 */
export const MOBILE_LAYOUT_QUERY = '(max-width: 767px), (max-height: 500px) and (pointer: coarse)'
