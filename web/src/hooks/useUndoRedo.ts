/** Undo/Redo helpers for creating commands */
import { useCallback } from 'react'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import type { AsrSegment } from '../types/asr-model'
import type { Command } from '../stores/editor-store'

/**
 * Hook providing undoable operations on segments.
 * All mutations go through useModelStore.getState() to avoid stale closures.
 */
export function useUndoableOps() {
  const execute = useEditorStore(s => s.execute)

  /** Undoable text edit */
  const editText = useCallback((id: string, oldText: string, newText: string) => {
    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegment(id, { text: newText }),
      inverse: () => useModelStore.getState().updateSegment(id, { text: oldText }),
      description: `Edit text of ${id}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable speaker change */
  const changeSpeaker = useCallback((id: string, oldSpeaker: string, newSpeaker: string) => {
    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegment(id, { speaker: newSpeaker }),
      inverse: () => useModelStore.getState().updateSegment(id, { speaker: oldSpeaker }),
      description: `Change speaker of ${id}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable emotion change */
  const changeEmotion = useCallback((id: string, oldEmotion: string, newEmotion: string) => {
    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegment(id, { emotion: newEmotion }),
      inverse: () => useModelStore.getState().updateSegment(id, { emotion: oldEmotion }),
      description: `Change emotion of ${id}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable time adjustment */
  const adjustTime = useCallback((id: string, field: 'start_ms' | 'end_ms', oldVal: number, newVal: number) => {
    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegment(id, { [field]: newVal }),
      inverse: () => useModelStore.getState().updateSegment(id, { [field]: oldVal }),
      description: `Adjust ${field} of ${id}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable split: split segment at time position */
  const splitSegment = useCallback((id: string, splitMs: number) => {
    const state = useModelStore.getState()
    if (!state.model) return

    const segments = state.model.segments
    const idx = segments.findIndex(s => s.id === id)
    if (idx < 0) return

    const seg = segments[idx]
    if (splitMs <= seg.start_ms || splitMs >= seg.end_ms) return

    // Generate new IDs
    const newId = `seg_${Math.random().toString(16).slice(2, 10)}`

    // Split text roughly by time ratio
    const ratio = (splitMs - seg.start_ms) / (seg.end_ms - seg.start_ms)
    const splitCharIdx = Math.max(1, Math.round(seg.text.length * ratio))
    const text1 = seg.text.slice(0, splitCharIdx)
    const text2 = seg.text.slice(splitCharIdx)

    const seg1: AsrSegment = {
      ...seg,
      end_ms: splitMs,
      text: text1,
    }
    const seg2: AsrSegment = {
      ...seg,
      id: newId,
      start_ms: splitMs,
      text: text2,
    }

    const newSegments = [...segments]
    newSegments.splice(idx, 1, seg1, seg2)

    const oldSegments = [...segments]

    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegments(newSegments),
      inverse: () => useModelStore.getState().updateSegments(oldSegments),
      description: `Split segment ${id} at ${splitMs}ms`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable merge: merge segment with the next one */
  const mergeWithNext = useCallback((id: string) => {
    const state = useModelStore.getState()
    if (!state.model) return

    const segments = state.model.segments
    const idx = segments.findIndex(s => s.id === id)
    if (idx < 0 || idx >= segments.length - 1) return

    const seg = segments[idx]
    const next = segments[idx + 1]

    const merged: AsrSegment = {
      ...seg,
      end_ms: next.end_ms,
      text: seg.text + next.text,
    }

    const newSegments = [...segments]
    newSegments.splice(idx, 2, merged)

    const oldSegments = [...segments]

    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegments(newSegments),
      inverse: () => useModelStore.getState().updateSegments(oldSegments),
      description: `Merge segments ${id} + ${next.id}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable insert: insert a new segment at given index */
  const insertSegment = useCallback((insertIdx: number, newSeg: AsrSegment) => {
    const state = useModelStore.getState()
    if (!state.model) return

    const oldSegments = [...state.model.segments]
    const newSegments = [...oldSegments]
    newSegments.splice(insertIdx, 0, newSeg)

    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegments(newSegments),
      inverse: () => useModelStore.getState().updateSegments(oldSegments),
      description: `Insert segment at index ${insertIdx}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable delete: remove segment by id */
  const deleteSegment = useCallback((id: string) => {
    const state = useModelStore.getState()
    if (!state.model) return

    const oldSegments = [...state.model.segments]
    const newSegments = oldSegments.filter(s => s.id !== id)

    const cmd: Command = {
      apply: () => useModelStore.getState().updateSegments(newSegments),
      inverse: () => useModelStore.getState().updateSegments(oldSegments),
      description: `Delete segment ${id}`,
    }
    execute(cmd)
  }, [execute])

  return { editText, changeSpeaker, changeEmotion, adjustTime, splitSegment, mergeWithNext, insertSegment, deleteSegment }
}
