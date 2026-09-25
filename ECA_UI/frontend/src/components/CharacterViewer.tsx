/* eslint-disable react-hooks/refs, react-hooks/immutability */
import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMHumanBoneName } from '@pixiv/three-vrm'
import type { VRM } from '@pixiv/three-vrm'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../hooks/useTheme'
import { OrbitControls, Html } from '@react-three/drei'
import { useRef, useEffect, useState, Suspense, useMemo } from 'react'
import * as THREE from 'three'
import { AnimationController } from '../lib/AnimationController'
import { AnimationRegistry } from '../lib/AnimationRegistry'
import { DEFAULT_GROUND_CLAMP, GroundClamp } from '../lib/groundClamp'
import { RootMotionAccumulator } from '../lib/rootMotionAccumulator'
import type { CameraResponsivePreset } from '../lib/CameraConfig'
import type { CameraMode } from '../lib/AnimationStates'
import { cameraModeOf, loopModeOf } from '../lib/AnimationStates'
import { useFsmBoot } from '../hooks/useFsmTriggers'
import { useMotion } from '../hooks/useMotion'
import { AvatarController } from '../avatar/AvatarController'
import { BodyPartPicker } from '../avatar/BodyPartPicker'
import { bodyPartClick } from '../avatar/userActivity'
import { loadProfileAsync } from '../avatar/AvatarProfile'
import LoadingOverlay from './ui/LoadingOverlay'
import { disposeVRM } from '../lib/vrmDispose'
import { ENV_CONFIG } from '../config/environmentConfig'
import RendererSetup from './scene/RendererSetup'
import SceneLighting from './scene/SceneLighting'
import SceneEnvironment from './scene/SceneEnvironment'
import ScenePostProcessing from './scene/ScenePostProcessing'
import ClickRipple from './scene/ClickRipple'
import ThinkingBubble from './scene/ThinkingBubble'
import { GraphicsProvider } from '../contexts/GraphicsContext'
import { useGraphics } from '../hooks/useGraphics'

/**
 * Where the model group is authored. The "Reset position" button returns the
 * character here; RootMotionAccumulator captures the same value as its base.
 */
const MODEL_HOME: [number, number, number] = [0, 1.5, 0]

/** Keeps the debug axis labels under the chat/sidebar (see ThinkingBubble). */
const AXIS_LABEL_Z: [number, number] = [100, 0]

interface CameraModeDef {
  /** Bone the camera parks in front of. Also the look-at anchor. */
  boneName: VRMHumanBoneName
  /**
   * Optional second bone the look-at target rides toward, weighted by
   * `trackWeight` (0 = anchor only, 1 = track bone only). The camera's
   * POSITION never follows this bone — only where it looks.
   */
  trackBone?: VRMHumanBoneName
  trackWeight?: number
  /**
   * Look-at only. The entry transition parks the camera in front of
   * `boneName`; after that the position is held and only the target moves.
   * Without this, the follow loop translates the camera with the target,
   * so a hand coming toward the lens would push the camera away from it.
   */
  holdPosition?: boolean
}

const CAMERA_MODES: Record<CameraMode, CameraModeDef> = {
  head: { boneName: VRMHumanBoneName.Head },
  hips: { boneName: VRMHumanBoneName.Head },
  // Kiss.fbx (the only gesture today) is LEFT-handed: measured through the
  // clip, the left hand closes from 76 cm to 31 cm of the head while the
  // right barely moves. 0.4 keeps the face in frame while the hand leads.
  face: {
    boneName: VRMHumanBoneName.Head,
    trackBone: VRMHumanBoneName.LeftHand,
    trackWeight: 0.4,
    holdPosition: true,
  },
  manual: { boneName: VRMHumanBoneName.Head },
}

/**
 * Orbit min-distance while the camera is locked to the face. The user's
 * configured minimum (1 m by default) is what keeps a free orbit from clipping
 * into the model; the locked close-up sits inside it on purpose, and controls
 * are disabled for the duration so nothing can orbit through the mesh. Low
 * enough that the blended look-at point can come toward the lens (the blown
 * kiss) without OrbitControls' radius clamp shoving the camera back.
 */
const FACE_LOCK_MIN_DISTANCE = 0.15

/**
 * Per-second rate at which the held camera's look-at target closes on the
 * tracked point: 1 - e^(-8 * dt) is ~12 % per frame at 60 fps, settling in
 * about half a second. Raw bone-following jitters on the fast part of a
 * gesture; this is the same damping the follow loop uses, made frame-rate
 * independent.
 */
const TRACK_DAMPING = 8

/**
 * Seconds to ease the camera back out of the `face` lock. Slower than the
 * 0.6 s mode switch on purpose: the kiss ends on a close-up, and a quick pull
 * back to 1 m reads as a cut rather than a camera move.
 */
const FACE_RELEASE_SEC = 1.2

/** Smooth start AND smooth stop — a release that only eases out starts with a jolt. */
const easeInOutCubic = (p: number) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2)

/** Responsive presets per camera mode. wideFraming = current desktop-tuned offsets;
 *  narrowFraming = mobile-portrait offsets (increase Y to push back, Z stays at eye level).
 *  narrowTargetZ shifts the look-at target DOWN so the model appears higher, compensating
 *  for the chat panel that occupies the bottom ~40% on mobile. */
const CAMERA_RESPONSIVE_PRESETS: Record<CameraMode, CameraResponsivePreset> = {
  head: {
    wideFraming: [0, 0.5, 0],
    narrowFraming: [0, 2.0, 0],
    narrowTargetZ: -0.6,
  },
  hips: {
    wideFraming: [1.5, 3.8, 1.5],
    narrowFraming: [1.5, 4.5, 1.5],
    narrowTargetZ: -0.4,
  },
  face: {
    // Straight in front of the head bone, tighter than `head` and lifted a
    // little so the eyes/mouth sit in the middle of the frame. Tune here.
    wideFraming: [0, 0.45, 0.04],
    narrowFraming: [0, 0.9, 0.04],
    narrowTargetZ: -0.3,
  },
  manual: {
    // Not used while manual (follow is disabled), but keep a valid entry
    wideFraming: [0, 0.5, 0],
    narrowFraming: [0, 2.0, 0],
    narrowTargetZ: -0.6,
  },
}

