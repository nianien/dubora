/** Pipeline Store: phase status, gate status, SSE run, log streaming */
import { create } from 'zustand'
import type { PhaseStatus, GateStatus, StageInfo, PipelineStatusResponse } from '../types/pipeline'
import { PHASE_NAMES } from '../types/pipeline'
import { fetchJson } from '../utils/api'

// ---- Action derivation ----

export interface WorkflowAction {
  label: string
}

function deriveAction(phases: PhaseStatus[], gates: GateStatus[]): WorkflowAction | null {
  if (phases.some(p => p.status === 'running')) return null
  if (phases.every(p => p.status === 'succeeded' || p.status === 'skipped')
      && gates.every(g => g.status === 'passed')) return null
  if (gates.some(g => g.status === 'awaiting')) return { label: '继续' }
  if (phases.some(p => p.status === 'failed')) return { label: '重试' }
  return { label: '开始' }
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
  loadStatus: (drama: string, ep: string) => Promise<void>
  runPipeline: (drama: string, ep: string, fromPhase?: string) => Promise<void>
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
  { key: 'extract',   label: '提取', phases: ['extract'],        status: 'pending' },
  { key: 'recognize', label: '识别', phases: ['asr', 'parse', 'reseg'], status: 'pending' },
  { key: 'translate', label: '翻译', phases: ['mt', 'align'],    status: 'pending' },
  { key: 'dub',       label: '配音', phases: ['tts', 'mix'],     status: 'pending' },
  { key: 'compose',   label: '合成', phases: ['burn'],           status: 'pending' },
]

// AbortController for current SSE connection
let _abortController: AbortController | null = null
// Track current drama/ep for re-fetching status
let _currentDrama = ''
let _currentEp = ''

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
        currentAction: deriveAction(data.phases, gates),
      })
    } catch {
      set({
        phases: defaultPhases,
        gates: defaultGates,
        stages: defaultStages,
        currentAction: deriveAction(defaultPhases, defaultGates),
      })
    }
  },

  runPipeline: async (drama, ep, fromPhase?) => {
    // Cancel any existing run
    get().cancelRun()

    _currentDrama = drama
    _currentEp = ep

    set({ isRunning: true, logs: [], runError: null })

    _abortController = new AbortController()
    const { signal } = _abortController

    const body: Record<string, string> = {}
    if (fromPhase) body.from_phase = fromPhase

    try {
      const res = await fetch(
        `/api/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/run-stream`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal,
        },
      )

      if (!res.ok) {
        const text = await res.text()
        set({ isRunning: false, runError: `API ${res.status}: ${text}` })
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
        // Keep incomplete last line in buffer
        buffer = lines.pop() ?? ''

        let currentEvent = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7)
          } else if (line.startsWith('data: ')) {
            const dataStr = line.slice(6)
            try {
              const data = JSON.parse(dataStr)
              switch (currentEvent) {
                case 'log':
                  set(s => ({ logs: [...s.logs, data.line] }))
                  break
                case 'phase':
                  // Re-fetch manifest status to update chips
                  get().loadStatus(drama, ep)
                  break
                case 'done':
                  // Final status refresh
                  await get().loadStatus(drama, ep)
                  if (data.returncode !== 0) {
                    set({ runError: `Pipeline exited with code ${data.returncode}` })
                  }
                  set({ isRunning: false })
                  return
                case 'error':
                  set({ runError: data.message, isRunning: false })
                  // Refresh status on error too
                  await get().loadStatus(drama, ep)
                  return
              }
            } catch {
              // Ignore malformed JSON
            }
            currentEvent = ''
          }
        }
      }

      // Stream ended without explicit done event
      set({ isRunning: false })
      await get().loadStatus(drama, ep)
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        set(s => ({ logs: [...s.logs, 'Pipeline cancelled'], isRunning: false }))
      } else {
        set({ runError: (err as Error).message, isRunning: false })
      }
      // Refresh status
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
