/** Model Store: data layer (segments CRUD, dirty, save) */
import { create } from 'zustand'
import type { AsrModel, AsrSegment, Episode, Roles } from '../types/asr-model'
import { fetchJson, putJson, postJson } from '../utils/api'
import type { ExportResult } from '../types/asr-model'
import { usePipelineStore } from './pipeline-store'
import { useEditorStore } from './editor-store'

interface ModelState {
  // episode selection
  episodes: Episode[]
  currentDrama: string
  currentEpisode: string
  videoFile: string    // relative path to video e.g. "东北雀神风云/2.mp4"

  // model data
  model: AsrModel | null
  loading: boolean
  error: string | null
  dirty: boolean

  // roles (voice mapping)
  roles: Roles | null

  // emotions config: [{key, name, lang, disabled?}, ...]
  emotions: { key: string; name: string; lang: string[]; disabled?: boolean }[]

  // actions
  loadEpisodes: () => Promise<void>
  selectEpisode: (drama: string, episode: string) => Promise<void>
  loadModel: (drama: string, episode: string) => Promise<void>
  saveModel: () => Promise<void>
  exportModel: () => Promise<ExportResult>
  loadRoles: (drama: string) => Promise<void>
  saveRoles: () => Promise<void>
  loadEmotions: () => Promise<void>

  // segment mutations
  updateSegment: (id: string, patch: Partial<AsrSegment>) => void
  updateSegments: (segments: AsrSegment[]) => void
  setDirty: (dirty: boolean) => void
}

export const useModelStore = create<ModelState>((set, get) => ({
  episodes: [],
  currentDrama: '',
  currentEpisode: '',
  videoFile: '',
  model: null,
  loading: false,
  error: null,
  dirty: false,
  roles: null,
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
      model: null,
      dirty: false,
      error: null,
    })
    // Reset editor state (timeline scroll, selection, undo stack, etc.)
    useEditorStore.getState().reset()
    await Promise.all([
      get().loadModel(drama, episode),
      get().loadRoles(drama),
      usePipelineStore.getState().loadStatus(drama, episode),
    ])
  },

  loadModel: async (drama, episode) => {
    set({ loading: true, error: null })
    try {
      const model = await fetchJson<AsrModel>(`/episodes/${encodeURIComponent(drama)}/${encodeURIComponent(episode)}/asr-model`)
      set({ model, loading: false, dirty: false })
    } catch (e) {
      set({ model: null, error: (e as Error).message, loading: false, dirty: false })
    }
  },

  saveModel: async () => {
    const { model, currentDrama, currentEpisode } = get()
    if (!model) return
    set({ loading: true, error: null })
    try {
      const updated = await putJson<AsrModel>(
        `/episodes/${encodeURIComponent(currentDrama)}/${encodeURIComponent(currentEpisode)}/asr-model`,
        model,
      )
      set({ model: updated, loading: false, dirty: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  exportModel: async () => {
    const { currentDrama, currentEpisode } = get()
    return postJson<ExportResult>(
      `/episodes/${encodeURIComponent(currentDrama)}/${encodeURIComponent(currentEpisode)}/export`,
    )
  },

  loadRoles: async (drama) => {
    try {
      const data = await fetchJson<Roles>(`/episodes/${encodeURIComponent(drama)}/roles`)
      set({ roles: data })
    } catch {
      set({ roles: null })
    }
  },

  saveRoles: async () => {
    const { roles, currentDrama } = get()
    if (!roles || !currentDrama) return
    try {
      const updated = await putJson<Roles>(
        `/episodes/${encodeURIComponent(currentDrama)}/roles`,
        roles,
      )
      set({ roles: updated })
    } catch (e) {
      set({ error: (e as Error).message })
    }
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

  updateSegment: (id, patch) => {
    const { model } = get()
    if (!model) return
    const segments = model.segments.map(seg =>
      seg.id === id ? { ...seg, ...patch } : seg
    )
    set({ model: { ...model, segments }, dirty: true })
  },

  updateSegments: (segments) => {
    const { model } = get()
    if (!model) return
    set({ model: { ...model, segments }, dirty: true })
  },

  setDirty: (dirty) => set({ dirty }),
}))
