import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import {
  MFCC_COEFFS,
  classify,
  createFeatureExtractor,
  nearestTemplate,
} from '../../src/avatar/vowelClassifier.ts'

/**
 * Measurement lab for vowel visemes (Phase A — no runtime impact).
 *
 *   calibrate --audio <dir> --words <json> --out <file.ts>
 *   evaluate  --audio <dir> --words <json> --templates <file.ts> [--report <file.md>]
 *
 * Frames are 1024 samples at a step of round(sampleRate / 60). A frame is a
 * vowel frame when its low-band (100–1000 Hz) power is >= 0.25x the file
 * maximum. A run is consecutive vowel frames; runs shorter than 3 frames
 * are dropped. Items with "take": "first-half" (diphthong tails like "toe")
 * keep only the first half of each run.
 */

const FRAME_SIZE = 1024
const VISEMES = ['A', 'I', 'U', 'E', 'O']

// ── WAV ─────────────────────────────────────────────────────────────────

function readWavMono16(path) {
  const buf = readFileSync(path)
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`${path}: not a RIFF/WAVE file`)
  }
  let fmt = null
  let dataOff = -1
  let dataLen = 0
  let off = 12
  while (off + 8 <= buf.length) {
    const id = buf.toString('ascii', off, off + 4)
    const size = buf.readUInt32LE(off + 4)
    if (id === 'fmt ') {
      fmt = {
        audioFormat: buf.readUInt16LE(off + 8),
        channels: buf.readUInt16LE(off + 10),
        sampleRate: buf.readUInt32LE(off + 12),
        bitsPerSample: buf.readUInt16LE(off + 22),
      }
    } else if (id === 'data') {
      dataOff = off + 8
      dataLen = size
    }
    off += 8 + size + (size % 2)
  }
  if (!fmt) {
    throw new Error(`${path}: missing fmt chunk`)
  }
  if (dataOff < 0) {
    throw new Error(`${path}: missing data chunk`)
  }
  if (fmt.audioFormat !== 1) {
    throw new Error(`${path}: not PCM (format ${fmt.audioFormat})`)
  }
  if (fmt.bitsPerSample !== 16) {
    throw new Error(`${path}: not 16-bit (${fmt.bitsPerSample} bit)`)
  }
  const frames = Math.floor(dataLen / (fmt.channels * 2))
  const pcm = new Float32Array(frames)
  for (let i = 0; i < frames; i++) {
    // First channel only; normalised to [-1, 1].
    pcm[i] = buf.readInt16LE(dataOff + i * fmt.channels * 2) / 32768
  }
  return { sampleRate: fmt.sampleRate, pcm }
}

// ── Frame analysis (shared by calibrate + evaluate) ─────────────────────

function analyzeFile(wavPath, take) {
  const { sampleRate, pcm } = readWavMono16(wavPath)
  const ex = createFeatureExtractor(sampleRate, FRAME_SIZE)
  const step = Math.round(sampleRate / 60)
  const frameBuf = new Float32Array(FRAME_SIZE)
  const out = new Float32Array(MFCC_COEFFS)
  const frames = []
  for (let start = 0; start + FRAME_SIZE <= pcm.length; start += step) {
    frameBuf.set(pcm.subarray(start, start + FRAME_SIZE))
    const power = ex.extract(frameBuf, out)
    frames.push({ start, time: start / sampleRate, mfcc: Array.from(out), power })
  }
  let maxP = 0
  for (const f of frames) {
    if (f.power > maxP) {
      maxP = f.power
    }
  }
  const threshold = 0.25 * maxP
  const runs = []
  let cur = []
  const flush = () => {
    if (cur.length >= 3) {
      runs.push(cur)
    }
    cur = []
  }
  for (const f of frames) {
    if (f.power >= threshold) {
      cur.push(f)
    } else {
      flush()
    }
  }
  flush()
  let keptRuns = runs
  if (take === 'first-half') {
    keptRuns = runs.map((r) => r.slice(0, Math.ceil(r.length / 2)))
  }
  const kept = []
  for (const r of keptRuns) {
    for (const f of r) {
      kept.push(f)
    }
  }
  return { sampleRate, step, pcm, frames, runs, kept }
}

function majorityLabel(counts) {
  let best = VISEMES[0]
  for (const v of VISEMES) {
    if (counts[v] > counts[best]) {
      best = v
    }
  }
  return best
}

// ── calibrate ───────────────────────────────────────────────────────────

