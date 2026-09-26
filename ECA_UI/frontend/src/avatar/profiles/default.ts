import type { AvatarProfile } from '../AvatarProfile'

/**
 * Default profile for VRM 0.x models whose presets migrate cleanly to the
 * three-vrm v3 standard 1.0 names (14-17 blendshape groups: neutral,
 * a/i/u/e/o, blink, joy, angry, sorrow, fun, look*, blink_l/r).
 *
 * `surprised` is intentionally mapped to the `surprised` preset even though a
 * plain VRM 0.x model has no such preset — VRMExpressionAdapter's capability
 * detection makes it a safe no-op where the channel is absent. Models that DO
 * have it (as a custom expression) provide a per-model override — see bronya.ts.
 */
export const defaultProfile: AvatarProfile = {
  version: 1,
  modelId: 'default',
  recipes: {
    neutral: {},
    happy: { happy: 1.0 },
    sad: { sad: 1.0 },
    angry: { angry: 1.0 },
    relaxed: { relaxed: 1.0 },
    surprised: { surprised: 1.0 },
  },
  visemes: { A: 'aa', I: 'ih', U: 'ou', E: 'ee', O: 'oh' },
  blinkChannel: 'blink',
  greetingEmotion: 'happy',
  // Bundled gestures every model gets. A character that brings its own set from
  // the database overrides this; one that brings none keeps it. These are ADDED
  // to the built-in FSM states (idle / greeting / bored / thinking / exercise),
  // which are untouched.
  gestures: {
    kiss: {
      source: { builtIn: 'kiss' },
      blendSec: 0.5,
      // Timed to Kiss.fbx (3.73 s): the hand is closest to the face at ~1.9 s
      // (measured, worklog 19-09). Smile → lips purse (U) and eyes close as the
      // hand arrives → smile after the blow. `happy` is 0 while the eyes are
      // closed: some models' happy expression blocks blink / mouth overrides.
      // Uses `blink`, not a wink: Bronya has no working blinkLeft/blinkRight.
      face: [
        { t: 0.0, face: {} },
        { t: 0.5, face: { happy: 0.4 } },
        { t: 1.1, face: { happy: 0.15, U: 0.5 } },
        { t: 1.5, face: { U: 0.9, blink: 0.9 } },
        { t: 2.2, face: { U: 0.9, blink: 0.9 } },
        { t: 2.6, face: { happy: 0.6, U: 0.1 } },
        { t: 3.4, face: { happy: 0.4 } },
        { t: 3.73, face: {} },
      ],
      // Close-up for the second half — the kiss at the lips (~1.5–2.25 s) and
      // the blow (~2.5–2.75 s). Simulated on the clip (worklog 26-09): with the
      // 30 % face follow the camera reaches ~30 cm from the nose at the kiss and
      // ~32–35 cm in the blow; nearest joint (a thumb tip) stays 6 cm outside
      // the 10 cm near plane; no backstop push needed.
      cameraZoom: [
        { t: 0.0, scale: 1 },
        { t: 1.4, scale: 1 },
        { t: 1.9, scale: 0.7 },
        { t: 2.8, scale: 0.7 },
        { t: 3.4, scale: 1 },
      ],
      // The viewer IS the kiss partner. Kiss.fbx: the left palm rises from
      // ~0.75 s and rests on the partner's head 1.4–2.4 s (within 3 cm), then
      // leaves for the blow. Move in as the hand arrives, hold while it rests,
      // move back out after — so the hand goes around the back of the viewer's
      // head instead of across the lens.
      partnerView: {
        hand: 'left',
        keys: [
          { t: 0.0, weight: 0 },
          { t: 0.8, weight: 0 },
          { t: 1.45, weight: 1 },
          { t: 2.45, weight: 1 },
          { t: 3.2, weight: 0 },
        ],
      },
    },
  },
  reactions: {
    // Animation and emotion together in one binding — the click plays the clip
    // and lifts the expression, from a single per-model record.
    'bodyPartClick:mouth': { gesture: 'kiss', emotion: { name: 'happy', durationMs: 600 } },
  },
}
