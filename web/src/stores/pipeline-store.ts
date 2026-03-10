/** Pipeline Store: phase status, gate status, SSE run, log streaming */
import { create } from 'zustand'
import type { PhaseStatus, GateStatus, StageInfo, PipelineStatusResponse } from '../types/pipeline'
import { PHASE_NAMES } from '../types/pipeline'
import { fetchJson } from '../utils/api'
import { useModelStore } from './model-store'

// ---- Action derivation ----

export type ActionKind = 'start' | 'resume' | 'pass_gate' | 'retry'

export interface WorkflowAction {
  label: string
  kind: ActionKind
  gateKey?: string     // for pass_gate
  fromPhase?: string   // for retry
}

function deriveAction(phases: PhaseStatus[], gates: GateStatus[], episodeStatus?: string): WorkflowAction | null {
  if (phases.some(p => p.status === 'running')) return null

  // Episode already completed (covers cases where early phases have no task records)
  if (episodeStatus === 'succeeded') return null

  // All done
  if (phases.every(p => p.status === 'succeeded' || p.status === 'skipped')
      && gates.every(g => g.status === 'passed')) return null

  // Gate awaiting → pass gate
  const awaitingGate = gates.find(g => g.status === 'awaiting')
  if (awaitingGate) return { label: '继续', kind: 'pass_gate', gateKey: awaitingGate.key }

  // Failed → retry from failed phase
  const failedPhase = phases.find(p => p.status === 'failed')
  if (failedPhase) return { label: '重试', kind: 'retry', fromPhase: failedPhase.name }

  // Some succeeded → resume
  if (phases.some(p => p.status === 'succeeded')) return { label: '继续', kind: 'resume' }

  // Nothing started
  return { label: '开始', kind: 'start' }
}

// ---- Store ----

interface PipelineState {
  phases: PhaseStatus[]
  gates: GateStatus[]
  stages: StageInfo[]
  isRunning: boolean
  logs: string[]
  runError: string | null
  currentAction: WorkflowAction | null

  // Actions
  loadStatus: (drama: string, ep: number) => Promise<void>
  _fetchStatus: (drama: string, ep: number) => Promise<void>
  _startPolling: () => void
  _stopPolling: () => void
  runPipeline: (drama: string, ep: number, fromPhase?: string) => Promise<void>
  passGate: (drama: string, ep: number, gateKey: string) => Promise<void>
  executeAction: (drama: string, ep: number) => Promise<void>
  resetGate: (drama: string, ep: number, gateKey: string) => Promise<void>
  _connectStream: (drama: string, ep: number) => Promise<void>
  cancelRun: () => void
  clearLogs: () => void
}

const defaultPhases: PhaseStatus[] = PHASE_NAMES.map(name => ({
  name,
  label: '',
  status: 'pending' as const,
  started_at: null,
  finished_at: null,
  skipped: false,
  metrics: {},
  error: null,
}))

const defaultGates: GateStatus[] = []
const defaultStages: StageInfo[] = [
  { key: 'extract',   label: '提取', phases: ['extract'],              status: 'pending' },
  { key: 'recognize', label: '识别', phases: ['asr', 'parse'],       status: 'pending' },
  { key: 'translate', label: '翻译', phases: ['translate'],            status: 'pending' },
  { key: 'dub',       label: '配音', phases: ['tts', 'mix'],           status: 'pending' },
  { key: 'compose',   label: '合成', phases: ['burn'],                status: 'pending' },
]

// AbortController for current SSE connection
let _abortController: AbortController | null = null
// Track current drama/ep for re-fetching status
let _currentDrama = ''
let _currentEp = 0
// Polling timer for auto-refresh
let _pollTimer: ReturnType<typeof setInterval> | null = null

// Phases that require voice assignments
const TTS_AND_AFTER = new Set(['tts', 'mix', 'burn'])

/**
 * Pre-flight check: if pipeline will reach TTS, verify all speech speakers
 * have voice_type assigned. Returns error message or null.
 */
