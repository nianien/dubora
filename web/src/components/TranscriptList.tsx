/** Transcript segment list with virtual scrolling */
import { useRef, useCallback, useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useModelStore } from '../stores/model-store'
import { useEditorStore } from '../stores/editor-store'
import { useUndoableOps } from '../hooks/useUndoRedo'
import { msToDisplay } from '../utils/time'
import { emotionColor } from '../utils/emotion-colors'
import { deriveSpeakers } from '../utils/derive-speakers'
import { ContextMenu } from './ContextMenu'
import type { ContextMenuItem } from './ContextMenu'
import type { AsrSegment } from '../types/asr-model'

const SPEAKER_COLORS = [
  'bg-blue-900/40',
  'bg-green-900/40',
  'bg-purple-900/40',
  'bg-orange-900/40',
  'bg-pink-900/40',
  'bg-teal-900/40',
  'bg-yellow-900/40',
  'bg-red-900/40',
  'bg-cyan-900/40',
  'bg-indigo-900/40',
  'bg-lime-900/40',
  'bg-fuchsia-900/40',
]

const SPEAKER_BADGE_COLORS = [
  'bg-blue-600',
  'bg-green-600',
  'bg-purple-600',
  'bg-orange-600',
  'bg-pink-600',
  'bg-teal-600',
  'bg-yellow-600',
  'bg-red-600',
  'bg-cyan-600',
  'bg-indigo-600',
  'bg-lime-600',
  'bg-fuchsia-600',
]

function speakerColor(speaker: string, speakers: string[]): string {
  const idx = speakers.indexOf(speaker)
  return SPEAKER_COLORS[idx >= 0 ? idx % SPEAKER_COLORS.length : 0]
}

function speakerBadgeColor(speaker: string, speakers: string[]): string {
  const idx = speakers.indexOf(speaker)
  return SPEAKER_BADGE_COLORS[idx >= 0 ? idx % SPEAKER_BADGE_COLORS.length : 0]
}

/** Inline dropdown — rendered via portal to escape transform parents, auto-flips upward */
function InlineDropdown({ anchorRef, onClose, children }: {
  anchorRef: React.RefObject<HTMLDivElement | null>
  onClose: () => void
  children: React.ReactNode
}) {
  const dropdownRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: -9999, top: -9999 })

  // Measure anchor + dropdown, place next to anchor, flip if needed
  useEffect(() => {
    const anchor = anchorRef.current
    const dropdown = dropdownRef.current
    if (!anchor || !dropdown) return
    const ar = anchor.getBoundingClientRect()
    const dr = dropdown.getBoundingClientRect()
    const spaceBelow = window.innerHeight - ar.bottom
    const fitsBelow = spaceBelow >= Math.min(dr.height, 200) + 4
    const left = Math.min(ar.left, window.innerWidth - dr.width - 8)
    setPos({
      left: Math.max(0, left),
      top: fitsBelow ? ar.bottom + 4 : ar.top - dr.height - 4,
    })
  }, [anchorRef, children])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
        anchorRef.current && !anchorRef.current.contains(e.target as Node)
      ) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [anchorRef, onClose])

  return createPortal(
    <div
      ref={dropdownRef}
      className="fixed z-[100] bg-gray-800 border border-gray-600 rounded shadow-lg py-1 min-w-[100px] max-h-[200px] overflow-auto"
      style={{ left: pos.left, top: pos.top }}
    >
      {children}
    </div>,
    document.body,
  )
}

