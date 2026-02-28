/** Global keyboard shortcuts */
import { useEffect } from 'react'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import { deriveSpeakers } from '../utils/derive-speakers'
import { useUndoableOps } from './useUndoRedo'
import type { AsrSegment } from '../types/asr-model'

export function useKeyboard() {
  const model = useModelStore(s => s.model)
  const updateSegment = useModelStore(s => s.updateSegment)
  const saveModel = useModelStore(s => s.saveModel)
  const { selectedSegmentId, selectSegment, setCurrentTime, currentTime, undo, redo } = useEditorStore()
  const { splitSegment, mergeWithNext, insertSegment, deleteSegment } = useUndoableOps()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable

      // Ctrl+S: save (always active)
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        saveModel()
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

      const segments = model?.segments ?? []
      const speakerList = deriveSpeakers(segments)
      const selectedIdx = segments.findIndex(s => s.id === selectedSegmentId)

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

      // Enter: jump to next segment + play
      if (e.key === 'Enter') {
        e.preventDefault()
        const nextIdx = selectedIdx + 1
        if (nextIdx < segments.length) {
          const next = segments[nextIdx]
          selectSegment(next.id)
          setCurrentTime(Math.max(0, next.start_ms - 500))
          const video = document.querySelector('video')
          if (video) {
            video.currentTime = Math.max(0, next.start_ms - 500) / 1000
            video.play()
          }
        }
        return
      }

      // Alt+Arrow: fine-tune start_ms ±50ms (or ±200ms with Shift)
      if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        if (!selectedSegmentId || selectedIdx < 0) return
        const seg = segments[selectedIdx]
        const delta = e.shiftKey ? 200 : 50
        const direction = e.key === 'ArrowLeft' ? -1 : 1
        updateSegment(seg.id, {
          start_ms: Math.max(0, seg.start_ms + direction * delta),
        })
        return
      }

      // Ctrl+Alt+Arrow: fine-tune end_ms ±50ms (or ±200ms with Shift)
      if ((e.ctrlKey || e.metaKey) && e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        if (!selectedSegmentId || selectedIdx < 0) return
        const seg = segments[selectedIdx]
        const delta = e.shiftKey ? 200 : 50
        const direction = e.key === 'ArrowLeft' ? -1 : 1
        updateSegment(seg.id, {
          end_ms: Math.max(seg.start_ms + 100, seg.end_ms + direction * delta),
        })
        return
      }

      // Ctrl+B: split segment at current playback time (undoable)
      if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
        e.preventDefault()
        if (!selectedSegmentId || selectedIdx < 0) return
        splitSegment(selectedSegmentId, currentTime)
        return
      }

      // Ctrl+M: merge with next segment (undoable)
      if ((e.ctrlKey || e.metaKey) && e.key === 'm') {
        e.preventDefault()
        if (selectedIdx < 0 || selectedIdx >= segments.length - 1) return
        mergeWithNext(selectedSegmentId!)
        return
      }

      // Ctrl+I: insert empty segment at current playback position (undoable)
      if ((e.ctrlKey || e.metaKey) && e.key === 'i') {
        e.preventDefault()
        const insertMs = currentTime
        let insertIdx = segments.length
        for (let i = 0; i < segments.length; i++) {
          if (segments[i].start_ms > insertMs) { insertIdx = i; break }
        }
        const prev = insertIdx > 0 ? segments[insertIdx - 1] : null
        const next = insertIdx < segments.length ? segments[insertIdx] : null
        const newStart = prev ? Math.max(insertMs, prev.end_ms) : insertMs
        const newEnd = next ? Math.min(newStart + 1000, next.start_ms) : newStart + 1000
        const refSeg = prev || next

        const newSeg: AsrSegment = {
          id: `seg_${Math.random().toString(16).slice(2, 10)}`,
          start_ms: newStart,
          end_ms: Math.max(newEnd, newStart + 100),
          text: '',
          text_en: '',
          speaker: refSeg?.speaker ?? '0',
          emotion: 'neutral',
          type: 'speech',
          flags: { overlap: false, needs_review: false },
        }

        insertSegment(insertIdx, newSeg)
        selectSegment(newSeg.id)
        return
      }

      // Delete/Backspace: delete selected segment (undoable)
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        if (selectedIdx < 0) return
        deleteSegment(selectedSegmentId!)
        // Select adjacent segment
        const remaining = segments.filter(s => s.id !== selectedSegmentId)
        if (remaining.length > 0) {
          const newIdx = Math.min(selectedIdx, remaining.length - 1)
          selectSegment(remaining[newIdx].id)
        }
        return
      }

      // 1-9: quick speaker switch
      if (/^[1-9]$/.test(e.key) && selectedSegmentId) {
        const spkIdx = parseInt(e.key) - 1
        if (spkIdx < speakerList.length) {
          updateSegment(selectedSegmentId, { speaker: speakerList[spkIdx] })
        }
        return
      }

      // Emotion shortcuts: N=neutral, A=angry, S=sad, E=surprised, I=happy, F=fearful
      const emotionMap: Record<string, string> = {
        n: 'neutral', a: 'angry', s: 'sad', e: 'surprised', i: 'happy', f: 'fearful',
      }
      if (e.key.toLowerCase() in emotionMap && selectedSegmentId) {
        updateSegment(selectedSegmentId, { emotion: emotionMap[e.key.toLowerCase()] })
        return
      }

      // Arrow Up/Down: navigate segments
      if (e.key === 'ArrowUp' && selectedIdx > 0) {
        e.preventDefault()
        const prev = segments[selectedIdx - 1]
        selectSegment(prev.id)
        setCurrentTime(Math.max(0, prev.start_ms - 500))
        return
      }
      if (e.key === 'ArrowDown' && selectedIdx < segments.length - 1) {
        e.preventDefault()
        const next = segments[selectedIdx + 1]
        selectSegment(next.id)
        setCurrentTime(Math.max(0, next.start_ms - 500))
        return
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [model, selectedSegmentId, currentTime, selectSegment, setCurrentTime, updateSegment, saveModel, undo, redo, splitSegment, mergeWithNext, insertSegment, deleteSegment])
}
