/** Global keyboard shortcuts */
import { useEffect } from 'react'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import { deriveSpeakers } from '../utils/derive-speakers'
import { useUndoableOps } from './useUndoRedo'
import type { Cue } from '../types/asr-model'

import { nextTempId } from '../utils/temp-id'

export function useKeyboard() {
  const cues = useModelStore(s => s.cues)
  const saveCues = useModelStore(s => s.saveCues)
  const selectedCueId = useEditorStore(s => s.selectedCueId)
  const selectCue = useEditorStore(s => s.selectCue)
  const setCurrentTime = useEditorStore(s => s.setCurrentTime)
  const currentTime = useEditorStore(s => s.currentTime)
  const undo = useEditorStore(s => s.undo)
  const redo = useEditorStore(s => s.redo)
  const { updateField, splitCue, mergeWithNext, insertCue, deleteCue } = useUndoableOps()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable

      // Ctrl+S: save (always active)
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        saveCues()
        return
      }

      // Ctrl+Z: undo
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault()
        undo()
        return
      }

      // Ctrl+Shift+Z: redo
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && e.shiftKey) {
        e.preventDefault()
        redo()
        return
      }

      // Skip single-key shortcuts when editing text
      if (isInput) return

      const speakerList = deriveSpeakers(cues)
      const selectedIdx = cues.findIndex(c => c.id === selectedCueId)

      // Space: play/pause
      if (e.key === ' ') {
        e.preventDefault()
        const video = document.querySelector('video')
        if (video) {
          if (video.paused) video.play()
          else video.pause()
        }
        return
      }

      // Enter: jump to next cue + play
      if (e.key === 'Enter') {
        e.preventDefault()
        const nextIdx = selectedIdx + 1
        if (nextIdx < cues.length) {
          const next = cues[nextIdx]
          selectCue(next.id)
          setCurrentTime(Math.max(0, next.start_ms - 500))
          const video = document.querySelector('video')
          if (video) {
            video.currentTime = Math.max(0, next.start_ms - 500) / 1000
            video.play()
          }
        }
        return
      }

      // Shift+Alt+Arrow: snap cue edge to cursor position
      if (e.shiftKey && e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        const video = document.querySelector('video')
        if (video && !video.paused) video.pause()
        if (selectedCueId == null || selectedIdx < 0) return
        const cue = cues[selectedIdx]
        const midMs = (cue.start_ms + cue.end_ms) / 2
        const editStart = currentTime <= midMs
        if (editStart) {
          const newVal = Math.max(0, Math.min(currentTime, cue.end_ms - 30))
          updateField(cue.id, 'start_ms', cue.start_ms, newVal)
        } else {
          const newVal = Math.max(cue.start_ms + 30, currentTime)
          updateField(cue.id, 'end_ms', cue.end_ms, newVal)
        }
        return
      }

      // Alt+Arrow: fine-tune cue edge ±30ms (cursor position decides start or end)
      if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        const video = document.querySelector('video')
        if (video && !video.paused) video.pause()
        if (selectedCueId == null || selectedIdx < 0) return
        const cue = cues[selectedIdx]
        const midMs = (cue.start_ms + cue.end_ms) / 2
        const editStart = currentTime <= midMs
        const direction = e.key === 'ArrowLeft' ? -1 : 1
        if (editStart) {
          const newVal = Math.max(0, Math.min(cue.start_ms + direction * 30, cue.end_ms - 30))
          updateField(cue.id, 'start_ms', cue.start_ms, newVal)
        } else {
          const newVal = Math.max(cue.start_ms + 30, cue.end_ms + direction * 30)
          updateField(cue.id, 'end_ms', cue.end_ms, newVal)
        }
        return
      }

      // Ctrl+B: split cue at playhead (or midpoint if playhead outside cue)
      if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
        e.preventDefault()
        if (selectedCueId == null || selectedIdx < 0) return
        const cue = cues[selectedIdx]
        const playheadInCue = currentTime > cue.start_ms && currentTime < cue.end_ms
        const splitMs = playheadInCue ? currentTime : Math.round((cue.start_ms + cue.end_ms) / 2)
        splitCue(selectedCueId, splitMs)
        return
      }

      // Ctrl+M: merge with next cue (undoable)
      if ((e.ctrlKey || e.metaKey) && e.key === 'm') {
        e.preventDefault()
        if (selectedIdx < 0 || selectedIdx >= cues.length - 1) return
        mergeWithNext(selectedCueId!)
        return
      }

      // Ctrl+I: insert empty cue at current playback position (undoable)
      if ((e.ctrlKey || e.metaKey) && e.key === 'i') {
        e.preventDefault()
        const insertMs = currentTime
        let insertIdx = cues.length
        for (let i = 0; i < cues.length; i++) {
          if (cues[i].start_ms > insertMs) { insertIdx = i; break }
        }
        const prev = insertIdx > 0 ? cues[insertIdx - 1] : null
        const next = insertIdx < cues.length ? cues[insertIdx] : null
        const newStart = prev ? Math.max(insertMs, prev.end_ms) : insertMs
        const newEnd = next ? Math.min(newStart + 1000, next.start_ms) : newStart + 1000
        const refCue = prev || next

        const newCue: Cue = {
          id: nextTempId(),
          episode_id: 0,
          text: '',
          start_ms: newStart,
          end_ms: Math.max(newEnd, newStart + 100),
          speaker: refCue?.speaker ?? 0,
          emotion: 'neutral',
          kind: 'speech',
        }

        insertCue(insertIdx, newCue)
        selectCue(newCue.id)
        return
      }

      // Delete/Backspace: delete selected cue (undoable)
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        if (selectedIdx < 0) return
        deleteCue(selectedCueId!)
        // Select adjacent cue
        const remaining = cues.filter(c => c.id !== selectedCueId)
        if (remaining.length > 0) {
          const newIdx = Math.min(selectedIdx, remaining.length - 1)
          selectCue(remaining[newIdx].id)
        }
        return
      }

      // 1-9: quick speaker switch
      if (/^[1-9]$/.test(e.key) && selectedCueId != null && selectedIdx >= 0) {
        const spkIdx = parseInt(e.key) - 1
        if (spkIdx < speakerList.length) {
          updateField(selectedCueId, 'speaker', cues[selectedIdx].speaker, speakerList[spkIdx])
        }
        return
      }

      // Emotion shortcuts: N=neutral, A=angry, S=sad, E=surprised, I=happy, F=fearful
      const emotionMap: Record<string, string> = {
        n: 'neutral', a: 'angry', s: 'sad', e: 'surprised', i: 'happy', f: 'fearful',
      }
      if (e.key.toLowerCase() in emotionMap && selectedCueId != null && selectedIdx >= 0) {
        updateField(selectedCueId, 'emotion', cues[selectedIdx].emotion, emotionMap[e.key.toLowerCase()])
        return
      }

      // Arrow Left/Right (no modifier): seek playback ±30ms (±1s with Shift), auto-pause
      if (!e.altKey && !e.ctrlKey && !e.metaKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        const video = document.querySelector('video')
        if (video && !video.paused) video.pause()
        const step = e.shiftKey ? 1000 : 30
        const direction = e.key === 'ArrowLeft' ? -1 : 1
        const duration = useEditorStore.getState().duration
        const newTime = Math.max(0, Math.min(duration, currentTime + direction * step))
        setCurrentTime(newTime)
        if (video) video.currentTime = newTime / 1000
        return
      }

      // Arrow Up/Down: navigate cues
      if (e.key === 'ArrowUp' && selectedIdx > 0) {
        e.preventDefault()
        const prev = cues[selectedIdx - 1]
        selectCue(prev.id)
        setCurrentTime(Math.max(0, prev.start_ms - 500))
        return
      }
      if (e.key === 'ArrowDown' && selectedIdx < cues.length - 1) {
        e.preventDefault()
        const next = cues[selectedIdx + 1]
        selectCue(next.id)
        setCurrentTime(Math.max(0, next.start_ms - 500))
        return
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [cues, selectedCueId, currentTime, selectCue, setCurrentTime, saveCues, undo, redo, updateField, splitCue, mergeWithNext, insertCue, deleteCue])
}
