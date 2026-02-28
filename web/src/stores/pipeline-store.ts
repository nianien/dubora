/** Pipeline Store: phase status, SSE run, log streaming, workflow derivation */
import { create } from 'zustand'
import type { PhaseStatus, PipelineStatusResponse, WorkflowStep, StepStatus } from '../types/pipeline'
import { PHASE_NAMES, WORKFLOW_STEPS_DEF } from '../types/pipeline'
import { fetchJson } from '../utils/api'

// ---- Workflow derivation ----

function deriveStepStatus(
  stepDef: typeof WORKFLOW_STEPS_DEF[number],
  stepIndex: number,
  phases: PhaseStatus[],
  allStepDefs: typeof WORKFLOW_STEPS_DEF,
): StepStatus {
  const phaseMap = new Map(phases.map(p => [p.name, p]))

  if (stepDef.isCalibration) {
    // Find the previous non-calibration step
    let prevDone = false
    for (let i = stepIndex - 1; i >= 0; i--) {
      const prev = allStepDefs[i]
      if (!prev.isCalibration && prev.phases.length > 0) {
        prevDone = prev.phases.every(pn => {
          const p = phaseMap.get(pn)
          return p && (p.status === 'succeeded' || p.status === 'skipped')
        })
        break
      }
    }

    // Find the next non-calibration step
    let nextStarted = false
    for (let i = stepIndex + 1; i < allStepDefs.length; i++) {
      const next = allStepDefs[i]
      if (!next.isCalibration && next.phases.length > 0) {
        nextStarted = next.phases.some(pn => {
          const p = phaseMap.get(pn)
          return p && p.status !== 'pending'
        })
        break
      }
    }

    if (prevDone && !nextStarted) return 'calibrating'
    if (prevDone && nextStarted) return 'done'
    return 'pending'
  }

  // Non-calibration step
  const stepPhases = stepDef.phases.map(pn => phaseMap.get(pn)).filter(Boolean) as PhaseStatus[]
  if (stepPhases.length === 0) return 'pending'

  if (stepPhases.some(p => p.status === 'failed')) return 'failed'
  if (stepPhases.some(p => p.status === 'running')) return 'running'
  if (stepPhases.every(p => p.status === 'succeeded' || p.status === 'skipped')) return 'done'
  return 'pending'
}

function deriveWorkflowSteps(phases: PhaseStatus[]): WorkflowStep[] {
  return WORKFLOW_STEPS_DEF.map((def, i) => ({
    ...def,
    status: deriveStepStatus(def, i, phases, WORKFLOW_STEPS_DEF),
  }))
}

export interface WorkflowAction {
  label: string
  fromPhase: string
  toPhase: string
}

function deriveAction(steps: WorkflowStep[]): WorkflowAction | null {
  // If any step is running, action is cancel (handled separately via isRunning)
  if (steps.some(s => s.status === 'running')) return null

  // All done
  if (steps.every(s => s.status === 'done')) return null

  // If all pending -> "开始" (demux->sub)
  if (steps.every(s => s.status === 'pending')) {
    return { label: '开始', fromPhase: 'demux', toPhase: 'sub' }
  }

  // calib1 calibrating -> "继续" (mt->align)
  const calib1 = steps.find(s => s.key === 'calib1')
  if (calib1?.status === 'calibrating') {
    return { label: '继续', fromPhase: 'mt', toPhase: 'align' }
  }

  // calib2 calibrating -> "继续" (tts->burn)
  const calib2 = steps.find(s => s.key === 'calib2')
  if (calib2?.status === 'calibrating') {
    return { label: '继续', fromPhase: 'tts', toPhase: 'burn' }
  }

  // If any step failed, allow re-run from that step to end of its segment
  const failedStep = steps.find(s => s.status === 'failed' && !s.isCalibration)
  if (failedStep && failedStep.phases.length > 0) {
    const failedIdx = steps.indexOf(failedStep)
    // Find last phase before next calibration point (or end)
    let toPhase = failedStep.phases[failedStep.phases.length - 1]
    for (let i = failedIdx + 1; i < steps.length; i++) {
      if (steps[i].isCalibration) break
      if (steps[i].phases.length > 0) {
        toPhase = steps[i].phases[steps[i].phases.length - 1]
      }
    }
    return { label: '重试', fromPhase: failedStep.phases[0], toPhase }
  }

  return null
}

// ---- Store ----

interface PipelineState {
  phases: PhaseStatus[]
  isRunning: boolean
  logs: string[]
  runError: string | null

  // Workflow derivations
  steps: WorkflowStep[]
  currentAction: WorkflowAction | null
  selectedStepKey: string | null

  // Actions
  loadStatus: (drama: string, ep: string) => Promise<void>
  runPipeline: (drama: string, ep: string, opts: {
    from_phase: string
    to_phase: string
  }) => Promise<void>
  cancelRun: () => void
  clearLogs: () => void
  selectStep: (stepKey: string) => void
}

const defaultPhases: PhaseStatus[] = PHASE_NAMES.map(name => ({
  name,
  status: 'pending',
  started_at: null,
  finished_at: null,
  skipped: false,
  metrics: {},
  error: null,
}))

const defaultSteps = deriveWorkflowSteps(defaultPhases)

// AbortController for current SSE connection
let _abortController: AbortController | null = null
// Track current drama/ep for re-fetching status
let _currentDrama = ''
let _currentEp = ''

export const usePipelineStore = create<PipelineState>((set, get) => ({
  phases: defaultPhases,
  isRunning: false,
  logs: [],
  runError: null,
  steps: defaultSteps,
  currentAction: deriveAction(defaultSteps),
  selectedStepKey: null,

  loadStatus: async (drama, ep) => {
    _currentDrama = drama
    _currentEp = ep
    try {
      const data = await fetchJson<PipelineStatusResponse>(
        `/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/status`,
      )
      const steps = deriveWorkflowSteps(data.phases)
      set({
        phases: data.phases,
        steps,
        currentAction: deriveAction(steps),
        selectedStepKey: null,
      })
    } catch {
      set({
        phases: defaultPhases,
        steps: defaultSteps,
        currentAction: deriveAction(defaultSteps),
      })
    }
  },

  runPipeline: async (drama, ep, opts) => {
    // Cancel any existing run
    get().cancelRun()

    _currentDrama = drama
    _currentEp = ep

    set({ isRunning: true, logs: [], runError: null, selectedStepKey: null })

    _abortController = new AbortController()
    const { signal } = _abortController

    try {
      const res = await fetch(
        `/api/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(ep)}/pipeline/run-stream`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            from_phase: opts.from_phase,
            to_phase: opts.to_phase,
          }),
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

  selectStep: (stepKey: string) => {
    const steps = get().steps
    const step = steps.find(s => s.key === stepKey)
    if (!step || step.status !== 'done') return

    if (step.isCalibration) {
      // Calibration step: re-enter calibration, run from next execution step to end
      const stepIndex = steps.indexOf(step)
      const newSteps = steps.map((s, i) =>
        i === stepIndex ? { ...s, status: 'calibrating' as StepStatus } : s
      )
      set({
        steps: newSteps,
        currentAction: deriveAction(newSteps),
        selectedStepKey: stepKey,
      })
    } else {
      // Execution step: re-run from this step's first phase to burn
      set({
        selectedStepKey: stepKey,
        currentAction: { label: '重新执行', fromPhase: step.phases[0], toPhase: 'burn' },
      })
    }
  },
}))