export function TranscriptList() {
  const model = useModelStore(s => s.model)
  const updateSegment = useModelStore(s => s.updateSegment)
  const { selectedSegmentId, selectSegment, setCurrentTime, currentTime, playingSegmentId, setPlayingSegment, isPlaying } = useEditorStore()
  const { splitSegment, mergeWithNext, insertSegment, deleteSegment } = useUndoableOps()

  const roles = useModelStore(s => s.roles)

  const parentRef = useRef<HTMLDivElement>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingField, setEditingField] = useState<'text' | 'text_en'>('text')
  const [editText, setEditText] = useState('')

  // Dropdown state: which segment + which dropdown type is open
  const [dropdownSegId, setDropdownSegId] = useState<string | null>(null)
  const [dropdownType, setDropdownType] = useState<'speaker' | 'emotion' | null>(null)

  const emotions = useModelStore(s => s.emotions)

  const segments = model?.segments ?? []
  const speakerList = deriveSpeakers(segments)

  const virtualizer = useVirtualizer({
    count: segments.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 76,
    overscan: 10,
  })

  // Track which segment is currently playing (separate from user selection)
  useEffect(() => {
    if (!segments.length || !isPlaying) return
    const idx = segments.findIndex(
      seg => seg.start_ms <= currentTime && currentTime < seg.end_ms
    )
    if (idx >= 0 && segments[idx].id !== playingSegmentId) {
      setPlayingSegment(segments[idx].id)
    }
  }, [currentTime, segments, playingSegmentId, isPlaying, setPlayingSegment])

  // Clear playing segment when playback stops
  useEffect(() => {
    if (!isPlaying) {
      setPlayingSegment(null)
    }
  }, [isPlaying, setPlayingSegment])

  // Auto-scroll to playing segment during playback
  useEffect(() => {
    if (!playingSegmentId || editingId) return
    const idx = segments.findIndex(s => s.id === playingSegmentId)
    if (idx >= 0) {
      virtualizer.scrollToIndex(idx, { align: 'auto' })
    }
  }, [playingSegmentId, segments, editingId, virtualizer])

  // Scroll to user-selected segment — only when selection actually changes
  const prevSelectedRef = useRef<string | null>(null)
  useEffect(() => {
    if (!selectedSegmentId || selectedSegmentId === prevSelectedRef.current) return
    prevSelectedRef.current = selectedSegmentId
    const idx = segments.findIndex(s => s.id === selectedSegmentId)
    if (idx >= 0) {
      virtualizer.scrollToIndex(idx, { align: 'auto' })
    }
  }, [selectedSegmentId, segments, virtualizer])

  const handleClick = useCallback((seg: AsrSegment) => {
    selectSegment(seg.id)
    // Seek to segment start (playhead + video both go to same position)
    setCurrentTime(seg.start_ms)
    const video = document.querySelector('video')
    if (video) {
      video.currentTime = seg.start_ms / 1000
    }
  }, [selectSegment, setCurrentTime])

  const handleDoubleClick = useCallback((seg: AsrSegment, field: 'text' | 'text_en' = 'text') => {
    setEditingId(seg.id)
    setEditingField(field)
    setEditText(field === 'text' ? seg.text : (seg.text_en ?? ''))
  }, [])

  const handleTextCommit = useCallback((id: string) => {
    const trimmed = editText.trim()
    updateSegment(id, { [editingField]: trimmed })
    setEditingId(null)
  }, [editText, editingField, updateSegment])

  const handleTextKeyDown = useCallback((e: React.KeyboardEvent, id: string) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleTextCommit(id)
    } else if (e.key === 'Escape') {
      setEditingId(null)
    }
  }, [handleTextCommit])

  const toggleDropdown = useCallback((segId: string, type: 'speaker' | 'emotion') => {
    if (dropdownSegId === segId && dropdownType === type) {
      setDropdownSegId(null)
      setDropdownType(null)
    } else {
      setDropdownSegId(segId)
      setDropdownType(type)
    }
  }, [dropdownSegId, dropdownType])

  const closeDropdown = useCallback(() => {
    setDropdownSegId(null)
    setDropdownType(null)
  }, [])

  // Context menu state
  const [contextMenu, setContextMenu] = useState<{ segId: string; x: number; y: number } | null>(null)

  const handleContextMenu = useCallback((e: React.MouseEvent, seg: AsrSegment) => {
    e.preventDefault()
    e.stopPropagation()
    selectSegment(seg.id)
    setContextMenu({ segId: seg.id, x: e.clientX, y: e.clientY })
  }, [selectSegment])

  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  /** Helper: create a new empty segment */
  const makeEmptySeg = useCallback((startMs: number, endMs: number, speaker: string): AsrSegment => ({
    id: `seg_${Math.random().toString(16).slice(2, 10)}`,
    start_ms: startMs,
    end_ms: Math.max(endMs, startMs + 100),
    text: '',
    text_en: '',
    speaker,
    emotion: 'neutral',
    type: 'speech',
    flags: { overlap: false, needs_review: false },
  }), [])

  const buildContextMenuItems = useCallback((segId: string): ContextMenuItem[] => {
    const segs = model?.segments ?? []
    const idx = segs.findIndex(s => s.id === segId)
    if (idx < 0) return []
    const seg = segs[idx]
    const isLast = idx >= segs.length - 1
    const isFirst = idx === 0
    const canSplit = currentTime > seg.start_ms && currentTime < seg.end_ms

    const prev = !isFirst ? segs[idx - 1] : null
    const next = !isLast ? segs[idx + 1] : null

    return [
      {
        label: 'Split at Playhead',
        shortcut: '\u2318B',
        onClick: () => splitSegment(segId, currentTime),
        disabled: !canSplit,
        dividerAfter: true,
      },
      {
        label: 'Insert Before',
        onClick: () => {
          const newStart = prev ? prev.end_ms : Math.max(0, seg.start_ms - 1000)
          const newSeg = makeEmptySeg(newStart, seg.start_ms, seg.speaker)
          insertSegment(idx, newSeg)
          selectSegment(newSeg.id)
          // 自动进入中文文本编辑模式
          setTimeout(() => handleDoubleClick(newSeg, 'text'), 50)
        },
      },
      {
        label: 'Insert After',
        onClick: () => {
          const newEnd = next ? next.start_ms : seg.end_ms + 1000
          const newSeg = makeEmptySeg(seg.end_ms, newEnd, seg.speaker)
          insertSegment(idx + 1, newSeg)
          selectSegment(newSeg.id)
          // 自动进入中文文本编辑模式
          setTimeout(() => handleDoubleClick(newSeg, 'text'), 50)
        },
      },
      {
        label: 'Merge with Next',
        shortcut: '\u2318M',
        onClick: () => mergeWithNext(segId),
        disabled: isLast,
        dividerAfter: true,
      },
      {
        label: seg.type === 'singing' ? 'Set as Speech' : 'Set as Singing',
        onClick: () => updateSegment(segId, { type: seg.type === 'singing' ? 'speech' : 'singing' }),
        dividerAfter: true,
      },
      {
        label: 'Delete',
        shortcut: '\u232B',
        onClick: () => {
          deleteSegment(segId)
          const remaining = segs.filter(s => s.id !== segId)
          if (remaining.length > 0) {
            const newIdx = Math.min(idx, remaining.length - 1)
            selectSegment(remaining[newIdx].id)
          }
        },
      },
    ]
  }, [model, currentTime, splitSegment, mergeWithNext, insertSegment, deleteSegment, selectSegment, makeEmptySeg, updateSegment])

  if (!model) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        Select an episode to begin
      </div>
    )
  }

  return (
    <div ref={parentRef} className="h-full overflow-auto">
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map(virtualRow => {
          const seg = segments[virtualRow.index]
          const isSelected = seg.id === selectedSegmentId
          const isPlayingNow = seg.id === playingSegmentId
          const isEditing = seg.id === editingId
          const bgColor = speakerColor(seg.speaker, speakerList)
          const badgeColor = speakerBadgeColor(seg.speaker, speakerList)
          const emoColor = emotionColor(seg.emotion)

          return (
            <div
              key={seg.id}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
                zIndex: dropdownSegId === seg.id ? 10 : 1,
              }}
              className={`
                flex items-start gap-2 px-3 py-1 border-b border-gray-700 cursor-pointer
                ${isPlayingNow ? 'bg-green-900/50 border-l-[3px] border-l-green-400' : bgColor}
                ${isSelected && !isPlayingNow ? 'ring-1 ring-blue-400' : ''}
                ${seg.flags.overlap ? 'border-l-2 border-l-red-500' : ''}
                ${seg.flags.needs_review ? 'border-l-2 border-l-yellow-500' : ''}
                hover:bg-gray-700/50
                transition-colors duration-150
              `}
              onClick={() => handleClick(seg)}
              onDoubleClick={() => handleDoubleClick(seg)}
              onContextMenu={e => handleContextMenu(e, seg)}
            >
              {/* Time */}
              <div className="text-xs text-gray-400 font-mono w-24 shrink-0 pt-1">
                {msToDisplay(seg.start_ms)}
              </div>

              {/* Speaker badge — click to open role dropdown */}
              <SpeakerBadge
                seg={seg}
                badgeColor={badgeColor}
                roles={roles}
                isOpen={dropdownSegId === seg.id && dropdownType === 'speaker'}
                onToggle={() => toggleDropdown(seg.id, 'speaker')}
                onClose={closeDropdown}
              />

              {/* Emotion badge — click to open dropdown */}
              <EmotionBadge
                seg={seg}
                emoColor={emoColor}
                emotions={emotions}
                isOpen={dropdownSegId === seg.id && dropdownType === 'emotion'}
                onToggle={() => toggleDropdown(seg.id, 'emotion')}
                onSelect={(emo) => { updateSegment(seg.id, { emotion: emo }); closeDropdown() }}
                onClose={closeDropdown}
              />

              {/* Type toggle: speech / singing */}
              {seg.type === 'singing' && (
                <button
                  onClick={e => { e.stopPropagation(); updateSegment(seg.id, { type: 'speech' }) }}
                  className="shrink-0 pt-1 text-xs px-1.5 py-0.5 rounded bg-pink-800 text-pink-200 hover:brightness-125"
                  title="Singing (click to switch to speech)"
                >
                  singing
                </button>
              )}

              {/* Text (Chinese + English) */}
              <div className="flex-1 min-w-0">
                {isEditing && editingField === 'text' ? (
                  <input
                    type="text"
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    onBlur={() => handleTextCommit(seg.id)}
                    onKeyDown={e => handleTextKeyDown(e, seg.id)}
                    className="w-full bg-gray-800 text-gray-100 px-2 py-1 rounded text-sm outline-none ring-1 ring-blue-400"
                    placeholder="输入中文原文..."
                    autoFocus
                  />
                ) : (
                  <div
                    className={`text-sm truncate px-1 py-0.5 rounded cursor-text hover:bg-gray-700/50 ${seg.text ? (isPlayingNow ? 'text-green-100 font-medium' : '') : 'text-gray-600 italic'}`}
                    onDoubleClick={e => { e.stopPropagation(); handleDoubleClick(seg, 'text') }}
                  >
                    {seg.text || '(空)'}
                  </div>
                )}
                {isEditing && editingField === 'text_en' ? (
                  <input
                    type="text"
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    onBlur={() => handleTextCommit(seg.id)}
                    onKeyDown={e => handleTextKeyDown(e, seg.id)}
                    className="w-full bg-gray-800 text-gray-400 px-2 py-1 rounded text-xs outline-none ring-1 ring-blue-400 mt-0.5"
                    placeholder="English translation..."
                    autoFocus
                  />
                ) : (
                  <div
                    className="text-xs text-gray-500 truncate px-1 py-0.5 rounded cursor-text hover:bg-gray-700/50 mt-0.5 min-h-[20px] leading-[20px]"
                    onDoubleClick={e => { e.stopPropagation(); handleDoubleClick(seg, 'text_en') }}
                  >
                    {seg.text_en || '\u00A0'}
                  </div>
                )}
              </div>

              {/* Duration */}
              <div className="text-xs text-gray-500 shrink-0 pt-1">
                {((seg.end_ms - seg.start_ms) / 1000).toFixed(1)}s
              </div>
            </div>
          )
        })}
      </div>

      {/* Context menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={buildContextMenuItems(contextMenu.segId)}
          onClose={closeContextMenu}
        />
      )}
    </div>
  )
}

/** Clickable speaker badge with role assignment dropdown + new role creation */
function SpeakerBadge({ seg, badgeColor, roles, isOpen, onToggle, onClose }: {
  seg: AsrSegment
  badgeColor: string
  roles: ReturnType<typeof useModelStore.getState>['roles']
  isOpen: boolean
  onToggle: () => void
  onClose: () => void
}) {
  const anchorRef = useRef<HTMLDivElement>(null)
  const updateSegment = useModelStore(s => s.updateSegment)
  const saveRoles = useModelStore(s => s.saveRoles)
  const roleIds = roles ? Object.keys(roles.roles) : []

  const [filter, setFilter] = useState('')
  const [highlightIdx, setHighlightIdx] = useState(-1)

  const ensureRoles = (): NonNullable<typeof roles> => {
    if (roles) return roles
    return { roles: {}, default_roles: {} }
  }

  const handleRoleSelect = (roleId: string) => {
    updateSegment(seg.id, { speaker: roleId || seg.speaker })
    onClose()
    setFilter('')
    setHighlightIdx(-1)
  }

  const handleAddRole = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed) return
    const rs = ensureRoles()
    const updated = {
      ...rs,
      roles: { ...rs.roles, [trimmed]: '' },
    }
    useModelStore.setState({ roles: updated })
    updateSegment(seg.id, { speaker: trimmed })
    setFilter('')
    setHighlightIdx(-1)
    onClose()
    setTimeout(() => saveRoles(), 0)
  }

  const filtered = filter
    ? roleIds.filter(rid => rid.toLowerCase().startsWith(filter.toLowerCase()))
    : roleIds
  const exactMatch = roleIds.some(rid => rid.toLowerCase() === filter.toLowerCase())
  // Total selectable items: filtered roles + optional "Create" item
  const hasCreate = filter.trim() !== '' && !exactMatch
  const totalItems = filtered.length + (hasCreate ? 1 : 0)
  const createIdx = hasCreate ? filtered.length : -1

  return (
    <div ref={anchorRef} className="shrink-0 pt-1 relative">
      <button
        onClick={e => { e.stopPropagation(); onToggle(); setFilter(''); setHighlightIdx(-1) }}
        className={`text-xs px-1.5 py-0.5 rounded ${badgeColor} text-white min-w-[24px] text-center hover:brightness-125 flex items-center gap-1`}
        title="Assign role"
      >
        {seg.speaker}
        <span className="text-[10px] opacity-60">▾</span>
      </button>
      {isOpen && (
        <InlineDropdown anchorRef={anchorRef} onClose={() => { onClose(); setFilter(''); setHighlightIdx(-1) }}>
          {/* Search / filter input */}
          <div className="px-2 py-1">
            <input
              type="text"
              value={filter}
              onChange={e => { setFilter(e.target.value); setHighlightIdx(-1) }}
              onKeyDown={e => {
                e.stopPropagation()
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setHighlightIdx(prev => (prev + 1) % totalItems)
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setHighlightIdx(prev => (prev - 1 + totalItems) % totalItems)
                } else if (e.key === 'Enter') {
                  if (highlightIdx >= 0 && highlightIdx < filtered.length) {
                    handleRoleSelect(filtered[highlightIdx])
                  } else if (highlightIdx === createIdx) {
                    handleAddRole(filter)
                  } else if (filtered.length === 1) {
                    handleRoleSelect(filtered[0])
                  } else if (hasCreate) {
                    handleAddRole(filter)
                  }
                } else if (e.key === 'Escape') {
                  onClose(); setFilter(''); setHighlightIdx(-1)
                }
              }}
              onClick={e => e.stopPropagation()}
              className="w-full bg-gray-700 text-gray-100 px-1.5 py-0.5 rounded text-xs outline-none ring-1 ring-gray-500 focus:ring-blue-400"
              placeholder="Search or create..."
              autoFocus
            />
          </div>
          {filtered.map((rid, i) => (
            <button
              key={rid}
              onClick={e => { e.stopPropagation(); handleRoleSelect(rid) }}
              onMouseEnter={() => setHighlightIdx(i)}
              className={`block w-full text-left px-2 py-1 ${
                i === highlightIdx ? 'bg-gray-600' : 'hover:bg-gray-700'
              } ${seg.speaker === rid ? 'text-blue-400' : 'text-gray-300'}`}
            >
              {rid}
            </button>
          ))}
          {hasCreate && (
            <div className="border-t border-gray-600 mt-1 pt-1">
              <button
                onClick={e => { e.stopPropagation(); handleAddRole(filter) }}
                onMouseEnter={() => setHighlightIdx(createIdx)}
                className={`block w-full text-left px-2 py-1 text-green-400 ${
                  highlightIdx === createIdx ? 'bg-gray-600' : 'hover:bg-gray-700'
                }`}
              >
                + Create "{filter.trim()}"
              </button>
            </div>
          )}
        </InlineDropdown>
      )}
    </div>
  )
}

