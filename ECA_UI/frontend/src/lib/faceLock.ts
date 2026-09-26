/**
 * Face-lock camera helpers (CameraMode 'face', used by gestures like the kiss).
 *
 * Measured on Kiss.fbx (worklog 26-09): during the kiss the head moves 26 cm
 * FORWARD and 8 cm UP, and the heel lifts 9 cm (the "up-heel"). A camera held
 * still in front of the face therefore got within 17 cm of a joint — and the
 * mesh reaches ~10 cm past its joints — so the lens went through the avatar.
 * The fix is to move WITH the face (see CharacterViewer) and keep a clearance
 * backstop; aiming at the nose rather than the head bone fixes the framing,
 * which sat at the chin/mouth.
 */

import * as THREE from 'three'

/** Nose tip below the eyes' centre, metres (Z-up). */
export const NOSE_BELOW_EYES = 0.03
/** Nose above the head bone when a model has no eye bones, metres. The VRM /
 *  Mixamo head bone sits at the base of the skull, around jaw height. */
export const HEAD_TO_NOSE = 0.07

/** The point the locked camera aims at: the nose. Writes into and returns `out`. */
export function noseFocus(
  head: THREE.Vector3,
  leftEye: THREE.Vector3 | null,
  rightEye: THREE.Vector3 | null,
  out: THREE.Vector3,
): THREE.Vector3 {
  if (leftEye && rightEye) {
    out.addVectors(leftEye, rightEye).multiplyScalar(0.5)
    out.z -= NOSE_BELOW_EYES
    return out
  }
  out.copy(head)
  out.z += HEAD_TO_NOSE
  return out
}

const MAX_BACKOFF = 1 // metres — a joint parked on the view axis cannot push further
const scratch = new THREE.Vector3()

/**
 * Extra distance to add along `dir` so a camera at `anchor + dir * baseDist`
 * keeps every joint at least its clearance away. 0 when already clear.
 * `clearance` is one value for all joints, or one per joint (same order):
 * fingertip joints sit at the mesh surface and can come much closer than the
 * head or torso, whose mesh extends well past the joint. `dir` must be normalised.
 */
export function clearanceBackoff(
  anchor: THREE.Vector3,
  dir: THREE.Vector3,
  baseDist: number,
  joints: readonly THREE.Vector3[],
  clearance: number | readonly number[],
): number {
  let extra = 0
  for (let step = 0; step < 10; step++) {
    scratch.copy(anchor).addScaledVector(dir, baseDist + extra)
    let shortfall = 0
    for (let i = 0; i < joints.length; i++) {
      const c = typeof clearance === 'number' ? clearance : clearance[i]
      shortfall = Math.max(shortfall, c - scratch.distanceTo(joints[i]))
    }
    if (shortfall <= 0) return extra
    extra = Math.min(MAX_BACKOFF, extra + shortfall + 0.005)
    if (extra >= MAX_BACKOFF) return MAX_BACKOFF
  }
  return extra
}

const dirNose = new THREE.Vector3()
const dirAim = new THREE.Vector3()
const axis = new THREE.Vector3()

/**
 * Let the aim lean toward the kissing hand, but never so far that the nose
 * leaves the frame: the view direction is rotated from "at the nose" toward
 * "at the aim" by at most `maxAngle` radians. Writes into and returns `out`.
 * (The camera's vertical half-FOV is 22.5°; ~15° keeps the nose well inside.)
 */
export function limitAimOffset(
  camera: THREE.Vector3,
  nose: THREE.Vector3,
  aim: THREE.Vector3,
  maxAngle: number,
  out: THREE.Vector3,
): THREE.Vector3 {
  dirNose.subVectors(nose, camera)
  const noseDist = dirNose.length()
  dirAim.subVectors(aim, camera)
  if (noseDist < 1e-6 || dirAim.lengthSq() < 1e-12) return out.copy(aim)
  dirNose.divideScalar(noseDist)
  dirAim.normalize()
  const angle = dirNose.angleTo(dirAim)
  if (angle <= maxAngle) return out.copy(aim)
  axis.crossVectors(dirNose, dirAim)
  if (axis.lengthSq() < 1e-12) return out.copy(nose) // straight behind: stay on the nose
  axis.normalize()
  return out.copy(dirNose).applyAxisAngle(axis, maxAngle).multiplyScalar(noseDist).add(camera)
}

// ── Partner view (the kiss): the camera stands where the partner's eyes are ──
//
// Kiss.fbx is a two-person kiss: from 1.4 to 2.4 s her left palm rests (within
// 3 cm) on the side/back of a partner's head that is ~26 cm from her lips. The
// partner stands straight in front of her face, NOT toward the palm: the palm
// sits 22 cm to her left, and using the nose/palm midpoint swung the camera to
// her left side (Owner, 26-09). With the camera in front at 28 cm the resting
// palm is ~57 deg off the view axis, i.e. out of frame beside the viewer's head.

/** Partner's eyes sit a little above her nose line. */
export const PARTNER_EYE_LIFT = 0.035
/** Never closer to her nose point than this. The nose point comes from the eye
 *  bones, which sit INSIDE the head, and anime VRM heads are far larger than
 *  the Mixamo rig's: at 0.12 the camera ended up inside her head (Owner, 26-09).
 *  0.28 keeps a close-up without entering the skull. */
export const PARTNER_MIN_DISTANCE = 0.28

/**
 * Where the partner's eyes are: straight out from her nose along `front` (the
 * face lock's nose->camera direction, normalised), PARTNER_MIN_DISTANCE away,
 * lifted a little. Writes into and returns `out`.
 */
export function partnerEyes(nose: THREE.Vector3, front: THREE.Vector3, out: THREE.Vector3): THREE.Vector3 {
  out.copy(nose).addScaledVector(front, PARTNER_MIN_DISTANCE)
  out.z += PARTNER_EYE_LIFT
  return out
}

/** Move `point` straight away from `from` until it is at least `minDist` from it. */
export function keepAway(point: THREE.Vector3, from: THREE.Vector3, minDist: number): THREE.Vector3 {
  scratch.subVectors(point, from)
  const d = scratch.length()
  if (d >= minDist) return point
  if (d < 1e-9) scratch.set(0, 1, 0) // degenerate: pick "in front" (+Y faces the viewer)
  else scratch.divideScalar(d)
  return point.copy(from).addScaledVector(scratch, minDist)
}