/**
 * Static <Canvas> configuration, hoisted OUT of the render.
 *
 * R3F calls `root.configure(props)` on every render of <Canvas> and writes
 * these onto the live renderer. Passing fresh object literals meant that work
 * repeated on every React re-render — and re-renders got more frequent once FSM
 * state started flowing through context. Module constants make the config what
 * it actually is: fixed for the lifetime of the app.
 */
const CANVAS_SHADOWS = { type: ENV_CONFIG.shadows.type }
const CANVAS_CAMERA = { position: [0, 2.05, 0] as [number, number, number], fov: 45 }
const CANVAS_GL = {
  antialias: true,
  alpha: true,
  toneMapping: ENV_CONFIG.renderer.toneMapping,
  toneMappingExposure: ENV_CONFIG.renderer.toneMappingExposure,
}

/* ───────────────────── VRM Character with FSM-driven animation ──────────── */

interface VRMCharacterProps {
  vrmUrl: string
  modelId: string
  /** Readiness gate: false while the model has no pose yet (bind pose hidden). */
  onReady: (ready: boolean) => void
  vrmRef: React.MutableRefObject<VRM | null>
  avatarRef: React.MutableRefObject<AvatarController | null>
  /**
   * Called after "Reset position" moves the character, with the world-space
   * jump, so the camera can make the same jump. Needed because camera follow
   * is off by design (CameraConfig.followTarget): without it the character
   * would leave the frame.
   */
  onTeleport?: (delta: THREE.Vector3) => void
}

