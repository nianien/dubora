/** Canvas timeline: single-track, playhead, zoom, drag boundaries, auto-scroll */
import { useRef, useEffect, useCallback, useState } from 'react'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import { useUndoableOps } from '../hooks/useUndoRedo'
import { deriveSpeakers } from '../utils/derive-speakers'
import { ContextMenu } from './ContextMenu'
import type { ContextMenuItem } from './ContextMenu'
import type { AsrSegment } from '../types/asr-model'

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
  segId: string
  edge: 'start' | 'end'
  originalMs: number
} | null

export function TimelineView() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const model = useModelStore(s => s.model)
  const updateSegment = useModelStore(s => s.updateSegment)
  const { currentTime, selectedSegmentId, selectSegment, setCurrentTime, zoom, scrollOffset, setScrollOffset, setZoom, playingSegmentId, isPlaying } = useEditorStore()

  const [dragState, setDragState] = useState<DragState>(null)
  const { splitSegment, mergeWithNext, insertSegment, deleteSegment } = useUndoableOps()
  const { undo, redo, canUndo, canRedo } = useEditorStore()

  const [contextMenu, setContextMenu] = useState<{
    x: number; y: number
    clickMs: number
    segId: string | null
  } | null>(null)

  const segments = model?.segments ?? []
  const speakers = deriveSpeakers(segments)

  const msToX = useCallback((ms: number) => {
    return ((ms - scrollOffset) / 1000) * zoom
  }, [zoom, scrollOffset])

  const xToMs = useCallback((x: number) => {
    return (x / zoom) * 1000 + scrollOffset
  }, [zoom, scrollOffset])

  // Auto-scroll: keep playhead visible during playback
  useEffect(() => {
    if (!isPlaying) return
    const container = containerRef.current
    if (!container) return
    const width = container.getBoundingClientRect().width
    const phX = msToX(currentTime)
    const rightThreshold = width * 0.75
    const leftThreshold = width * 0.05
    if (phX > rightThreshold || phX < leftThreshold) {
      const targetMs = currentTime - (width * 0.25 / zoom) * 1000
      setScrollOffset(Math.max(0, targetMs))
    }
  }, [currentTime, isPlaying, zoom, msToX, setScrollOffset])

  // Auto-scroll horizontally to center the selected segment
  useEffect(() => {
    if (!selectedSegmentId || isPlaying) return
    const container = containerRef.current
    if (!container) return
    const seg = segments.find(s => s.id === selectedSegmentId)
    if (!seg) return
    const width = container.getBoundingClientRect().width
    const segMidMs = (seg.start_ms + seg.end_ms) / 2
    const segMidX = msToX(segMidMs)
    if (segMidX < 0 || segMidX > width) {
      const targetMs = segMidMs - (width * 0.5 / zoom) * 1000
      setScrollOffset(Math.max(0, targetMs))
    }
  }, [selectedSegmentId, segments, isPlaying, msToX, zoom, setScrollOffset])

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

    // No model → just show empty canvas
    if (!model) return

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

    // Draw segments — all on one track
    segments.forEach(seg => {
      const spkIdx = speakers.indexOf(seg.speaker)
      const x1 = msToX(seg.start_ms)
      const x2 = msToX(seg.end_ms)
      const segWidth = Math.max(2, x2 - x1)
      if (x2 < 0 || x1 > width) return

      const color = SPEAKER_COLORS[Math.max(0, spkIdx) % SPEAKER_COLORS.length]
      const isSelected = seg.id === selectedSegmentId
      const isPlayingNow = seg.id === playingSegmentId
      const isDragging = dragState?.segId === seg.id

      const drawX = Math.max(0, x1)
      const drawW = Math.min(segWidth, width - drawX)

      // Segment rect
      ctx.fillStyle = (isPlayingNow || isSelected) ? color : color + '99'
      ctx.fillRect(drawX, TRACK_TOP + 2, drawW, TRACK_HEIGHT - 4)

      // Border
      if (isSelected || isDragging || isPlayingNow) {
        ctx.strokeStyle = isDragging ? '#fbbf24' : isPlayingNow ? '#60a5fa' : '#ffffff'
        ctx.lineWidth = 1.5
        ctx.strokeRect(drawX, TRACK_TOP + 2, drawW, TRACK_HEIGHT - 4)
      }

      // Edge handles
      if (isSelected && segWidth > 10) {
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(drawX - 1, TRACK_TOP + 5, 3, TRACK_HEIGHT - 10)
        const rx = Math.min(x2, width)
        ctx.fillRect(rx - 2, TRACK_TOP + 5, 3, TRACK_HEIGHT - 10)
      }

      // Text label (speaker + text)
      if (segWidth > 20) {
        ctx.fillStyle = '#ffffff'
        ctx.font = '10px sans-serif'
        ctx.save()
        ctx.beginPath()
        ctx.rect(drawX, TRACK_TOP, drawW, TRACK_HEIGHT)
        ctx.clip()
        const label = speakers.length > 1 ? `${seg.speaker}: ${seg.text}` : seg.text
        ctx.fillText(label, drawX + 3, TRACK_TOP + TRACK_HEIGHT / 2 + 3, drawW - 6)
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
  }, [model, segments, speakers, currentTime, selectedSegmentId, playingSegmentId, zoom, scrollOffset, msToX, dragState])

  // Find segment edge near mouse
  const findEdge = useCallback((x: number, y: number): { segId: string; edge: 'start' | 'end' } | null => {
    if (y < TRACK_TOP || y > TRACK_TOP + TRACK_HEIGHT) return null
    for (const seg of segments) {
      const x1 = msToX(seg.start_ms)
      const x2 = msToX(seg.end_ms)
      if (Math.abs(x - x1) <= EDGE_HIT_PX) return { segId: seg.id, edge: 'start' }
      if (Math.abs(x - x2) <= EDGE_HIT_PX) return { segId: seg.id, edge: 'end' }
    }
    return null
  }, [msToX, segments])

  // Find segment at position
  const findSegmentAt = useCallback((x: number, y: number): AsrSegment | null => {
    if (y < TRACK_TOP || y > TRACK_TOP + TRACK_HEIGHT) return null
    const ms = xToMs(x)
    return segments.find(s => s.start_ms <= ms && ms <= s.end_ms) ?? null
  }, [xToMs, segments])

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
      const seg = segments.find(s => s.id === edge.segId)
      if (seg) {
        setDragState({ segId: edge.segId, edge: edge.edge, originalMs: edge.edge === 'start' ? seg.start_ms : seg.end_ms })
        selectSegment(edge.segId)
      }
      return
    }

    const ms = xToMs(x)
    if (ms < 0) return

    const clickedSeg = findSegmentAt(x, y)
    if (clickedSeg) {
      selectSegment(clickedSeg.id)
    }

    const seekMs = Math.max(0, Math.round(ms))
    setCurrentTime(seekMs)
    const video = document.querySelector('video')
    if (video) video.currentTime = seekMs / 1000
  }, [findEdge, findSegmentAt, xToMs, segments, selectSegment, setCurrentTime])

  // Drag move/up
  useEffect(() => {
    if (!dragState) return
    const handleMouseMove = (e: MouseEvent) => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const x = e.clientX - rect.left
      const ms = Math.max(0, Math.round(xToMs(x)))
      const seg = segments.find(s => s.id === dragState.segId)
      if (!seg) return
      if (dragState.edge === 'start') {
        updateSegment(seg.id, { start_ms: Math.min(ms, seg.end_ms - 100) })
      } else {
        updateSegment(seg.id, { end_ms: Math.max(ms, seg.start_ms + 100) })
      }
    }
    const handleMouseUp = () => setDragState(null)
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => { window.removeEventListener('mousemove', handleMouseMove); window.removeEventListener('mouseup', handleMouseUp) }
  }, [dragState, xToMs, segments, updateSegment])

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
    const hit = findSegmentAt(cx, cy)
    if (hit) selectSegment(hit.id)
    setContextMenu({ x: e.clientX, y: e.clientY, clickMs: ms, segId: hit?.id ?? null })
  }, [xToMs, findSegmentAt, selectSegment])

  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  const buildMenuItems = useCallback((): ContextMenuItem[] => {
    if (!contextMenu) return []
    const { clickMs, segId } = contextMenu
    const segs = model?.segments ?? []

    if (segId) {
      const idx = segs.findIndex(s => s.id === segId)
      if (idx < 0) return []
      const seg = segs[idx]
      const isLast = idx >= segs.length - 1
      const canSplit = clickMs > seg.start_ms && clickMs < seg.end_ms

      return [
        { label: 'Split Here', shortcut: '\u2318B', onClick: () => splitSegment(segId, clickMs), disabled: !canSplit },
        { label: 'Merge with Next', shortcut: '\u2318M', onClick: () => mergeWithNext(segId), disabled: isLast, dividerAfter: true },
        {
          label: 'Delete Segment', shortcut: '\u232B',
          onClick: () => {
            deleteSegment(segId)
            const remaining = segs.filter(s => s.id !== segId)
            if (remaining.length > 0) selectSegment(remaining[Math.min(idx, remaining.length - 1)].id)
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
        label: 'Insert Segment Here', shortcut: '\u2318I',
        onClick: () => {
          let insertIdx = segs.length
          for (let i = 0; i < segs.length; i++) {
            if (segs[i].start_ms > clickMs) { insertIdx = i; break }
          }
          const prev = insertIdx > 0 ? segs[insertIdx - 1] : null
          const next = insertIdx < segs.length ? segs[insertIdx] : null
          const newStart = prev ? Math.max(clickMs, prev.end_ms) : clickMs
          const newEnd = next ? Math.min(newStart + 1000, next.start_ms) : newStart + 1000
          const refSeg = prev || next
          const newSeg: AsrSegment = {
            id: `seg_${Math.random().toString(16).slice(2, 10)}`,
            start_ms: newStart, end_ms: Math.max(newEnd, newStart + 100),
            text: '', text_en: '', speaker: refSeg?.speaker ?? '0', emotion: 'neutral', type: 'speech',
            flags: { overlap: false, needs_review: false },
          }
          insertSegment(insertIdx, newSeg)
          selectSegment(newSeg.id)
        },
        dividerAfter: true,
      },
      { label: 'Undo', shortcut: '\u2318Z', onClick: () => undo(), disabled: !canUndo() },
      { label: 'Redo', shortcut: '\u21E7\u2318Z', onClick: () => redo(), disabled: !canRedo() },
    ]
  }, [contextMenu, model, splitSegment, mergeWithNext, insertSegment, deleteSegment, selectSegment, undo, redo, canUndo, canRedo])

  // Resize observer
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver(() => setScrollOffset(scrollOffset))
    ro.observe(container)
    return () => ro.disconnect()
  }, [scrollOffset, setScrollOffset])

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
