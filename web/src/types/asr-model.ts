/** ASR Model TypeScript types — DB-backed Cue is the single source of truth */

export interface TTSPolicy {
  max_rate: number
  allow_extend_ms: number
}

/** Cue: DB-backed atomic unit, independent of utterances */
export interface Cue {
  id: number
  episode_id: number
  text: string
  text_en?: string      // MT fills this on SRC cues
  start_ms: number
  end_ms: number
  speaker: number       // roles.id FK
  emotion: string
  gender?: string | null
  kind: 'speech' | 'singing'
}

/** Utterance: self-contained group computed from SRC cues + TTS cache */
export interface Utterance {
  id: number
  episode_id: number
  text_cn: string
  text_en: string
  start_ms: number
  end_ms: number
  speaker: number       // roles.id FK
  emotion: string
  gender?: string | null
  kind: string
  source_hash?: string | null
  voice_hash?: string | null
  tts_policy?: TTSPolicy | null
  audio_path?: string | null
  tts_duration_ms?: number | null
  tts_rate?: number | null
  tts_error?: string | null
}

/** Role: per-drama voice assignment entity */
export interface Role {
  id: number
  name: string
  voice_type: string
  role_type: string   // 'lead' | 'supporting' | 'extra' | 'narrator'
}

/** Paginated API response envelope */
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/** Drama: aggregated drama info from API */
export interface Drama {
  id: number
  name: string
  synopsis: string | null
  cover_image: string | null
  total_episodes: number
  episode_count: number
  updated_at: string | null
}

export type EpisodeStatus = 'ready' | 'running' | 'succeeded' | 'failed' | 'review'

export interface Episode {
  id: number
  drama: string
  drama_id: number
  episode: number
  path: string
  status: EpisodeStatus
  updated_at: string | null
  video_file: string
  has_asr_result: boolean
  has_asr_model: boolean
  dubbed_video: boolean
  subtitle_file: boolean
}