function cmdCalibrate(opts) {
  const words = JSON.parse(readFileSync(opts.words, 'utf8'))
  const built = []
  for (const item of words.calibration) {
    const wav = resolve(opts.audio, `cal_${item.word}.wav`)
    const a = analyzeFile(wav, item.take)
    const warn = a.runs.length !== 3 ? '  [warn] runs != 3 — TTS may have misread this word' : ''
    console.log(`${item.word}: ${a.kept.length} vowel frames, ${a.runs.length} runs${warn}`)
    if (a.kept.length === 0) {
      console.log(`  [warn] ${item.word}: no vowel frames — skipping this word, no template emitted`)
      continue
    }
    const mean = new Array(MFCC_COEFFS).fill(0)
    for (const f of a.kept) {
      for (let i = 0; i < MFCC_COEFFS; i++) {
        mean[i] += f.mfcc[i] / a.kept.length
      }
    }
    built.push({ viseme: item.viseme, word: item.word, mfcc: mean, kept: a.kept })
  }
  const templates = built.map(({ viseme, word, mfcc }) => ({ viseme, word, mfcc }))
  const byViseme = new Map()
  for (const t of templates) {
    if (!byViseme.has(t.viseme)) {
      byViseme.set(t.viseme, [])
    }
    byViseme.get(t.viseme).push(t)
  }
  const dists = []
  for (const b of built) {
    for (const f of b.kept) {
      dists.push(nearestTemplate(f.mfcc, byViseme.get(b.viseme)).distance)
    }
  }
  dists.sort((x, y) => x - y)
  const p95 = dists[Math.min(dists.length - 1, Math.max(0, Math.ceil(0.95 * dists.length) - 1))]
  const rejectDistance = 1.5 * p95

  const r4 = (x) => Number(x.toFixed(4))
  const lines = [
    '/** Generated by scripts/visemes/viseme-lab.mjs — DO NOT EDIT. */',
    "import type { VowelTemplateSet } from '../vowelClassifier'",
    '',
    'export const anneEn: VowelTemplateSet = {',
    '  version: 1,',
    '  frameSize: 1024,',
    '  templates: [',
  ]
  for (const t of templates) {
    lines.push(
      `    { viseme: '${t.viseme}', word: '${t.word}', mfcc: [${t.mfcc.map(r4).join(', ')}] },`,
    )
  }
  lines.push('  ],', `  rejectDistance: ${r4(rejectDistance)},`, '}', '')
  const outPath = resolve(opts.out)
  mkdirSync(dirname(outPath), { recursive: true })
  writeFileSync(outPath, lines.join('\n'))
  console.log(`wrote ${opts.out} (${templates.length} templates, rejectDistance=${r4(rejectDistance)})`)
}

// ── evaluate ────────────────────────────────────────────────────────────