function VRMCharacter({ vrmUrl, modelId, onReady, vrmRef, avatarRef, onTeleport }: VRMCharacterProps) {
  const { attachControllers, setClipInfo, prefetchGestures, registerPositionReset } = useMotion()
  const onTeleportRef = useRef(onTeleport)
  useEffect(() => {
    onTeleportRef.current = onTeleport
  }, [onTeleport])

  const gltf = useLoader(GLTFLoader, vrmUrl, (loader) => {
    loader.register((parser) => new VRMLoaderPlugin(parser))
  })

  const vrm: VRM = gltf.userData.vrm

  // Cache rest poses immediately upon load, before any animations mutate the bones.
  // The retargeter (BVH/Mixamo) needs these pure bind poses to calculate offsets.
  if (!vrm.scene.userData.restPoses) {
    const restPoses = new Map<string, { position: THREE.Vector3; quaternion: THREE.Quaternion }>()
    if (vrm.humanoid) {
      Object.values(VRMHumanBoneName).forEach((boneName) => {
        const bone = vrm.humanoid?.getNormalizedBoneNode(boneName as VRMHumanBoneName)
        if (bone) {
          restPoses.set(boneName, {
            position: bone.position.clone(),
            quaternion: bone.quaternion.clone(),
          })
        }
      })
    }
    vrm.scene.userData.restPoses = restPoses
  }

  // Expose the VRM instance so the parent can read bone positions.
  useEffect(() => {
    vrmRef.current = vrm
    return () => {
      vrmRef.current = null
    }
  }, [vrm, vrmRef])

  // Dispose GPU resources (geometry/material/texture) when the VRM truly
  // unmounts (model switch). React 19 StrictMode double-fires effects in dev
  // (mount → cleanup → remount): a naive cleanup would destroy the
  // useLoader-cached VRM on the first cycle, making the model invisible on
  // remount. The `mountedRef` flag lets us distinguish: on cleanup we set it
  // false, then on the synchronous remount we set it true again — the
  // microtask only fires dispose if the ref is still false (real unmount).
  const disposeGuardRef = useRef(true)
  useEffect(() => {
    disposeGuardRef.current = true
    return () => {
      disposeGuardRef.current = false
      const vrmToDispose = vrm
      queueMicrotask(() => {
        if (!disposeGuardRef.current) {
          disposeVRM(vrmToDispose)
        }
      })
    }
  }, [vrm])

  const avatarControllerRef = useRef<AvatarController | null>(null)
  const animControllerRef = useRef<AnimationController | null>(null)
  const modelGroupRef = useRef<THREE.Group>(null)
  const groundClampRef = useRef<GroundClamp | null>(null)
  const rootMotionRef = useRef<RootMotionAccumulator | null>(null)
  const posedRef = useRef(false)
  const emotionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // "Reset position": undo the travel motions leave behind. Both accumulators
  // are cleared: the inertializer's (default blend) and RootMotionAccumulator's
  // (crossfade). Called from a click, i.e. between frames, so the next frame
  // renders the character at home.
  useEffect(() => registerPositionReset(() => {
    const group = modelGroupRef.current
    if (!group) return
    // The group sits directly under the scene root, so its position delta IS
    // the world-space jump the camera has to match.
    const before = group.position.clone()
    animControllerRef.current?.resetRootMotion()
    rootMotionRef.current?.reset()
    group.position.x = MODEL_HOME[0]
    group.position.y = MODEL_HOME[1]
    // Hair and skirt: the spring bones integrate in world space, so a jump of
    // metres would read as a violent whip. Put them at rest instead. reset()
    // reads each joint's parent world matrix, so refresh the subtree first.
    group.updateMatrixWorld(true)
    vrm.springBoneManager?.reset()
    onTeleportRef.current?.(group.position.clone().sub(before))
  }), [vrm, registerPositionReset])
  // Per-instance state — key={vrmUrl} remounts resets these on model switch.
  // `posed`: the first animation pose has reached the bones.
  const [posed, setPosed] = useState(false)
  // `avatarAttached`: AvatarController exists, so eye/head follow is driving.
  const [avatarAttached, setAvatarAttached] = useState(false)

  /**
   * Reveal needs BOTH conditions, not just a pose.
   *
   * AvatarController is built behind `await loadProfileAsync(...)`, a network
   * request, while the greeting clip starts as soon as it is retargeted. Showing
   * the model on the pose alone exposed that gap: for its duration nothing holds
   * Neck/Head, so the greeting clip's own head track shows through — the head
   * leans down — and then snaps straight the moment HeadController attaches.
   * A warm HTTP cache shrinks the gap to nothing, which is why a second refresh
   * "fixed" it and why HMR (VRM served instantly from the useLoader cache, profile
   * still refetched) made it easiest to reproduce. Waiting costs one small request
   * that already falls back rather than failing, behind a spinner that is up
   * anyway for the 10-18 MB model download.
   */
  const revealed = posed && avatarAttached
  // Also kept in state so the boot effect re-runs when the controller is swapped.
  const [animController, setAnimController] = useState<AnimationController | null>(null)
  const [registry, setRegistry] = useState<AnimationRegistry | null>(null)
  // Reused by the ground clamp so it allocates nothing per frame.
  const groundScratch = useMemo(() => new THREE.Vector3(), [])

  // Latest-value refs: the animation controller lives outside React, so its
  // callbacks must not capture a stale render's props.
  const onReadyRef = useRef(onReady)
  const setClipInfoRef = useRef(setClipInfo)
  useEffect(() => {
    onReadyRef.current = onReady
    setClipInfoRef.current = setClipInfo
  }, [onReady, setClipInfo])

  // Single source of truth for the loading overlay: it hides exactly when the
  // model becomes visible. Previously readiness was pushed from three different
  // places (clip callback, effect body, effect cleanup), which is how it could
  // drift from what <primitive visible> was actually doing.
  useEffect(() => {
    onReadyRef.current(revealed)
  }, [revealed])

  // Facial-animation controller lifecycle: attach on VRM load, detach on
  // model change / unmount. Kept out of React state — this ref IS the handle
  // (facial-animation-plan.md §8 rules 2-4).
  // The profile now comes from the character record, so this awaits before
  // attaching. Cheap in context: the VRM this runs after is a 10-18 MB
  // download, and loadProfileAsync falls back to the bundled registry rather
  // than failing, so the wait is bounded by one small request either way.
  useEffect(() => {
    if (!vrm) return
    const abort = new AbortController()
    let controller: AvatarController | null = null
    let prefetchIdle: { cancel: () => void } | null = null

    void (async () => {
      let profile
      try {
        profile = await loadProfileAsync(modelId, abort.signal)
      } catch (err) {
        // loadProfileAsync rethrows ONLY AbortError — every other failure falls
        // back to the bundled registry (AvatarProfile.ts:131-136). That used to
        // be a detail; now that reveal waits on this effect it is load-bearing,
        // so an unexpected throw must release the gate rather than strand the
        // model behind the loading overlay forever.
        if (!abort.signal.aborted) {
          console.error('[avatar] profile load failed — revealing without facial controller', err)
          setAvatarAttached(true)
        }
        return
      }
      if (abort.signal.aborted) return

      controller = new AvatarController(vrm, profile)
      avatarControllerRef.current = controller
      avatarRef.current = controller
      setAvatarAttached(true)
      // The profile is what declares this character's gestures, so this is the
      // first moment the set is known. Warming them on idle keeps the first
      // click off the fetch-and-retarget path (111-214 ms, measured in
      // AnimationRegistry) — the whole reason gestures are declared rather than
      // named ad-hoc at the moment of the click.
      prefetchIdle = scheduleIdle(prefetchGestures)
    })()

    return () => {
      abort.abort()
      prefetchIdle?.cancel()
      setAvatarAttached(false)
      if (controller) {
        controller.detach()
        if (avatarRef.current === controller) avatarRef.current = null
        avatarControllerRef.current = null
      }
    }
  }, [vrm, modelId, avatarRef, prefetchGestures])

  // Animation FSM lifecycle. The registry is per-VRM because clips are
  // retargeted against a specific skeleton (plan lỗi #6); a new instance per
  // model IS the invalidation.
  useEffect(() => {
    if (!vrm) return

    const registry = new AnimationRegistry(vrm, vrmUrl)
    const controller = new AnimationController(vrm, registry, {
      onClipApplied: (info) => {
        setClipInfoRef.current({ tracks: info.tracks, duration: info.duration })
        // Record that a real pose has reached the bones. Event-driven — never a
        // timed wait. Reveal itself is derived from this plus the avatar attach.
        if (!posedRef.current) {
          posedRef.current = true
          setPosed(true)
        }
        // Schedule emotion at midpoint of greeting clip (plan greeting-midpoint-emotion).
        if (info.state === 'greeting') {
          if (emotionTimerRef.current) clearTimeout(emotionTimerRef.current)
          const delayMs = info.duration * 0.25 * 1000
          emotionTimerRef.current = setTimeout(() => {
            const emotion = avatarControllerRef.current?.profile.greetingEmotion ?? 'happy'
            avatarControllerRef.current?.setEmotion(emotion, 1, 600)
          }, delayMs)
        }
        // Begin root-motion tracking for one-shots that use a wide camera
        // (currently only `exercise`). For crossfade, rootMotion handles the
        // group offset; for inertial, PoseInertializer handles it via 1 - x/x0.
        if (loopModeOf(info.state) === 'once' && cameraModeOf(info.state) === 'hips') {
          const isInertial = animControllerRef.current?.blendMode !== 'crossfade'
          if (!isInertial) {
            rootMotionRef.current?.beginOneShot(vrm)
          }
        }
      },
      onBeforeAutoTransition: (_completed, _next, blendSec) => {
        // Commit the hips displacement BEFORE the successor clip (idle) resets
        // hips to rest position. For crossfade, ramp group offset over blendSec.
        // For inertial, skip — PoseInertializer drives group via 1 - x/x0.
        const isInertial = animControllerRef.current?.blendMode !== 'crossfade'
        if (!isInertial) {
          rootMotionRef.current?.commitOneShot(vrm, blendSec)
        }
      },
    })

    animControllerRef.current = controller
    setAnimController(controller)
    setRegistry(registry)
    const detach = attachControllers(controller, registry)

    return () => {
      if (emotionTimerRef.current) clearTimeout(emotionTimerRef.current)
      detach()
      controller.dispose()
      animControllerRef.current = null
      setAnimController(null)
      setRegistry(null)
      posedRef.current = false
      setPosed(false)
    }
  }, [vrm, vrmUrl, attachControllers])

  // Boot: greet once, then idle (plan §2.5).
  useFsmBoot(animController, registry)

  // Update body animation, then facial expressions, then the VRM itself.
  // Order is mandatory (§8 rule 1): the avatar controller calls setValue, and
  // vrm.update applies those weights via expressionManager.update() — so the
  // tick must land BETWEEN the mixer update and vrm.update.
  // Inertializer must write to normalized bone nodes BEFORE vrm.update(),
  // and group offset via inertializer before groundClamp.
  useFrame((_state, delta) => {
    // Provide group target to inertializer once (lazy, after group mounts)
    if (modelGroupRef.current && animControllerRef.current) {
      animControllerRef.current.setGroupTarget(modelGroupRef.current)
    }
    animControllerRef.current?.update(delta)
    avatarControllerRef.current?.tick(delta)
    // The clamp reads the pose this frame actually produced — which the mixer
    // and inertializer have already written to the normalized bones, so it
    // does not need vrm.update(). A generated clip can descend further than the
    // character is tall (measured: 1.21 m on motion_b28e8284), which would
    // otherwise sink it through the floor and kill its shadow — see
    // lib/groundClamp.ts.
    //
    // Every write to the model group's transform MUST land BEFORE vrm.update():
    // that call steps the spring-bone physics (hair, skirt), which integrates in
    // WORLD space. A group move made after it is seen one frame late, as a jump
    // the body never made. At the end of an exercise the clamp's lift falls as
    // the pose stands back up — body stationary in the world — and the old
    // order (physics, then clamp) whipped the hair ~10.6° on the first frame in
    // a spring-joint simulation; this order gives 0°. See worklog 24-09-2026.
    if (modelGroupRef.current) {
      if (!groundClampRef.current) {
        groundClampRef.current = new GroundClamp(modelGroupRef.current, groundScratch, {
          ...DEFAULT_GROUND_CLAMP,
          groundZ: ENV_CONFIG.shadows.fitGroundZ,
        })
      }
      if (!rootMotionRef.current) {
        rootMotionRef.current = new RootMotionAccumulator(modelGroupRef.current)
      }
      rootMotionRef.current.update(delta)
      groundClampRef.current.update(vrm)
    }
    vrm?.update(delta)
  })

  return (
    <group ref={modelGroupRef} position={MODEL_HOME} rotation={[Math.PI / 2, 0, 0]}>
      {/* visible=false until the first animation pose is applied — the model
          never renders in bind pose (T-pose). */}
      <primitive object={vrm.scene} visible={revealed} />
    </group>
  )
}