function _checkVoiceAssignments(fromPhase?: string): string | null {
  const { cues, roles } = useModelStore.getState()
  if (!cues.length) return null

  // If fromPhase is before TTS (extract/asr/parse/translate), skip check
  if (fromPhase && !TTS_AND_AFTER.has(fromPhase)) return null

  // Build role voice map: role_id → voice_type
  const voiceMap = new Map<number, string>()
  for (const r of roles) {
    voiceMap.set(r.id, r.voice_type || '')
  }

  // Find speech speakers with no voice
  const missing: string[] = []
  const checked = new Set<number>()
  for (const cue of cues) {
    if (cue.kind !== 'speech' || checked.has(cue.speaker)) continue
    checked.add(cue.speaker)
    const vt = voiceMap.get(cue.speaker)
    if (!vt) {
      const role = roles.find(r => r.id === cue.speaker)
      missing.push(role?.name ?? `Speaker #${cue.speaker}`)
    }
  }

  if (missing.length > 0) {
    return `以下角色未分配音色，请先在音色面板中配置：${missing.join('、')}`
  }
  return null
}

/** Reload all model data after pipeline events */
function _reloadModelData(drama: string, ep: number) {
  const ms = useModelStore.getState()
  if (ms.currentDrama === drama && ms.currentEpisode === ep) {
    ms.loadCues(drama, ep)
  }
}

