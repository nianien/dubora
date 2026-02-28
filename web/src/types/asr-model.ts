/** ASR Model TypeScript types — mirrors Python schema/asr_model.py */

export interface AsrSegmentFlags {
  overlap: boolean
  needs_review: boolean
}

export interface TTSPolicy {
  max_rate: number
  allow_extend_ms: number
}

export interface AsrSegment {
  id: string
  start_ms: number
  end_ms: number
  text: string
  text_en: string
  speaker: string
  emotion: string
  type: 'speech' | 'singing'
  gender?: string | null
  tts_policy?: TTSPolicy | null
  flags: AsrSegmentFlags
}

export interface AsrMediaInfo {
  duration_ms: number
}

export interface AsrHistory {
  rev: number
  created_at: string
  updated_at: string
}

export interface AsrFingerprint {
  algo: string
  value: string
  scope: string
}

export interface AsrModel {
  schema: string
  media: AsrMediaInfo
  segments: AsrSegment[]
  history: AsrHistory
  fingerprint: AsrFingerprint
}

export interface Roles {
  roles: Record<string, string>          // role_id → voice_type
  default_roles: Record<string, string>  // gender → role_id
}

export interface Episode {
  drama: string
  episode: string
  video_file: string
  has_asr_result: boolean
  has_asr_model: boolean
  has_subtitle_model: boolean
}

export interface ExportResult {
  status: string
  exported: string[]
  segments: number
  utterances: number
}