/* ───────────────────────── Floating Particles ────────────────────── */

function FloatingParticles() {
  const { particles } = ENV_CONFIG
  const count = particles.count
  const pointsRef = useRef<THREE.Points>(null!)
  const { settings } = useGraphics()

  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    let seed = 1337
    const rand = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0
      return seed / 4294967296
    }
    for (let i = 0; i < count; i++) {
      arr[i * 3 + 0] = (rand() - 0.5) * 10
      arr[i * 3 + 1] = (rand() - 0.5) * 10
      arr[i * 3 + 2] = (rand() - 0.5) * 10
    }
    return arr
  }, [count])

  useFrame(({ clock }) => {
    if (!settings.particles || !pointsRef.current) return
    const t = clock.getElapsedTime()
    pointsRef.current.rotation.y = t * 0.02
    pointsRef.current.rotation.x = t * 0.01
  })

  if (!settings.particles) return null

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          args={[positions, 3]}
        />
      </bufferGeometry>
      <pointsMaterial
        size={particles.size}
        color={particles.color}
        transparent
        opacity={particles.opacity}
        sizeAttenuation
      />
    </points>
  )
}

/* ────────────────────────────── Scene ─────────────────────────────── */

interface SceneProps {
  theme: 'light' | 'dark'
  vrmUrl: string
  modelId: string
  onReady: (ready: boolean) => void
  avatarRef: React.MutableRefObject<AvatarController | null>
}

