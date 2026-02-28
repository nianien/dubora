/** Pipeline status types — mirrors backend manifest phase data */

export interface PhaseStatus {
  name: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'
  started_at: string | null
  finished_at: string | null
  skipped: boolean
  metrics: Record<string, unknown>
  error: { type: string; message: string; traceback?: string } | null
}

export interface PipelineStatusResponse {
  has_manifest: boolean
  phases: PhaseStatus[]
}

export const PHASE_NAMES = [
  'demux', 'sep', 'asr', 'sub', 'mt', 'align', 'tts', 'mix', 'burn',
] as const

export type PhaseName = (typeof PHASE_NAMES)[number]

// ---- Workflow types ----

export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'calibrating'

export interface WorkflowStep {
  key: string
  label: string
  phases: PhaseName[]
  isCalibration: boolean
  status: StepStatus
}

export const WORKFLOW_STEPS_DEF: Omit<WorkflowStep, 'status'>[] = [
  { key: 'ingest', label: '开始', phases: ['demux', 'sep', 'asr', 'sub'], isCalibration: false },
  { key: 'calib1', label: '校准', phases: [],                      isCalibration: true },
  { key: 'mt',     label: '翻译', phases: ['mt', 'align'],         isCalibration: false },
  { key: 'calib2', label: '校准', phases: [],                      isCalibration: true },
  { key: 'tts',    label: '成片', phases: ['tts', 'mix', 'burn'],  isCalibration: false },
]
