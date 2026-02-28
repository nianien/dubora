/** Pipeline status types — mirrors backend manifest data */

export interface PhaseStatus {
  name: string
  label: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'
  started_at: string | null
  finished_at: string | null
  skipped: boolean
  metrics: Record<string, unknown>
  error: { type: string; message: string; traceback?: string } | null
}

export interface GateStatus {
  key: string
  after: string        // phase name after which this gate sits
  label: string
  status: 'pending' | 'awaiting' | 'passed'
}

export interface StageInfo {
  key: string
  label: string
  phases: string[]
  status: 'pending' | 'running' | 'succeeded' | 'failed'
}

export interface PipelineStatusResponse {
  has_manifest: boolean
  phases: PhaseStatus[]
  gates: GateStatus[]
  stages: StageInfo[]
}

export const PHASE_NAMES = [
  'extract', 'asr', 'parse',
  'mt', 'align',
  'tts', 'mix', 'burn',
] as const

export type PhaseName = (typeof PHASE_NAMES)[number]