export const usePipelineStore = create<PipelineState>((set, get) => ({
  phases: defaultPhases,
  gates: defaultGates,
  stages: defaultStages,
  isRunning: false,
  logs: [],
  runError: null,
  currentAction: deriveAction(defaultPhases, defaultGates),

  loadStatus: async (drama, ep) => {
    _currentDrama = drama
    _currentEp = ep
    // Reset to all-pending to prevent stale action when switching episodes
    set({
      phases: defaultPhases,
      gates: defaultGates,
      stages: defaultStages,
      currentAction: deriveAction(defaultPhases, defaultGates),
    })
    await get()._fetchStatus(drama, ep)
    // Start polling
    get()._startPolling()
  },

  _fetchStatus: async (drama, ep) => {
    try {
      const data = await fetchJson<PipelineStatusResponse>(
        `/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/status`,
      )
      const gates = data.gates ?? []
      const stages = data.stages ?? []
      set({
        phases: data.phases,
        gates,
        stages,
        currentAction: deriveAction(data.phases, gates, data.episode_status),
      })
    } catch {
      // keep current state on fetch error
    }
  },

  _startPolling: () => {
    if (_pollTimer) clearInterval(_pollTimer)
    _pollTimer = setInterval(() => {
      if (_currentDrama && _currentEp) {
        get()._fetchStatus(_currentDrama, _currentEp)
      }
    }, 2000)
  },

  _stopPolling: () => {
    if (_pollTimer) {
      clearInterval(_pollTimer)
      _pollTimer = null
    }
  },

  runPipeline: async (drama, ep, fromPhase?) => {
    // Pre-flight: check voice assignments before reaching TTS
    const voiceError = _checkVoiceAssignments(fromPhase)
    if (voiceError) {
      set({ runError: voiceError })
      return
    }

    // Only abort SSE, do NOT send cancel (would race with the run request)
    if (_abortController) {
      _abortController.abort()
      _abortController = null
    }

    _currentDrama = drama
    _currentEp = ep

    set({ isRunning: true, logs: [], runError: null })

    const body: Record<string, string> = {}
    if (fromPhase) body.from_phase = fromPhase

    // Step 1: Submit pipeline tasks to DB
    try {
      const res = await fetch(
        `/api/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/run`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
      )
      if (!res.ok) {
        const text = await res.text()
        set({ isRunning: false, runError: `API ${res.status}: ${text}` })
        return
      }
    } catch (err) {
      set({ runError: (err as Error).message, isRunning: false })
      return
    }

    // Step 2: Connect SSE stream
    await get()._connectStream(drama, ep)
  },

  passGate: async (drama, ep, gateKey) => {
    // Only abort SSE, do NOT cancel pipeline (would delete the gate task)
    if (_abortController) {
      _abortController.abort()
      _abortController = null
    }

    _currentDrama = drama
    _currentEp = ep
    set({ isRunning: true, logs: [], runError: null })

    // Step 1: Pass the gate (backend reactor creates next task)
    try {
      const res = await fetch(
        `/api/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/gate/${encodeURIComponent(gateKey)}/pass`,
        { method: 'POST' },
      )
      if (!res.ok) {
        const text = await res.text()
        set({ isRunning: false, runError: `API ${res.status}: ${text}` })
        return
      }
    } catch (err) {
      set({ runError: (err as Error).message, isRunning: false })
      return
    }

    // Step 2: Connect SSE stream to watch progress (no submit needed, reactor already enqueued)
    await get()._connectStream(drama, ep)
  },

  executeAction: async (drama, ep) => {
    const action = get().currentAction
    if (!action) return
    switch (action.kind) {
      case 'pass_gate':
        if (action.gateKey) await get().passGate(drama, ep, action.gateKey)
        break
      case 'retry':
        await get().runPipeline(drama, ep, action.fromPhase)
        break
      case 'start':
      case 'resume':
        await get().runPipeline(drama, ep)
        break
    }
  },

  resetGate: async (drama, ep, gateKey) => {
    try {
      const res = await fetch(
        `/api/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/gate/${encodeURIComponent(gateKey)}/reset`,
        { method: 'POST' },
      )
      if (!res.ok) {
        const text = await res.text()
        set({ runError: `API ${res.status}: ${text}` })
      }
    } catch (err) {
      set({ runError: (err as Error).message })
    }
    await get()._fetchStatus(drama, ep)
  },

  _connectStream: async (drama, ep) => {
    _abortController = new AbortController()
    const { signal } = _abortController

    try {
      const res = await fetch(
        `/api/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/stream`,
        { signal },
      )

      if (!res.ok) {
        const text = await res.text()
        set({ isRunning: false, runError: `SSE ${res.status}: ${text}` })
        return
      }

      const reader = res.body?.getReader()
      if (!reader) {
        set({ isRunning: false, runError: 'No response stream' })
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        let currentEvent = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7)
          } else if (line.startsWith('data: ')) {
            const dataStr = line.slice(6)
            try {
              const data = JSON.parse(dataStr)

              if (currentEvent === 'error') {
                set({ runError: data.message, isRunning: false })
                await get().loadStatus(drama, ep)
                return
              }

              if (currentEvent === 'gate_awaiting') {
                await get().loadStatus(drama, ep)
                _reloadModelData(drama, ep)
                set({ isRunning: false })
                return
              }

              if (currentEvent.startsWith('pipeline_')) {
                const result = currentEvent.replace('pipeline_', '')
                if (result === 'failed') {
                  set({ runError: data.error || 'Pipeline failed' })
                }
                set({ isRunning: false })
                await get().loadStatus(drama, ep)
                _reloadModelData(drama, ep)
                return
              }

              // Task status events
              if (data.type) {
                await get()._fetchStatus(drama, ep)
              }
            } catch {
              // Ignore malformed JSON
            }
            currentEvent = ''
          }
        }
      }

      set({ isRunning: false })
      await get().loadStatus(drama, ep)
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        set(s => ({ logs: [...s.logs, 'Pipeline stopped'], isRunning: false }))
      } else {
        set({ runError: (err as Error).message, isRunning: false })
      }
      get().loadStatus(drama, ep)
    } finally {
      _abortController = null
    }
  },

  cancelRun: () => {
    if (_abortController) {
      _abortController.abort()
      _abortController = null
    }
    // Also tell backend to kill the process
    if (_currentDrama && _currentEp) {
      fetch(
        `/api/episodes/${encodeURIComponent(_currentDrama)}/${encodeURIComponent(_currentEp)}/pipeline/cancel`,
        { method: 'POST' },
      ).catch(() => {})
    }
    set({ isRunning: false })
  },

  clearLogs: () => set({ logs: [], runError: null }),
}))
