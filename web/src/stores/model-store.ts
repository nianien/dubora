/** Model Store: data layer (cues CRUD, dirty, save) */
import { create } from 'zustand'
import type { Episode, Role, Cue, Utterance } from '../types/asr-model'
import { fetchJson, putJson } from '../utils/api'
import { usePipelineStore } from './pipeline-store'
import { useEditorStore } from './editor-store'

// Auto-save: debounce 2s after last mutation — saves cues to DB
let _autoSaveTimer: ReturnType<typeof setTimeout> | null = null
function scheduleAutoSave() {
  if (_autoSaveTimer) clearTimeout(_autoSaveTimer)
  _autoSaveTimer = setTimeout(() => {
    _autoSaveTimer = null
    const { dirty, saveCues, cues } = useModelStore.getState()
    if (dirty && cues.length > 0) {
      saveCues()
    }
  }, 2000)
}

interface ModelState {
  // episode selection
  episodes: Episode[]
  currentDrama: string
  currentEpisode: number
  videoFile: string    // relative path to video e.g. "东北雀神风云/2.mp4"

  // data state
  loaded: boolean
  loading: boolean
  error: string | null
  dirty: boolean

  // cues (DB-backed SRC cues with text_en from MT)
  cues: Cue[]

  // utterances (from DB, for pipeline display only)
  utterances: Utterance[]

  // roles (voice mapping, array of Role objects)
  roles: Role[]

  // emotions config: [{key, name, lang, disabled?}, ...]
  emotions: { key: string; name: string; lang: string[]; disabled?: boolean }[]

  // actions
  loadEpisodes: () => Promise<void>
  selectEpisode: (drama: string, episode: number) => Promise<void>
  loadCues: (drama: string, episode: number) => Promise<void>
  saveCues: () => Promise<void>
  loadUtterances: (drama: string, episode: number) => Promise<void>
  loadRoles: (drama: string) => Promise<void>
  saveRoles: () => Promise<void>
  loadEmotions: () => Promise<void>

  // role mutations
  updateRoles: (roles: Role[]) => Promise<void>

  // cue mutations
  updateCue: (id: number, patch: Partial<Cue>) => void
  updateCues: (cues: Cue[]) => void
  setDirty: (dirty: boolean) => void
}

export const useModelStore = create<ModelState>((set, get) => ({
  episodes: [],
  currentDrama: '',
  currentEpisode: 0,
  videoFile: '',
  loaded: false,
  loading: false,
  error: null,
  dirty: false,
  cues: [],
  utterances: [],
  roles: [],
  emotions: [],

  loadEpisodes: async () => {
    try {
      const episodes = await fetchJson<Episode[]>('/episodes')
      set({ episodes })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  selectEpisode: async (drama, episode) => {
    // Find the video_file from episodes list
    const ep = get().episodes.find(e => e.drama === drama && e.episode === episode)
    set({
      currentDrama: drama,
      currentEpisode: episode,
      videoFile: ep?.video_file ?? '',
      loaded: false,
      cues: [],
      utterances: [],
      dirty: false,
      error: null,
    })
    // Reset editor state (timeline scroll, selection, undo stack, etc.)
    useEditorStore.getState().reset()

    // Load all data
    await Promise.all([
      get().loadCues(drama, episode),
      get().loadUtterances(drama, episode),
      get().loadRoles(drama),
      usePipelineStore.getState().loadStatus(drama, episode),
    ])
  },

  loadCues: async (drama, episode) => {
    try {
      const data = await fetchJson<{ cues: Cue[] }>(
        `/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(episode)}/cues`,
      )
      set({ cues: data.cues ?? [], loaded: true })
    } catch {
      set({ cues: [], loaded: true })
    }
  },

  saveCues: async () => {
    const { cues, currentDrama, currentEpisode } = get()
    if (!cues.length) return
    try {
      const data = await putJson<{ cues: Cue[] }>(
        `/episodes/${encodeURIComponent(currentDrama)}/${encodeURIComponent(currentEpisode)}/cues`,
        { cues },
      )
      set({ cues: data.cues ?? cues, dirty: false })
      // Refresh pipeline status (diff_and_save may have reset a gate)
      usePipelineStore.getState()._fetchStatus(currentDrama, currentEpisode)
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  loadUtterances: async (drama, episode) => {
    try {
      const data = await fetchJson<{ utterances: Utterance[] }>(
        `/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(episode)}/utterances`,
      )
      set({ utterances: data.utterances ?? [] })
    } catch {
      set({ utterances: [] })
    }
  },

  loadRoles: async (drama) => {
    try {
      const data = await fetchJson<{ roles: Role[] }>(`/episodes/${encodeURIComponent(drama)}/roles`)
      set({ roles: data.roles ?? [] })
    } catch {
      set({ roles: [] })
    }
  },

  saveRoles: async () => {
    const { roles, currentDrama } = get()
    if (!currentDrama) return
    try {
      const updated = await putJson<{ roles: Role[] }>(
        `/episodes/${encodeURIComponent(currentDrama)}/roles`,
        { roles },
      )
      set({ roles: updated.roles ?? roles })
    } catch (e) {
      set({ error: (e as Error).message })
    }
  },

  updateRoles: async (roles) => {
    set({ roles })
    await get().saveRoles()
  },

  loadEmotions: async () => {
    try {
      const all = await fetchJson<{ key: string; name: string; lang: string[]; disabled?: boolean }[]>('/emotions')
      set({ emotions: all.filter(e => !e.disabled) })
    } catch {
      set({ emotions: [
        { key: 'neutral', name: '中性', lang: ['zh', 'en'] },
        { key: 'happy', name: '开心', lang: ['zh', 'en'] },
      ] })
    }
  },

  updateCue: (id, patch) => {
    const cues = get().cues.map(c => c.id === id ? { ...c, ...patch } : c)
    set({ cues, dirty: true })
    scheduleAutoSave()
  },

  updateCues: (cues) => {
    set({ cues, dirty: true })
    scheduleAutoSave()
  },

  setDirty: (dirty) => set({ dirty }),
}))