/** Clickable emotion badge with inline dropdown + arrow key navigation */
function EmotionBadge({ seg, emoColor, emotions, isOpen, onToggle, onSelect, onClose }: {
  seg: AsrSegment
  emoColor: { bg: string; text: string }
  emotions: { key: string; name: string; lang: string[]; disabled?: boolean }[]
  isOpen: boolean
  onToggle: () => void
  onSelect: (emo: string) => void
  onClose: () => void
}) {
  const anchorRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const [highlightIdx, setHighlightIdx] = useState(-1)

  // Focus the list container when opened so it captures keyboard events
  useEffect(() => {
    if (isOpen) {
      setHighlightIdx(-1)
      // Delay focus to after render
      requestAnimationFrame(() => listRef.current?.focus())
    }
  }, [isOpen])

  return (
    <div ref={anchorRef} className="shrink-0 pt-1 relative">
      <button
        onClick={e => { e.stopPropagation(); onToggle() }}
        style={{ background: emoColor.bg, color: emoColor.text }}
        className="text-xs px-1.5 py-0.5 rounded hover:brightness-125 flex items-center gap-0.5"
        title="Change emotion"
      >
        {emotions.find(e => e.key === seg.emotion)?.name ?? seg.emotion}
        <span className="text-[10px] opacity-60">▾</span>
      </button>
      {isOpen && (
        <InlineDropdown anchorRef={anchorRef} onClose={onClose}>
          <div
            ref={listRef}
            tabIndex={-1}
            className="outline-none"
            onKeyDown={e => {
              e.stopPropagation()
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setHighlightIdx(prev => (prev + 1) % emotions.length)
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setHighlightIdx(prev => (prev - 1 + emotions.length) % emotions.length)
              } else if (e.key === 'Enter' && highlightIdx >= 0) {
                e.preventDefault()
                onSelect(emotions[highlightIdx].key)
                setHighlightIdx(-1)
              } else if (e.key === 'Escape') {
                onClose()
                setHighlightIdx(-1)
              }
            }}
          >
            {emotions.map((emo, i) => {
              const ec = emotionColor(emo.key)
              return (
                <button
                  key={emo.key}
                  onClick={e => { e.stopPropagation(); onSelect(emo.key) }}
                  onMouseEnter={() => setHighlightIdx(i)}
                  className={`block w-full text-left px-2 py-1 ${
                    i === highlightIdx ? 'bg-gray-600' : 'hover:bg-gray-700'
                  } ${seg.emotion === emo.key ? 'font-medium' : ''}`}
                  style={{ color: ec.text }}
                >
                  <span
                    className="inline-block w-2 h-2 rounded-full mr-1.5"
                    style={{ background: ec.bg }}
                  />
                  {emo.name}<span className="opacity-50 text-[10px] ml-1">({emo.lang.join(',')})</span>
                </button>
              )
            })}
          </div>
        </InlineDropdown>
      )}
    </div>
  )
}
