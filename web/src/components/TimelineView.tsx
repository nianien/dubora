/** Canvas timeline: single-track, playhead, zoom, drag boundaries, auto-scroll */
import { useRef, useEffect, useCallback, useState } from 'react'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import { useUndoableOps } from '../hooks/useUndoRedo'
import { deriveSpeakers } from '../utils/derive-speakers'
import { ContextMenu } from './ContextMenu'
import type { ContextMenuItem } from './ContextMenu'
import type { Cue } from '../types/asr-model'

/** Negative temp IDs for new cues */
let _nextTempId = -1

const RULER_HEIGHT = 20
const TRACK_TOP = 24
const TRACK_HEIGHT = 32
const CANVAS_HEIGHT = TRACK_TOP + TRACK_HEIGHT + 8
const PLAYHEAD_COLOR = '#ef4444'
const EDGE_HIT_PX = 6

const SPEAKER_COLORS = [
  '#3b82f6', '#22c55e', '#a855f7', '#f97316',
  '#ec4899', '#14b8a6', '#eab308', '#ef4444',
  '#06b6d4', '#6366f1', '#84cc16', '#d946ef',
]

type DragState = {
  cueId: number
  edge: 'start' | 'end'
  originalMs: number
} | null

export function TimelineView() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const cues = useModelStore(s => s.cues)
  const loaded = useModelStore(s => s.loaded)
  const updateCue = useModelStore(s => s.updateCue)
  const currentTime = useEditorStore(s => s.currentTime)
  const selectedCueId = useEditorStore(s => s.selectedCueId)
  const selectCue = useEditorStore(s => s.selectCue)
  const setCurrentTime = useEditorStore(s => s.setCurrentTime)
  const zoom = useEditorStore(s => s.zoom)
  const scrollOffset = useEditorStore(s => s.scrollOffset)
  const setScrollOffset = useEditorStore(s => s.setScrollOffset)
  const setZoom = useEditorStore(s => s.setZoom)
  const playingCueId = useEditorStore(s => s.playingCueId)
  const isPlaying = useEditorStore(s => s.isPlaying)

  const [dragState, setDragState] = useState<DragState>(null)
  const { splitCue, mergeWithNext, insertCue, deleteCue } = useUndoableOps()
  const undo = useEditorStore(s => s.undo)
  const redo = useEditorStore(s => s.redo)
  const canUndo = useEditorStore(s => s.canUndo)
  const canRedo = useEditorStore(s => s.canRedo)

  const [contextMenu, setContextMenu] = useState<{
    x: number; y: number
    clickMs: number
    cueId: number | null
  } | null>(null)

  const speakers = deriveSpeakers(cues)

  const msToX = useCallback((ms: number) => {
    return ((ms - scrollOffset) / 1000) * zoom
  }, [zoom, scrollOffset])

  const xToMs = useCallback((x: number) => {
    return (x / zoom) * 1000 + scrollOffset
  }, [zoom, scrollOffset])

  // Keep refs for auto-scroll to avoid effect dependency loops
  const scrollOffsetRef = useRef(scrollOffset)
  scrollOffsetRef.current = scrollOffset
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom

  // Auto-scroll: keep playhead in view (only depends on currentTime, not scrollOffset)
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const width = container.getBoundingClientRect().width
    const so = scrollOffsetRef.current
    const z = zoomRef.current
    const phX = ((currentTime - so) / 1000) * z
    if (phX < width * 0.1 || phX > width * 0.9) {
      const targetMs = currentTime - (width * 0.5 / z) * 1000
      setScrollOffset(Math.max(0, targetMs))
    }
  }, [currentTime, setScrollOffset])

  // Auto-scroll to center selected cue (not during playback)
  useEffect(() => {
    if (selectedCueId == null || isPlaying) return
    const container = containerRef.current
    if (!container) return
    const cue = cues.find(c => c.id === selectedCueId)
    if (!cue) return
    const width = container.getBoundingClientRect().width
    const so = scrollOffsetRef.current
    const z = zoomRef.current
    const cueMidMs = (cue.start_ms + cue.end_ms) / 2
    const cueMidX = ((cueMidMs - so) / 1000) * z
    if (cueMidX < 0 || cueMidX > width) {
      const targetMs = cueMidMs - (width * 0.5 / z) * 1000
      setScrollOffset(Math.max(0, targetMs))
    }
  }, [selectedCueId, cues, isPlaying, setScrollOffset])

  // Draw
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const rect = container.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    canvas.width = rect.width * dpr
    canvas.height = CANVAS_HEIGHT * dpr
    canvas.style.width = `${rect.width}px`
    canvas.style.height = `${CANVAS_HEIGHT}px`

    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)

    const width = rect.width

    // Clear
    ctx.fillStyle = '#1f2937'
    ctx.fillRect(0, 0, width, CANVAS_HEIGHT)

    // No data → just show empty canvas
    if (!loaded) return

    // Time ruler
    ctx.fillStyle = '#374151'
    ctx.fillRect(0, 0, width, RULER_HEIGHT)

    const startSec = Math.floor(scrollOffset / 1000)
    const endSec = Math.ceil((scrollOffset + width / zoom * 1000) / 1000)
    for (let s = startSec; s <= endSec; s++) {
      const x = msToX(s * 1000)
      if (x < 0 || x > width) continue
      ctx.fillStyle = '#4b5563'
      ctx.fillRect(x, 16, 1, 4)
      if (s % 5 === 0) {
        ctx.fillStyle = '#9ca3af'
        ctx.font = '10px monospace'
        const label = `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, '0')}`
        ctx.fillText(label, x + 2, 14)
      }
    }

    // Track background
    ctx.fillStyle = '#111827'
    ctx.fillRect(0, TRACK_TOP, width, TRACK_HEIGHT)

    // Draw cues — all on one track
    cues.forEach(cue => {
      const spkIdx = speakers.indexOf(cue.speaker)
      const x1 = msToX(cue.start_ms)
      const x2 = msToX(cue.end_ms)
      const cueWidth = Math.max(2, x2 - x1)
      if (x2 < 0 || x1 > width) return

      const color = SPEAKER_COLORS[Math.max(0, spkIdx) % SPEAKER_COLORS.length]
      const isSelected = cue.id === selectedCueId
      const isPlayingNow = cue.id === playingCueId
      const isDragging = dragState?.cueId === cue.id

      const drawX = Math.max(0, x1)
      const drawW = Math.min(cueWidth, width - drawX)

      // Cue rect
      ctx.fillStyle = (isPlayingNow || isSelected) ? color : color + '99'
      ctx.fillRect(drawX, TRACK_TOP + 2, drawW, TRACK_HEIGHT - 4)

      // Border
      if (isSelected || isDragging || isPlayingNow) {
        ctx.strokeStyle = isDragging ? '#fbbf24' : isPlayingNow ? '#60a5fa' : '#ffffff'
        ctx.lineWidth = 1.5
        ctx.strokeRect(drawX, TRACK_TOP + 2, drawW, TRACK_HEIGHT - 4)
      }

      // Edge handles (active edge highlighted based on cursor position)
      if (isSelected && cueWidth > 10) {
        const midMs = (cue.start_ms + cue.end_ms) / 2
        const startActive = currentTime <= midMs
        // Start handle
        ctx.fillStyle = startActive ? '#fbbf24' : '#ffffff55'
        ctx.fillRect(drawX - 1, TRACK_TOP + 3, 3, TRACK_HEIGHT - 6)
        // End handle
        const rx = Math.min(x2, width)
        ctx.fillStyle = startActive ? '#ffffff55' : '#fbbf24'
        ctx.fillRect(rx - 2, TRACK_TOP + 3, 3, TRACK_HEIGHT - 6)
      }

      // Text label
      if (cueWidth > 20) {
        ctx.fillStyle = '#ffffff'
        ctx.font = '10px sans-serif'
        ctx.save()
        ctx.beginPath()
        ctx.rect(drawX, TRACK_TOP, drawW, TRACK_HEIGHT)
        ctx.clip()
        ctx.fillText(cue.text, drawX + 3, TRACK_TOP + TRACK_HEIGHT / 2 + 3, drawW - 6)
        ctx.restore()
      }
    })

    // Playhead
    const phX = msToX(currentTime)
    if (phX >= 0 && phX <= width) {
      ctx.strokeStyle = PLAYHEAD_COLOR
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(phX, 0)
      ctx.lineTo(phX, CANVAS_HEIGHT)
      ctx.stroke()
      ctx.fillStyle = PLAYHEAD_COLOR
      ctx.beginPath()
      ctx.moveTo(phX - 6, 0)
      ctx.lineTo(phX + 6, 0)
      ctx.lineTo(phX, 10)
      ctx.closePath()
      ctx.fill()
    }
  }, [loaded, cues, speakers, currentTime, selectedCueId, playingCueId, zoom, scrollOffset, msToX, dragState])

  // Find cue edge near mouse
  const findEdge = useCallback((x: number, y: number): { cueId: number; edge: 'start' | 'end' } | null => {
    if (y < TRACK_TOP || y > TRACK_TOP + TRACK_HEIGHT) return null
    for (const cue of cues) {
      const x1 = msToX(cue.start_ms)
      const x2 = msToX(cue.end_ms)
      if (Math.abs(x - x1) <= EDGE_HIT_PX) return { cueId: cue.id, edge: 'start' }
      if (Math.abs(x - x2) <= EDGE_HIT_PX) return { cueId: cue.id, edge: 'end' }
    }
    return null
  }, [msToX, cues])

  // Find cue at position
  const findCueAt = useCallback((x: number, y: number): Cue | null => {
    if (y < TRACK_TOP || y > TRACK_TOP + TRACK_HEIGHT) return null
    const ms = xToMs(x)
    return cues.find(c => c.start_ms <= ms && ms <= c.end_ms) ?? null
  }, [xToMs, cues])

  // Mouse down: edge drag or click-to-seek
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top

    const edge = findEdge(x, y)
    if (edge) {
      e.preventDefault()
      const cue = cues.find(c => c.id === edge.cueId)
      if (cue) {
        setDragState({ cueId: edge.cueId, edge: edge.edge, originalMs: edge.edge === 'start' ? cue.start_ms : cue.end_ms })
        selectCue(edge.cueId)
      }
      return
    }

    const ms = xToMs(x)
    if (ms < 0) return

    const clickedCue = findCueAt(x, y)
    if (clickedCue) {
      selectCue(clickedCue.id)
      return
    }

    // Click empty area: move playhead
    const seekMs = Math.max(0, Math.round(ms))
    setCurrentTime(seekMs)
    const video = document.querySelector('video')
    if (video) video.currentTime = seekMs / 1000
  }, [findEdge, findCueAt, xToMs, cues, selectCue, setCurrentTime])

  // Drag move/up
  useEffect(() => {
    if (!dragState) return
    const handleMouseMove = (e: MouseEvent) => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const x = e.clientX - rect.left
      const ms = Math.max(0, Math.round(xToMs(x)))
      const cue = cues.find(c => c.id === dragState.cueId)
      if (!cue) return
      if (dragState.edge === 'start') {
        updateCue(cue.id, { start_ms: Math.min(ms, cue.end_ms - 100) })
      } else {
        updateCue(cue.id, { end_ms: Math.max(ms, cue.start_ms + 100) })
      }
    }
    const handleMouseUp = () => setDragState(null)
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => { window.removeEventListener('mousemove', handleMouseMove); window.removeEventListener('mouseup', handleMouseUp) }
  }, [dragState, xToMs, cues, updateCue])

  // Cursor
  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const canvas = canvasRef.current
    if (!canvas || dragState) return
    const rect = canvas.getBoundingClientRect()
    const edge = findEdge(e.clientX - rect.left, e.clientY - rect.top)
    canvas.style.cursor = edge ? 'col-resize' : 'crosshair'
  }, [findEdge, dragState])

  // Wheel: scroll horizontally + zoom
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    if (e.ctrlKey || e.metaKey) {
      setZoom(zoom + (e.deltaY > 0 ? -5 : 5))
    } else {
      const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY
      setScrollOffset(scrollOffset + delta * 10)
    }
  }, [zoom, scrollOffset, setZoom, setScrollOffset])

  // Right-click
  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    const ms = Math.max(0, Math.round(xToMs(cx)))
    const hit = findCueAt(cx, cy)
    if (hit) selectCue(hit.id)
    setContextMenu({ x: e.clientX, y: e.clientY, clickMs: ms, cueId: hit?.id ?? null })
  }, [xToMs, findCueAt, selectCue])

  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  const buildMenuItems = useCallback((): ContextMenuItem[] => {
    if (!contextMenu) return []
    const { clickMs, cueId } = contextMenu

    if (cueId != null) {
      const idx = cues.findIndex(c => c.id === cueId)
      if (idx < 0) return []
      const cue = cues[idx]
      const isLast = idx >= cues.length - 1
      const canSplit = clickMs > cue.start_ms && clickMs < cue.end_ms

      return [
        { label: 'Split Here', shortcut: '\u2318B', onClick: () => splitCue(cueId, clickMs), disabled: !canSplit },
        { label: 'Merge with Next', shortcut: '\u2318M', onClick: () => mergeWithNext(cueId), disabled: isLast, dividerAfter: true },
        {
          label: 'Delete Cue', shortcut: '\u232B',
          onClick: () => {
            deleteCue(cueId)
            const remaining = cues.filter(c => c.id !== cueId)
            if (remaining.length > 0) selectCue(remaining[Math.min(idx, remaining.length - 1)].id)
          },
          dividerAfter: true,
        },
        { label: 'Undo', shortcut: '\u2318Z', onClick: () => undo(), disabled: !canUndo() },
        { label: 'Redo', shortcut: '\u21E7\u2318Z', onClick: () => redo(), disabled: !canRedo() },
      ]
    }

    // Empty space
    return [
      {
        label: 'Insert Cue Here', shortcut: '\u2318I',
        onClick: () => {
          let insertIdx = cues.length
          for (let i = 0; i < cues.length; i++) {
            if (cues[i].start_ms > clickMs) { insertIdx = i; break }
          }
          const prev = insertIdx > 0 ? cues[insertIdx - 1] : null
          const next = insertIdx < cues.length ? cues[insertIdx] : null
          const newStart = prev ? Math.max(clickMs, prev.end_ms) : clickMs
          const newEnd = next ? Math.min(newStart + 1000, next.start_ms) : newStart + 1000
          const refCue = prev || next
          const newCue: Cue = {
            id: _nextTempId--,
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
        },
        dividerAfter: true,
      },
      { label: 'Undo', shortcut: '\u2318Z', onClick: () => undo(), disabled: !canUndo() },
      { label: 'Redo', shortcut: '\u21E7\u2318Z', onClick: () => redo(), disabled: !canRedo() },
    ]
  }, [contextMenu, cues, splitCue, mergeWithNext, insertCue, deleteCue, selectCue, undo, redo, canUndo, canRedo])

  // Resize observer: force canvas redraw on container resize
  const [, setResizeTick] = useState(0)
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver(() => setResizeTick(t => t + 1))
    ro.observe(container)
    return () => ro.disconnect()
  }, [])

  return (
    <div ref={containerRef} className="w-full bg-gray-800" style={{ height: CANVAS_HEIGHT }}>
      <canvas
        ref={canvasRef}
        className="w-full"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onWheel={handleWheel}
        onContextMenu={handleContextMenu}
      />
      {contextMenu && (
        <ContextMenu x={contextMenu.x} y={contextMenu.y} items={buildMenuItems()} onClose={closeContextMenu} />
      )}
    </div>
  )
}