async function cmdEvaluate(opts) {
  const words = JSON.parse(readFileSync(opts.words, 'utf8'))
  const mod = await import(pathToFileURL(resolve(opts.templates)).href)
  const set =
    mod.anneEn ??
    Object.values(mod).find((v) => v && typeof v === 'object' && v.version === 1 && Array.isArray(v.templates))
  if (!set) {
    throw new Error(`${opts.templates}: no exported VowelTemplateSet found`)
  }

  const lines = []
  const log = (s = '') => {
    console.log(s)
    lines.push(s)
  }

  // 1. Held-out words: nearest template per frame, no rejectDistance.
  log('# Viseme lab — evaluate')
  log('')
  log('## Held-out words (nearest template per frame, no reject)')
  log('')
  log('| word | expected | majority | frames | correct % | result |')
  log('|------|----------|----------|--------|-----------|--------|')
  const wordResults = []
  const confusion = {}
  for (const e of VISEMES) {
    confusion[e] = { A: 0, I: 0, U: 0, E: 0, O: 0 }
  }
  let correctFrames = 0
  let totalFrames = 0
  for (const item of words.heldout) {
    const wav = resolve(opts.audio, `held_${item.word}.wav`)
    const a = analyzeFile(wav, item.take)
    const counts = { A: 0, I: 0, U: 0, E: 0, O: 0 }
    for (const f of a.kept) {
      const hit = nearestTemplate(f.mfcc, set.templates)
      const pred = set.templates[hit.index].viseme
      counts[pred] += 1
      confusion[item.viseme][pred] += 1
      totalFrames += 1
      if (pred === item.viseme) {
        correctFrames += 1
      }
    }
    const n = a.kept.length
    const maj = majorityLabel(counts)
    const pct = n === 0 ? 0 : (100 * counts[item.viseme]) / n
    const ok = n > 0 && maj === item.viseme
    wordResults.push({ word: item.word, expected: item.viseme, majority: maj, ok })
    const result = ok ? 'OK' : n === 0 ? 'SAI (0 frame)' : 'SAI'
    log(
      `| ${item.word} | ${item.viseme} | ${maj} | ${n} | ${n === 0 ? 'n/a' : `${pct.toFixed(1)}%`} | ${result} |`,
    )
  }

  // 2. Confusion matrix + frame accuracy.
  log('')
  log('## Confusion matrix (rows = expected, cols = predicted, per frame)')
  log('')
  log('| exp\\pred | A | I | U | E | O |')
  log('|----------|---|---|---|---|---|')
  for (const e of VISEMES) {
    log(`| ${e} | ${VISEMES.map((p) => confusion[e][p]).join(' | ')} |`)
  }
  const frameAcc = totalFrames === 0 ? 0 : (100 * correctFrames) / totalFrames
  log('')
  log(`Frame accuracy: ${correctFrames}/${totalFrames} = ${frameAcc.toFixed(1)}% (chance = 20%)`)

  // 3. Gate 1.
  const passCount = wordResults.filter((r) => r.ok).length
  const perViseme = {}
  for (const v of VISEMES) {
    const got = wordResults.filter((r) => r.expected === v && r.ok).length
    const need = wordResults.filter((r) => r.expected === v).length
    perViseme[v] = `${got}/${need}`
  }
  const visemeCovered = VISEMES.every((v) =>
    wordResults.some((r) => r.expected === v && r.ok),
  )
  const gate = passCount >= 8 && visemeCovered
  log('')
  log('## Cổng 1')
  log('')
  log(`- Majority đúng: ${passCount}/10 (cần ≥ 8)`)
  log(`- Mỗi viseme có ≥ 1/2 từ đúng: ${VISEMES.map((v) => `${v} ${perViseme[v]}`).join(', ')}`)
  log(`- Kết luận: ${gate ? 'ĐẠT' : 'KHÔNG ĐẠT'}`)

  // 4. Sentence timelines: 100 ms cells, majority of kept frames with
  // distance <= rejectDistance, else '-'.
  log('')
  log('## Sentence timelines (100 ms cells, `-` = no close vowel frame)')
  log('')
  const sentAnalyses = []
  words.sentences.forEach((sentence, i) => {
    const wav = resolve(opts.audio, `sent_${i}.wav`)
    const a = analyzeFile(wav, undefined)
    sentAnalyses.push(a)
    const duration = a.pcm.length / a.sampleRate
    const cells = Math.max(1, Math.ceil(duration / 0.1))
    const row = []
    for (let c = 0; c < cells; c++) {
      const t0 = c * 0.1
      const t1 = t0 + 0.1
      const counts = { A: 0, I: 0, U: 0, E: 0, O: 0 }
      let n = 0
      for (const f of a.kept) {
        if (f.time < t0 || f.time >= t1) {
          continue
        }
        const hit = nearestTemplate(f.mfcc, set.templates)
        if (hit.distance > set.rejectDistance) {
          continue
        }
        counts[set.templates[hit.index].viseme] += 1
        n += 1
      }
      row.push(n === 0 ? '-' : majorityLabel(counts))
    }
    log(`### sent_${i} — "${sentence}"`)
    log('')
    log(row.join(' '))
    log('')
  })

  // 5. Cost: mean extract + classify time per frame over all sentence frames.
  const costPass = () => {
    let n = 0
    const frameBuf = new Float32Array(FRAME_SIZE)
    const out = new Float32Array(MFCC_COEFFS)
    for (const a of sentAnalyses) {
      const ex = createFeatureExtractor(a.sampleRate, FRAME_SIZE)
      for (let start = 0; start + FRAME_SIZE <= a.pcm.length; start += a.step) {
        frameBuf.set(a.pcm.subarray(start, start + FRAME_SIZE))
        ex.extract(frameBuf, out)
        classify(out, set)
        n += 1
      }
    }
    return n
  }
  costPass() // warmup
  const t0 = performance.now()
  const measured = costPass()
  const t1 = performance.now()
  const micros = measured === 0 ? 0 : ((t1 - t0) / measured) * 1000
  log('## Cost')
  log('')
  log(`extract + classify: ${micros.toFixed(1)} µs/frame over ${measured} sentence frames`)

  if (opts.report) {
    writeFileSync(resolve(opts.report), `${lines.join('\n')}\n`)
    console.log(`wrote ${opts.report}`)
  }
}

// ── CLI ─────────────────────────────────────────────────────────────────

function usage() {
  console.error(
    'usage:\n' +
      '  viseme-lab.mjs calibrate --audio <dir> --words <json> --out <file.ts>\n' +
      '  viseme-lab.mjs evaluate --audio <dir> --words <json> --templates <file.ts> [--report <file.md>]',
  )
}

async function main() {
  const cmd = process.argv[2]
  const rest = process.argv.slice(3)
  const opt = (name) => {
    const i = rest.indexOf(name)
    return i >= 0 && i + 1 < rest.length ? rest[i + 1] : undefined
  }
  const audio = opt('--audio')
  const words = opt('--words')
  try {
    if (cmd === 'calibrate') {
      const out = opt('--out')
      if (!audio || !words || !out) {
        usage()
        process.exitCode = 1
        return
      }
      cmdCalibrate({ audio, words, out })
    } else if (cmd === 'evaluate') {
      const templates = opt('--templates')
      const report = opt('--report')
      if (!audio || !words || !templates) {
        usage()
        process.exitCode = 1
        return
      }
      await cmdEvaluate({ audio, words, templates, report })
    } else {
      usage()
      process.exitCode = 1
    }
  } catch (err) {
    console.error(`viseme-lab: error: ${err instanceof Error ? err.message : err}`)
    process.exitCode = 1
  }
}

await main()