function Scene({ theme, vrmUrl, modelId, onReady, avatarRef }: SceneProps) {
  // Camera mode is owned by CameraController and driven by FSM state
  // (exercise → wide + 3s cooldown), plus manual override when user drags.
  const { cameraMode, cameraConfig, notifyManualInteraction } = useMotion()
  const { settings: gfx } = useGraphics()
  // `face` is an FSM-granted lock (see CameraMode): no orbit, no zoom, no pan
  // until the state that asked for it ends.
  const cameraLocked = cameraMode === 'face'
  // True while easing OUT of the lock. Must be React state, not a ref: it
  // drives OrbitControls' props, and drei calls controls.update() in its own
  // useFrame (priority -1, before ours) whenever `enabled` is true. Handing
  // the user's settings back the instant the lock ended let that update clamp
  // the camera from the ~0.45 m close-up out to minDistance (1 m) in ONE
  // frame: the snap.
  const [releasing, setReleasing] = useState(false)
  const releasingRef = useRef(false)
  const cameraHeld = cameraLocked || releasing
  /**
   * The user's view when the lock began, relative to the head bone, so a
   * manual camera is handed back where it was, not left on the close-up.
   */
  const preLockViewRef = useRef<{ pos: THREE.Vector3; target: THREE.Vector3 } | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- drei OrbitControls ref is an untyped Three.js controls instance
  const controlsRef = useRef<any>(null)
  const vrmRef = useRef<VRM | null>(null)
  const { camera, size } = useThree()
  const cameraInitializedRef = useRef(false)
  const cameraTransitionRef = useRef<{
    startPos: THREE.Vector3
    startTarget: THREE.Vector3
    elapsed: number
    duration: number
    /** Leaving the `face` lock: slower, eased at both ends, clears `releasing`. */
    release: boolean
    /** Explicit destination relative to the anchor bone (manual hand-back). */
    endOffset: { pos: THREE.Vector3; target: THREE.Vector3 } | null
  } | null>(null)
  const prevCameraModeRef = useRef(cameraMode)

  const responsiveTargetRef = useRef(new THREE.Vector3(0, 0.5, 0))
  const responsiveDisplayRef = useRef(new THREE.Vector3(0, 0.5, 0))

  const TRANSITION_DURATION = 0.6
  // Manual: rotate never counts, only zoom/pan far enough counts.
  const MANUAL_THRESHOLD_PAN = 0.3
  const MANUAL_THRESHOLD_ZOOM = 0.2
  const manualStartTargetRef = useRef<THREE.Vector3 | null>(null)
  const manualStartDistRef = useRef<number | null>(null)

  useEffect(() => {
    camera.up.set(0, 0, 1)
  }, [camera])

  useEffect(() => {
    cameraInitializedRef.current = false
  }, [vrmUrl])

  useEffect(() => {
    const prev = prevCameraModeRef.current
    prevCameraModeRef.current = cameraMode

    if (prev === cameraMode) return
    const isRelease = prev === 'face'
    const bone = vrmRef.current?.humanoid.getNormalizedBoneNode(CAMERA_MODES[cameraMode].boneName)
    if (!cameraInitializedRef.current || !controlsRef.current || !bone) {
      // No transition will run, so nothing would ever clear `releasing`.
      releasingRef.current = false
      setReleasing(false)
      return
    }

    const anchor = new THREE.Vector3()
    bone.getWorldPosition(anchor)

    // Entering the lock: remember the user's view. Not while still easing out
    // of a previous lock: that view is mid-flight, the saved one is the real one.
    if (cameraMode === 'face' && !releasingRef.current) {
      preLockViewRef.current = {
        pos: camera.position.clone().sub(anchor),
        target: (controlsRef.current.target as THREE.Vector3).clone().sub(anchor),
      }
    }

    cameraTransitionRef.current = {
      startPos: camera.position.clone(),
      startTarget: controlsRef.current.target.clone(),
      elapsed: 0,
      duration: isRelease ? FACE_RELEASE_SEC : TRANSITION_DURATION,
      release: isRelease,
      endOffset: isRelease && cameraMode === 'manual' ? preLockViewRef.current : null,
    }
    releasingRef.current = isRelease
    setReleasing(isRelease)
  }, [cameraMode]) // eslint-disable-line react-hooks/exhaustive-deps -- camera.position is mutable and should not trigger a transition; only cameraMode matters

  // Reusable vectors to avoid GC pressure
  const followPos = useMemo(() => new THREE.Vector3(), [])
  /** Where the camera looks: `followPos`, pulled toward `trackBone` if set. */
  const lookPos = useMemo(() => new THREE.Vector3(), [])
  const trackPos = useMemo(() => new THREE.Vector3(), [])
  const deltaVec = useMemo(() => new THREE.Vector3(), [])
  const offsetDeltaVec = useMemo(() => new THREE.Vector3(), [])
  const lastAppliedOffsetRef = useRef(new THREE.Vector3(0, 0.5, 0))
  const targetZRef = useRef(0)
  // Built once: a fresh helper per render would make R3F detach/dispose and
  // re-attach the object on every state change.
  const axesHelper = useMemo(() => new THREE.AxesHelper(3), [])

  // Lock X/Y/Z — freeze per-axis when toggled (Plan C). Stored frozen value is
  // the last unrestricted position; right-drag delta on a locked axis is reverted.
  const lockPrevTargetRef = useRef(new THREE.Vector3())
  const lockPrevPosRef = useRef(new THREE.Vector3())
  const lockInitializedRef = useRef(false)

  // "Reset position" jumped the character by `delta`: make the same jump with
  // the camera, so whatever view the user had (default or dragged) is kept.
  // Also shifts an in-flight transition's start and the Lock X/Y/Z baselines,
  // or those would pull the camera back toward the old spot.
  const handleTeleport = (delta: THREE.Vector3) => {
    camera.position.add(delta)
    const controls = controlsRef.current
    if (controls) (controls.target as THREE.Vector3).add(delta)
    const t = cameraTransitionRef.current
    if (t) {
      t.startPos.add(delta)
      t.startTarget.add(delta)
    }
    lockPrevTargetRef.current.add(delta)
    lockPrevPosRef.current.add(delta)
    controls?.update()
  }

  // Every frame: make the camera orbit target follow the selected bone.
  // We also shift the camera position by the same delta so the orbital
  // offset (angle + distance) is preserved while the rig moves.
  // In manual mode the camera is fully user-owned — do not follow.
  useFrame((_state, delta) => {
    // Manual is user-owned, except while a transition is handing it back.
    if (cameraMode === 'manual' && !cameraTransitionRef.current) return
    if (!vrmRef.current || !controlsRef.current) return

    // Compute t from canvas width: desktop (>768px) → t=0 (wideFraming only),
    // mobile (<360px) → t=1 (full narrowFraming + targetZ shift for chat panel).
    // Matches Tailwind md breakpoint where MobileNavBar/ChatPanel activate.
    const tClamped = Math.max(0, Math.min(1,
      (768 - size.width) / (768 - 360)
    ))
    const preset = CAMERA_RESPONSIVE_PRESETS[cameraMode]
    responsiveTargetRef.current.set(
      THREE.MathUtils.lerp(preset.wideFraming[0], preset.narrowFraming[0], tClamped),
      THREE.MathUtils.lerp(preset.wideFraming[1], preset.narrowFraming[1], tClamped),
      THREE.MathUtils.lerp(preset.wideFraming[2], preset.narrowFraming[2], tClamped),
    )

    const targetZTarget = THREE.MathUtils.lerp(0, preset.narrowTargetZ, tClamped)

    // Smooth towards target on resize; snap during camera-mode transition
    if (cameraTransitionRef.current) {
      responsiveDisplayRef.current.copy(responsiveTargetRef.current)
      targetZRef.current = targetZTarget
    } else {
      responsiveDisplayRef.current.lerp(responsiveTargetRef.current, 0.2)
      targetZRef.current = THREE.MathUtils.lerp(targetZRef.current, targetZTarget, 0.2)
    }

    const mode = CAMERA_MODES[cameraMode]
    const bone =
      vrmRef.current.humanoid.getNormalizedBoneNode(mode.boneName) ??
      vrmRef.current.humanoid.getNormalizedBoneNode(VRMHumanBoneName.Hips)
    if (!bone) return

    bone.getWorldPosition(followPos)

    // The look-at point. Same as the anchor unless the mode tracks a second
    // bone, in which case it sits `trackWeight` of the way toward it. The
    // camera's own position is always placed relative to the anchor.
    lookPos.copy(followPos)
    const trackBone = mode.trackBone
      ? vrmRef.current.humanoid.getNormalizedBoneNode(mode.trackBone)
      : null
    if (trackBone) {
      trackBone.getWorldPosition(trackPos)
      lookPos.lerp(trackPos, mode.trackWeight ?? 0)
    }

    if (cameraTransitionRef.current) {
      const t = cameraTransitionRef.current
      t.elapsed += delta
      const progress = Math.min(t.elapsed / t.duration, 1)
      const eased = t.release ? easeInOutCubic(progress) : 1 - Math.pow(1 - progress, 3)

      const currentCustomOffset = new THREE.Vector3(cameraConfig.offsetX, cameraConfig.offsetY, cameraConfig.offsetZ)
      let endTarget: THREE.Vector3
      let endPos: THREE.Vector3
      if (t.endOffset) {
        // Manual hand-back: the user's own framing, re-anchored to where the
        // head is now.
        endTarget = followPos.clone().add(t.endOffset.target)
        endPos = followPos.clone().add(t.endOffset.pos)
      } else {
        endTarget = lookPos.clone().add(currentCustomOffset)
        endTarget.z += targetZRef.current
        endPos = followPos.clone().add(responsiveDisplayRef.current).add(currentCustomOffset)
      }
      // Land where OrbitControls will keep the camera. The `head` preset sits
      // 0.5 m out, inside the default 1 m minDistance, so a transition ending
      // there was clamped outward on the next frame: a second, smaller snap.
      const minDist = cameraMode === 'face' ? FACE_LOCK_MIN_DISTANCE : cameraConfig.minDistance
      const reach = endPos.distanceTo(endTarget)
      if (reach < minDist) {
        const dir = reach > 1e-6
          ? endPos.clone().sub(endTarget).normalize()
          : camera.position.clone().sub(endTarget).normalize()
        endPos = endTarget.clone().addScaledVector(dir, minDist)
      }

      camera.position.lerpVectors(t.startPos, endPos, eased)
      controlsRef.current.target.lerpVectors(t.startTarget, endTarget, eased)
      controlsRef.current.update()

      if (progress >= 1) {
        cameraTransitionRef.current = null
        lastAppliedOffsetRef.current.copy(responsiveDisplayRef.current)
        if (t.release) {
          releasingRef.current = false
          setReleasing(false)
        }
      }
      return
    }

    const currentCustomOffset = new THREE.Vector3(cameraConfig.offsetX, cameraConfig.offsetY, cameraConfig.offsetZ)
    const targetPos = lookPos.clone().add(currentCustomOffset)
    targetPos.z += targetZRef.current

    if (!cameraInitializedRef.current) {
      controlsRef.current.target.copy(targetPos)
      camera.position.copy(followPos).add(responsiveDisplayRef.current).add(currentCustomOffset)
      lastAppliedOffsetRef.current.copy(responsiveDisplayRef.current)
      camera.lookAt(targetPos)
      controlsRef.current.update()
      cameraInitializedRef.current = true
      return
    }

    if (mode.holdPosition) {
      // Track: the camera stays where the entry transition parked it and only
      // the look-at target moves, damped, toward the blended point. Deliberately
      // ahead of the `followTarget` check — an FSM-granted lock owns the camera
      // and must track even if the user has switched auto-follow off.
      // OrbitControls.update() re-aims the camera at the new target; with no
      // input pending it leaves the position alone (bar the radius clamp,
      // which FACE_LOCK_MIN_DISTANCE keeps out of the way).
      controlsRef.current.target.lerp(targetPos, 1 - Math.exp(-TRACK_DAMPING * delta))
      lastAppliedOffsetRef.current.copy(responsiveDisplayRef.current)
      controlsRef.current.update()
      return
    }

    if (!cameraConfig.followTarget) return

    // How far did the follow point move since the last frame?
    deltaVec.subVectors(targetPos, controlsRef.current.target)

    // How much did the responsive offset change since last frame?
    offsetDeltaVec.subVectors(responsiveDisplayRef.current, lastAppliedOffsetRef.current)

    // Move the camera by both the bone delta and the offset delta
    camera.position.add(deltaVec).add(offsetDeltaVec)

    lastAppliedOffsetRef.current.copy(responsiveDisplayRef.current)

    // Update the controls target to the new follow point
    controlsRef.current.target.copy(targetPos)
    controlsRef.current.update()
  })

  // Second plane: enforce Lock X/Y/Z after the main follow/transition logic and
  // after OrbitControls has applied any user drag. Registration order guarantees
  // this runs after the preceding useFrame.
  useFrame(() => {
    if (!controlsRef.current) return
    if (!cameraInitializedRef.current) return
    if (!lockInitializedRef.current) {
      lockPrevTargetRef.current.copy(controlsRef.current.target)
      lockPrevPosRef.current.copy(camera.position)
      lockInitializedRef.current = true
      return
    }
    const target = controlsRef.current.target as THREE.Vector3
    const pos = camera.position as THREE.Vector3
    let needsUpdate = false
    if (cameraConfig.lockX) {
      if (target.x !== lockPrevTargetRef.current.x) { target.x = lockPrevTargetRef.current.x; needsUpdate = true }
      if (pos.x !== lockPrevPosRef.current.x) { pos.x = lockPrevPosRef.current.x; needsUpdate = true }
    } else {
      lockPrevTargetRef.current.x = target.x
      lockPrevPosRef.current.x = pos.x
    }
    if (cameraConfig.lockY) {
      if (target.y !== lockPrevTargetRef.current.y) { target.y = lockPrevTargetRef.current.y; needsUpdate = true }
      if (pos.y !== lockPrevPosRef.current.y) { pos.y = lockPrevPosRef.current.y; needsUpdate = true }
    } else {
      lockPrevTargetRef.current.y = target.y
      lockPrevPosRef.current.y = pos.y
    }
    if (cameraConfig.lockZ) {
      if (target.z !== lockPrevTargetRef.current.z) { target.z = lockPrevTargetRef.current.z; needsUpdate = true }
      if (pos.z !== lockPrevPosRef.current.z) { pos.z = lockPrevPosRef.current.z; needsUpdate = true }
    } else {
      lockPrevTargetRef.current.z = target.z
      lockPrevPosRef.current.z = pos.z
    }
    if (needsUpdate) controlsRef.current.update()
  })

return (
    <>
      {/* ── Phase 1: Renderer color pipeline + material audit ────── */}
      <RendererSetup vrm={vrmRef.current} /> // eslint-disable-line react-hooks/refs,react-hooks/immutability,react-hooks/set-state-in-effect

      {/* ── Phase 2+3+4: Lighting, shadows & ground ─────────────── */}
      <SceneLighting vrm={vrmRef.current} />

      {/* ── Phase 5: Background (gradient / HDRI + stars) ────────── */}
      <SceneEnvironment theme={theme} />

      {/* ── Phase 6: Post processing (Bloom / SSAO / Vignette) ──── */}
      <ScenePostProcessing />

      {/* key={vrmUrl}: a model switch is a clean remount — the old model is
          dropped immediately and the new one stays hidden (plus a loading
          overlay) until its first pose is applied. */}
      <VRMCharacter
        key={vrmUrl}
        vrmRef={vrmRef}
        vrmUrl={vrmUrl}
        modelId={modelId}
        onReady={onReady}
        avatarRef={avatarRef}
        onTeleport={handleTeleport}
      />
      <ThinkingBubble vrmRef={vrmRef} />
      <FloatingParticles />

      {/* ── Debug Overlays ─────────────────────────────────────── */}
      {gfx.showGrid && (
        <group rotation={[Math.PI / 2, 0, 0]}>
          <gridHelper
            args={[8, 16, theme === 'dark' ? '#666688' : '#808080', theme === 'dark' ? '#2a2a3e' : '#c0c0c0']}
            position={[0, 0, -1.5]}
          />
        </group>
      )}

      {gfx.showAxes && (
        <>
          <primitive object={axesHelper} />
          {/* These labels are DOM nodes, not WebGL. drei's default zIndexRange
              starts at 16,777,271, which put them on top of the chat box and
              sidebar (z-index 9999/9998). Cap them the same way ThinkingBubble
              does so they stay under the UI. */}
          <Html position={[3.2, 0, 0]} zIndexRange={AXIS_LABEL_Z}>
            <span style={{ color: 'red', fontWeight: 'bold', fontSize: 14 }}>X</span>
          </Html>
          <Html position={[0, 3.2, 0]} zIndexRange={AXIS_LABEL_Z}>
            <span style={{ color: 'green', fontWeight: 'bold', fontSize: 14 }}>Y</span>
          </Html>
          <Html position={[0, 0, 3.2]} zIndexRange={AXIS_LABEL_Z}>
            <span style={{ color: 'blue', fontWeight: 'bold', fontSize: 14 }}>Z</span>
          </Html>
        </>
      )}

      {/* Orbital camera: follows hips, enforces minimum distance (radius) */}
      <OrbitControls
        ref={controlsRef}
        enabled={!cameraHeld}
        enablePan={cameraConfig.enablePan}
        enableZoom={cameraConfig.enableZoom}
        minDistance={cameraHeld ? FACE_LOCK_MIN_DISTANCE : cameraConfig.minDistance}
        maxDistance={cameraConfig.maxDistance}
        target={[0, 0, 0]}
        onStart={() => {
          if (controlsRef.current) {
            manualStartTargetRef.current = controlsRef.current.target.clone()
            manualStartDistRef.current = camera.position.distanceTo(controlsRef.current.target as THREE.Vector3)
          }
        }}
        onEnd={() => {
          const startTarget = manualStartTargetRef.current
          const startDist = manualStartDistRef.current
          manualStartTargetRef.current = null
          manualStartDistRef.current = null
          if (!startTarget || startDist === null || !controlsRef.current) return
          const targetDelta = (controlsRef.current.target as THREE.Vector3).distanceTo(startTarget)
          const endDist = camera.position.distanceTo(controlsRef.current.target as THREE.Vector3)
          const distDelta = Math.abs(endDist - startDist)
          const hasPanned = targetDelta > MANUAL_THRESHOLD_PAN
          const hasZoomed = distDelta > MANUAL_THRESHOLD_ZOOM
          const shouldManual = hasPanned || hasZoomed
          if (shouldManual) {
            notifyManualInteraction()
          }
        }}
      />
      <BodyPartClickLogger vrmRef={vrmRef} />
    </>
  )
}

