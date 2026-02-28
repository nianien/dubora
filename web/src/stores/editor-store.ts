/** Editor Store: UI layer (selection, playback, undo/redo) */
import { create } from 'zustand'

/** Undo/Redo command */
export interface Command {
  apply: () => void
  inverse: () => void
  description: string
}

interface EditorState {
  // selection
  selectedSegmentId: string | null
  selectSegment: (id: string | null) => void

  // playback tracking (separate from user selection)
  playingSegmentId: string | null
  setPlayingSegment: (id: string | null) => void

  // playback
  currentTime: number      // current playback time in ms
  duration: number         // video duration in ms
  isPlaying: boolean
  setCurrentTime: (ms: number) => void
  setDuration: (ms: number) => void
  setPlaying: (playing: boolean) => void

  // undo/redo
  undoStack: Command[]
  redoStack: Command[]
  execute: (cmd: Command) => void
  undo: () => void
  redo: () => void
  canUndo: () => boolean
  canRedo: () => boolean

  // timeline zoom (pixels per second)
  zoom: number
  setZoom: (zoom: number) => void

  // timeline scroll offset (ms)
  scrollOffset: number
  setScrollOffset: (offset: number) => void

  // reset all state (on episode switch)
  reset: () => void
}

export const useEditorStore = create<EditorState>((set, get) => ({
  selectedSegmentId: null,
  selectSegment: (id) => set({ selectedSegmentId: id }),

  playingSegmentId: null,
  setPlayingSegment: (id) => set({ playingSegmentId: id }),

  currentTime: 0,
  duration: 0,
  isPlaying: false,
  setCurrentTime: (ms) => set({ currentTime: ms }),
  setDuration: (ms) => set({ duration: ms }),
  setPlaying: (playing) => set({ isPlaying: playing }),

  undoStack: [],
  redoStack: [],

  execute: (cmd) => {
    cmd.apply()
    set(state => ({
      undoStack: [...state.undoStack, cmd],
      redoStack: [],
    }))
  },

  undo: () => {
    const { undoStack } = get()
    if (undoStack.length === 0) return
    const cmd = undoStack[undoStack.length - 1]
    cmd.inverse()
    set(state => ({
      undoStack: state.undoStack.slice(0, -1),
      redoStack: [...state.redoStack, cmd],
    }))
  },

  redo: () => {
    const { redoStack } = get()
    if (redoStack.length === 0) return
    const cmd = redoStack[redoStack.length - 1]
    cmd.apply()
    set(state => ({
      redoStack: state.redoStack.slice(0, -1),
      undoStack: [...state.undoStack, cmd],
    }))
  },

  canUndo: () => get().undoStack.length > 0,
  canRedo: () => get().redoStack.length > 0,

  zoom: 50,  // 50 px per second
  setZoom: (zoom) => set({ zoom: Math.max(10, Math.min(200, zoom)) }),

  scrollOffset: 0,
  setScrollOffset: (offset) => set({ scrollOffset: Math.max(0, offset) }),

  reset: () => set({
    selectedSegmentId: null,
    playingSegmentId: null,
    currentTime: 0,
    duration: 0,
    isPlaying: false,
    scrollOffset: 0,
    undoStack: [],
    redoStack: [],
  }),
}))
