/** Undo/Redo helpers for creating commands */
import { useCallback } from 'react'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import type { Cue } from '../types/asr-model'
import type { Command } from '../stores/editor-store'

import { nextTempId } from '../utils/temp-id'

/**
 * Hook providing undoable operations on cues.
 * All mutations go through useModelStore.getState() to avoid stale closures.
 */
export function useUndoableOps() {
  const execute = useEditorStore(s => s.execute)

  /** Generic undoable field update */
  const updateField = useCallback((id: number, field: string, oldVal: unknown, newVal: unknown) => {
    if (oldVal === newVal) return
    const cmd: Command = {
      apply: () => useModelStore.getState().updateCue(id, { [field]: newVal }),
      inverse: () => useModelStore.getState().updateCue(id, { [field]: oldVal }),
      description: `Change ${field} of ${id}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable text edit */
  const editText = useCallback((id: number, oldText: string, newText: string) => {
    updateField(id, 'text', oldText, newText)
  }, [updateField])

  /** Undoable speaker change */
  const changeSpeaker = useCallback((id: number, oldSpeaker: number, newSpeaker: number) => {
    updateField(id, 'speaker', oldSpeaker, newSpeaker)
  }, [updateField])

  /** Undoable emotion change */
  const changeEmotion = useCallback((id: number, oldEmotion: string, newEmotion: string) => {
    updateField(id, 'emotion', oldEmotion, newEmotion)
  }, [updateField])

  /** Undoable time adjustment */
  const adjustTime = useCallback((id: number, field: 'start_ms' | 'end_ms', oldVal: number, newVal: number) => {
    updateField(id, field, oldVal, newVal)
  }, [updateField])

  /** Undoable split: split cue at time position */
  const splitCue = useCallback((id: number, splitMs: number) => {
    const state = useModelStore.getState()
    const cues = state.cues
    const idx = cues.findIndex(c => c.id === id)
    if (idx < 0) return

    const cue = cues[idx]
    if (splitMs <= cue.start_ms || splitMs >= cue.end_ms) return

    // Split text at nearest punctuation to time-ratio position
    const ratio = (splitMs - cue.start_ms) / (cue.end_ms - cue.start_ms)
    const targetIdx = Math.round(cue.text.length * ratio)
    const punctRe = /[，。！？、；：,.\!\?;:]/
    let bestIdx = -1
    for (let d = 0; d < cue.text.length; d++) {
      if (targetIdx + d < cue.text.length && punctRe.test(cue.text[targetIdx + d])) {
        bestIdx = targetIdx + d; break
      }
      if (targetIdx - d - 1 >= 0 && punctRe.test(cue.text[targetIdx - d - 1])) {
        bestIdx = targetIdx - d - 1; break
      }
    }
    let text1: string, text2: string
    if (bestIdx >= 0) {
      text1 = cue.text.slice(0, bestIdx)
      text2 = cue.text.slice(bestIdx + 1)
    } else {
      const splitCharIdx = Math.max(1, targetIdx)
      text1 = cue.text.slice(0, splitCharIdx)
      text2 = cue.text.slice(splitCharIdx)
    }

    const cue1: Cue = {
      ...cue,
      end_ms: splitMs,
      text: text1,
    }
    const cue2: Cue = {
      ...cue,
      id: nextTempId(),
      start_ms: splitMs,
      text: text2,
    }

    const newCues = [...cues]
    newCues.splice(idx, 1, cue1, cue2)

    const oldCues = [...cues]

    const cmd: Command = {
      apply: () => useModelStore.getState().updateCues(newCues),
      inverse: () => useModelStore.getState().updateCues(oldCues),
      description: `Split cue ${id} at ${splitMs}ms`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable merge: merge cue with the next one */
  const mergeWithNext = useCallback((id: number) => {
    const state = useModelStore.getState()
    const cues = state.cues
    const idx = cues.findIndex(c => c.id === id)
    if (idx < 0 || idx >= cues.length - 1) return

    const cue = cues[idx]
    const next = cues[idx + 1]

    // Auto-insert comma if first cue doesn't end with punctuation
    const needsComma = cue.text.length > 0 && !/[，。！？、；：,.!?;:]$/.test(cue.text)
    const merged: Cue = {
      ...cue,
      end_ms: next.end_ms,
      text: cue.text + (needsComma ? '，' : '') + next.text,
    }

    const newCues = [...cues]
    newCues.splice(idx, 2, merged)

    const oldCues = [...cues]

    const cmd: Command = {
      apply: () => useModelStore.getState().updateCues(newCues),
      inverse: () => useModelStore.getState().updateCues(oldCues),
      description: `Merge cues ${id} + ${next.id}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable insert: insert a new cue at given index */
  const insertCue = useCallback((insertIdx: number, newCue: Cue) => {
    const state = useModelStore.getState()
    const oldCues = [...state.cues]
    const newCues = [...oldCues]
    newCues.splice(insertIdx, 0, newCue)

    const cmd: Command = {
      apply: () => useModelStore.getState().updateCues(newCues),
      inverse: () => useModelStore.getState().updateCues(oldCues),
      description: `Insert cue at index ${insertIdx}`,
    }
    execute(cmd)
  }, [execute])

  /** Undoable delete: remove cue by id */
  const deleteCue = useCallback((id: number) => {
    const state = useModelStore.getState()
    const oldCues = [...state.cues]
    const newCues = oldCues.filter(c => c.id !== id)

    const cmd: Command = {
      apply: () => useModelStore.getState().updateCues(newCues),
      inverse: () => useModelStore.getState().updateCues(oldCues),
      description: `Delete cue ${id}`,
    }
    execute(cmd)
  }, [execute])

  return { updateField, editText, changeSpeaker, changeEmotion, adjustTime, splitCue, mergeWithNext, insertCue, deleteCue }
}