/* ────────────────────────── Body-part picking ────────────────────── */

/** Pointer travel, in CSS px, still counted as a click rather than a drag. */
const DRAG_SLOP_PX = 5

/**
 * Turns a click on the avatar into a `UserActivity`.
 *
 * This component knows which body part was hit and nothing else — not which
 * animation plays, not which expression follows. It reports the interaction and
 * `dispatchActivity` asks the character's own profile what that means, so a
 * model can bring different reactions from the database without this file
 * changing.
 *
 * The picking itself lives in BodyPartPicker (a GPU pick, not a raycast — see
 * that file). What stays here is plumbing: rebuild the picker when the VRM is
 * swapped, keep its one-off vertex pass off the frame that swapped the model in,
 * and tell a click apart from a camera drag.
 */
function BodyPartClickLogger({ vrmRef }: { vrmRef: React.MutableRefObject<VRM | null> }) {
  const { camera, gl } = useThree()
  const { dispatchActivity } = useMotion()
  const pickerRef = useRef<BodyPartPicker | null>(null)
  const builtForRef = useRef<VRM | null>(null)
  const idleRef = useRef<{ cancel: () => void } | null>(null)

  // The VRM arrives — and is replaced on a model switch — through a ref, so
  // there is no render to hang an effect on. One identity compare per frame is
  // cheaper than routing the model through context just for this.
  useFrame(() => {
    const vrm = vrmRef.current
    if (vrm === builtForRef.current) return
    builtForRef.current = vrm

    idleRef.current?.cancel()
    idleRef.current = null
    pickerRef.current?.dispose()
    pickerRef.current = null
    if (!vrm) return

    const picker = new BodyPartPicker(vrm, gl)
    pickerRef.current = picker
    // Tagging every vertex is a single pass over the whole model. Same reasoning
    // as the clip warm-up in AnimationRegistry: do it while nothing is waiting.
    idleRef.current = scheduleIdle(() => {
      idleRef.current = null
      if (pickerRef.current !== picker) return
      picker.build()
      picker.warm(camera)
    })
  })

  useEffect(() => {
    const el = gl.domElement
    // Where the gesture that is currently down began. A pointerup further than
    // DRAG_SLOP_PX away was a camera orbit, not a click on the character.
    let down: { id: number; x: number; y: number } | null = null

    const onDown = (e: PointerEvent) => {
      down = { id: e.pointerId, x: e.clientX, y: e.clientY }
    }

    const onUp = (e: PointerEvent) => {
      const start = down
      down = null
      if (!start || start.id !== e.pointerId) return
      if (Math.abs(e.clientX - start.x) > DRAG_SLOP_PX) return
      if (Math.abs(e.clientY - start.y) > DRAG_SLOP_PX) return

      const picker = pickerRef.current
      if (!picker?.isReady) return
      const rect = el.getBoundingClientRect()

      void picker.pickAsync(e.clientX - rect.left, e.clientY - rect.top, camera).then((part) => {
        // `blocking` is what the click costs the main thread; `latency` includes
        // the GPU fence wait, which happens off-thread. See BodyPartPicker.timings.
        const { render, blocking, latency } = picker.timings
        console.log(
          '[bodyPart]',
          part ?? 'miss',
          `blocking ${blocking.toFixed(2)}ms (render ${render.toFixed(2)}) · answer in ${latency.toFixed(2)}ms`,
        )
        if (part) void dispatchActivity(bodyPartClick(part))
      })
    }

    const onCancel = () => {
      down = null
    }

    // Capture phase, like the click ripple: OrbitControls calls
    // setPointerCapture on pointerdown, and this sidesteps any question of what
    // it does with the event on the way back up.
    el.addEventListener('pointerdown', onDown, { capture: true })
    el.addEventListener('pointerup', onUp, { capture: true })
    el.addEventListener('pointercancel', onCancel, { capture: true })
    return () => {
      el.removeEventListener('pointerdown', onDown, { capture: true })
      el.removeEventListener('pointerup', onUp, { capture: true })
      el.removeEventListener('pointercancel', onCancel, { capture: true })
    }
  }, [camera, gl, dispatchActivity])

  useEffect(
    () => () => {
      idleRef.current?.cancel()
      idleRef.current = null
      pickerRef.current?.dispose()
      pickerRef.current = null
    },
    [],
  )

  return null
}

