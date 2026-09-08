import { useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import { motion, AnimatePresence } from 'framer-motion'
import { VRMHumanBoneName } from '@pixiv/three-vrm'
import type { VRM } from '@pixiv/three-vrm'
import * as THREE from 'three'
import { useMotion } from '../../hooks/useMotion'
import { useChat } from '../../hooks/useChat'

interface ThinkingBubbleProps {
  vrmRef: React.MutableRefObject<VRM | null>
}

const HEAD_OFFSET = new THREE.Vector3(-0.15, -0.02, 0.28)

// Distances from bubble center toward head (px) — margin 4px between bubble edge and first dot
const TAIL_DISTS = [22, 32, 44]

export default function ThinkingBubble({ vrmRef }: ThinkingBubbleProps) {
  const { currentState } = useMotion()
  const { stageLabel } = useChat()
  const { camera } = useThree()
  const groupRef = useRef<THREE.Group>(null)
  const headPos = useRef(new THREE.Vector3())
  const bubblePos = useRef(new THREE.Vector3())
  const tail0Ref = useRef<HTMLSpanElement>(null)
  const tail1Ref = useRef<HTMLSpanElement>(null)
  const tail2Ref = useRef<HTMLSpanElement>(null)
  const initializedRef = useRef(false)

  const visible = currentState === 'thinking_intro' && !!stageLabel
  const displayText = stageLabel ?? ''

  // Scratch vectors to avoid alloc per frame
  const headNdc = useRef(new THREE.Vector3())
  const bubbleNdc = useRef(new THREE.Vector3())

  useFrame(() => {
    const group = groupRef.current
    const vrm = vrmRef.current
    if (!group || !vrm?.humanoid) return

    const bone =
      vrm.humanoid.getNormalizedBoneNode(VRMHumanBoneName.Head) ??
      vrm.humanoid.getNormalizedBoneNode(VRMHumanBoneName.Neck) ??
      vrm.humanoid.getNormalizedBoneNode(VRMHumanBoneName.Hips)

    if (!bone) return

    bone.getWorldPosition(headPos.current)

    const target = headPos.current.clone().add(HEAD_OFFSET)

    if (!initializedRef.current) {
      bubblePos.current.copy(target)
      group.position.copy(bubblePos.current)
      initializedRef.current = true
    } else {
      bubblePos.current.lerp(target, 0.18)
      group.position.copy(bubblePos.current)
    }

    if (!visible) return

    // Project to NDC to get screen-space angle bubble -> head
    headNdc.current.copy(headPos.current).project(camera)
    bubbleNdc.current.copy(bubblePos.current).project(camera)
    const dx = headNdc.current.x - bubbleNdc.current.x
    const dy = -(headNdc.current.y - bubbleNdc.current.y) // CSS Y is down
    if (Math.abs(dx) < 0.0001 && Math.abs(dy) < 0.0001) return
    const angle = Math.atan2(dy, dx)
    const cosA = Math.cos(angle)
    const sinA = Math.sin(angle)

    const dots = [tail0Ref.current, tail1Ref.current, tail2Ref.current]
    for (let i = 0; i < 3; i++) {
      const el = dots[i]
      if (!el) continue
      const dist = TAIL_DISTS[i]
      const x = cosA * dist
      const y = sinA * dist
      el.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px)`
    }
  })

  if (!visible) {
    initializedRef.current = false
  }

  return (
    <group ref={groupRef}>
      <Html
        center
        distanceFactor={1.35}
        zIndexRange={[100, 0]}
        style={{ pointerEvents: 'none' }}
        transform={false}
      >
        <AnimatePresence>
          {visible && (
            <motion.div
              key="bubble"
              initial={{ scale: 0, opacity: 0, y: 12 }}
              animate={{ scale: 1, opacity: 1, y: 0 }}
              exit={{ scale: 0, opacity: 0, y: 8 }}
              transition={{ type: 'spring', stiffness: 420, damping: 18, mass: 0.7 }}
              className="relative pointer-events-none select-none"
              style={{ pointerEvents: 'none', width: 0, height: 0, overflow: 'visible' }}
            >
              <motion.div
                animate={{ y: [0, -4, 0] }}
                transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }}
                className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
                style={{ overflow: 'visible' }}
              >
                <div className="relative bg-white/95 dark:bg-zinc-900/90 backdrop-blur-md rounded-[18px] px-4 py-2.5 shadow-[0_8px_28px_rgba(0,0,0,0.12),0_2px_8px_rgba(0,0,0,0.08)] border border-zinc-200/70 dark:border-zinc-700/60 flex items-center whitespace-nowrap">
                  <span className="text-[13px] font-medium leading-none tracking-tight text-zinc-700 dark:text-zinc-100 inline-flex">
                    {displayText.split('').map((char, i) => (
                      <motion.span
                        key={`${displayText}-${i}`}
                        animate={{ y: [0, -2, 0] }}
                        transition={{
                          duration: 0.32,
                          repeat: Infinity,
                          repeatDelay: 1.6,
                          delay: i * 0.025,
                          ease: 'easeInOut',
                        }}
                        style={{ display: 'inline-block', whiteSpace: 'pre' }}
                      >
                        {char === ' ' ? '\u00A0' : char}
                      </motion.span>
                    ))}
                  </span>
                </div>
              </motion.div>

              {/* Tail dots — positioned from bubble center toward head via JS */}
              <span
                ref={tail0Ref}
                className="absolute left-1/2 top-1/2 w-[14px] h-[14px] bg-white/95 dark:bg-zinc-900/90 rounded-full border border-zinc-200/70 dark:border-zinc-700/60 shadow-sm pointer-events-none"
                style={{ transform: 'translate(-50%, -50%)' }}
                aria-hidden
              />
              <span
                ref={tail1Ref}
                className="absolute left-1/2 top-1/2 w-[9px] h-[9px] bg-white/95 dark:bg-zinc-900/90 rounded-full border border-zinc-200/60 dark:border-zinc-700/50 shadow-sm pointer-events-none"
                style={{ transform: 'translate(-50%, -50%)' }}
                aria-hidden
              />
              <span
                ref={tail2Ref}
                className="absolute left-1/2 top-1/2 w-[6px] h-[6px] bg-white/95 dark:bg-zinc-900/90 rounded-full shadow-sm pointer-events-none"
                style={{ transform: 'translate(-50%, -50%)' }}
                aria-hidden
              />
            </motion.div>
          )}
        </AnimatePresence>
      </Html>
    </group>
  )
}