/** requestIdleCallback with a setTimeout fallback, cancellable either way. */
function scheduleIdle(task: () => void): { cancel: () => void } {
  if (typeof requestIdleCallback === 'function') {
    const handle = requestIdleCallback(task, { timeout: 2000 })
    return { cancel: () => cancelIdleCallback(handle) }
  }
  const handle = setTimeout(task, 200)
  return { cancel: () => clearTimeout(handle) }
}

/* ───────────────────────── Exported Component ────────────────────── */

export default function CharacterViewer() {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const { selectedVrmId, vrmOptions, vrmOptionsError, avatarRef, setClipInfo, currentState, isAvatarSwitching, setAvatarReady } = useMotion()

  const selectedVrm = vrmOptions.find((o) => o.id === selectedVrmId)
  // No local fallback any more: the models live on the CDN, so until the
  // catalog answers there is genuinely no URL to render. Bundling anne.vrm as a
  // safety net would put the 15.8 MB this change removes straight back in.
  const vrmUrl = selectedVrm?.url
  // Readiness gate driven by VRMCharacter: the model (and this overlay) swap
  // only when the first pose is actually applied — never a timed wait.
  const [viewerReady, setViewerReady] = useState(false)
  // Stable model id for loadProfile. With the catalog it is already the slug;
  // the bundled-fallback path still yields a label like "models/anne.vrm".
  const modelId = (selectedVrm?.id ?? selectedVrm?.label ?? 'anne')
    .replace(/\.vrm$/i, '')
    .replace(/^.*\//, '')
    .toLowerCase()

  useEffect(() => {
    setClipInfo(null)
  }, [vrmUrl, setClipInfo])

  // B3: unified switch — when VRMCharacter reports revealed, clear switching flag so
  // both card mini-overlay and viewer fullscreen overlay turn off together.
  // Lag is only in Canvas, but this sync keeps the two DOM overlays matching.
  useEffect(() => {
    setAvatarReady(viewerReady)
  }, [viewerReady, setAvatarReady])

  // Click ripple state — uses native pointerdown with capture to fire before
  // R3F's internal event system calls stopPropagation on the canvas element.
  const [clicks, setClicks] = useState<{ id: number; x: number; y: number }[]>([])
  const clickIdRef = useRef(0)
  const lastClickTimeRef = useRef(0)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const handler = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse' && e.pointerType !== 'touch' && e.pointerType !== 'pen') return
      const now = Date.now()
      if (now - lastClickTimeRef.current < 200) return
      lastClickTimeRef.current = now
      const rect = el.getBoundingClientRect()
      const id = ++clickIdRef.current
      setClicks((prev) => [...prev, { id, x: e.clientX - rect.left, y: e.clientY - rect.top }])
    }
    el.addEventListener('pointerdown', handler, { capture: true })
    return () => el.removeEventListener('pointerdown', handler, { capture: true })
  }, [])

  const removeClick = (id: number) => {
    setClicks((prev) => prev.filter((c) => c.id !== id))
  }

  // Feed normalized mouse position to the avatar's eye gaze (§4.2 / EyeController).
  // Suppressed during the greeting animation so the model performs its scripted
  // wave without being pulled toward the cursor.
  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (currentState === 'greeting') return
    const controller = avatarRef.current
    if (!controller) return
    const rect = e.currentTarget.getBoundingClientRect()
    const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1
    const ny = -(((e.clientY - rect.top) / rect.height) * 2 - 1)
    controller.setMouse(nx, ny)
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden"
      onMouseMove={handleMouseMove}
      style={{
        background: 'transparent',
      }}
    >
      <Canvas
        shadows={CANVAS_SHADOWS}
        camera={CANVAS_CAMERA}
        gl={CANVAS_GL}
        onCreated={({ gl }) => {
          gl.outputColorSpace = ENV_CONFIG.renderer.outputColorSpace
        }}
      >
        <Suspense fallback={null}>
          <GraphicsProvider>
          {vrmUrl && (
            <Scene
              theme={theme}
              vrmUrl={vrmUrl}
              modelId={modelId}
              onReady={setViewerReady}
              avatarRef={avatarRef}
            />
          )}
          </GraphicsProvider>
        </Suspense>
      </Canvas>

      {/* Click ripple effects */}
      {clicks.map((c) => (
        <ClickRipple key={c.id} x={c.x} y={c.y} theme={theme} onDone={() => removeClick(c.id)} />
      ))}

      {/* Loading overlay: shown while the model has no pose yet (initial load
           / model switch). Replaces the old T-pose flash with a spinner.
           A catalog failure is called out by name — without this the screen is
           an indistinguishable spinner whether the CDN is unreachable or the
           model is merely still downloading.
           B3: viewerReady and isAvatarSwitching share lifecycle — both DOM overlays
           (card mini + viewer fullscreen) use same violet style and turn off together
           when revealed. Canvas freeze does not affect these DOM overlays. */}
      {(!viewerReady || isAvatarSwitching) && (
        <LoadingOverlay
          text={
            vrmOptionsError
              ? t('character.load_error', { error: vrmOptionsError })
              : vrmUrl
                ? t('character.loading_avatar')
                : t('character.loading_catalog')
          }
        />
      )}

      {/* Bottom gradient */}
      <div className="absolute bottom-0 left-0 right-0 h-24 pointer-events-none bg-gradient-to-t from-background/80 to-transparent" />
    </div>
  )
}


