/** Transcript cue list with virtual scrolling */
import { useRef, useCallback, useState, useEffect, useMemo } from 'react'
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
import type { Cue, Role } from '../types/asr-model'
import { matchPinyin } from '../utils/pinyin-match'

/** Negative temp IDs for new cues */
let _nextTempId = -1

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

function speakerColor(speaker: number, speakers: number[]): string {
  const idx = speakers.indexOf(speaker)
  return SPEAKER_COLORS[idx >= 0 ? idx % SPEAKER_COLORS.length : 0]
}

function speakerBadgeColor(speaker: number, speakers: number[]): string {
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
  const cues = useModelStore(s => s.cues)
  const loaded = useModelStore(s => s.loaded)
  const updateCue = useModelStore(s => s.updateCue)
  const selectedCueId = useEditorStore(s => s.selectedCueId)
  const selectCue = useEditorStore(s => s.selectCue)
  const setCurrentTime = useEditorStore(s => s.setCurrentTime)
  const playingCueId = useEditorStore(s => s.playingCueId)
  const setPlayingCue = useEditorStore(s => s.setPlayingCue)
  const isPlaying = useEditorStore(s => s.isPlaying)
  // Read currentTime via ref to avoid re-rendering TranscriptList on every timeupdate (~4/s)
  const currentTimeRef = useRef(useEditorStore.getState().currentTime)
  const { splitCue, mergeWithNext, insertCue, deleteCue } = useUndoableOps()

  const roles = useModelStore(s => s.roles)
  const emotions = useModelStore(s => s.emotions)

  const speakerList = deriveSpeakers(cues)

  const parentRef = useRef<HTMLDivElement>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingField, setEditingField] = useState<'text' | 'text_en'>('text')
  const [editText, setEditText] = useState('')

  // Dropdown state: which cue + which dropdown type is open
  const [dropdownCueId, setDropdownCueId] = useState<number | null>(null)
  const [dropdownType, setDropdownType] = useState<'speaker' | 'emotion' | null>(null)

  const virtualizer = useVirtualizer({
    count: cues.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 76,
    overscan: 10,
  })

  // Track which cue is currently playing via subscribe (avoids re-render on every timeupdate)
  const cuesRef = useRef(cues)
  cuesRef.current = cues
  useEffect(() => {
    const unsub = useEditorStore.subscribe((state, prev) => {
      if (state.currentTime === prev.currentTime) return
      currentTimeRef.current = state.currentTime
      if (!state.isPlaying || !cuesRef.current.length) return
      const t = state.currentTime
      const cue = cuesRef.current.find(c => c.start_ms <= t && t < c.end_ms)
      if (cue && cue.id !== state.playingCueId) {
        state.setPlayingCue(cue.id)
      }
    })
    return unsub
  }, [])

  // Clear playing cue when playback stops
  useEffect(() => {
    if (!isPlaying) {
      setPlayingCue(null)
    }
  }, [isPlaying, setPlayingCue])

  // Stable ref to virtualizer to avoid infinite re-render loops
  const virtualizerRef = useRef(virtualizer)
  virtualizerRef.current = virtualizer

  // Auto-scroll to playing cue during playback
  useEffect(() => {
    if (playingCueId == null || editingId != null) return
    const idx = cues.findIndex(c => c.id === playingCueId)
    if (idx >= 0) {
      requestAnimationFrame(() => {
        virtualizerRef.current.scrollToIndex(idx, { align: 'auto' })
      })
    }
  }, [playingCueId, cues, editingId])

  // Scroll to user-selected cue — only when selection actually changes
  const prevSelectedRef = useRef<number | null>(null)
  useEffect(() => {
    if (selectedCueId == null || selectedCueId === prevSelectedRef.current) return
    prevSelectedRef.current = selectedCueId
    const idx = cues.findIndex(c => c.id === selectedCueId)
    if (idx >= 0) {
      virtualizerRef.current.scrollToIndex(idx, { align: 'auto' })
    }
  }, [selectedCueId, cues])

  const handleClick = useCallback((cue: Cue) => {
    selectCue(cue.id)
    // Seek to cue start (playhead + video both go to same position)
    setCurrentTime(cue.start_ms)
    const video = document.querySelector('video')
    if (video) {
      video.currentTime = cue.start_ms / 1000
    }
  }, [selectCue, setCurrentTime])

  const handleDoubleClick = useCallback((cue: Cue, field: 'text' | 'text_en' = 'text') => {
    setEditingId(cue.id)
    setEditingField(field)
    setEditText(field === 'text' ? cue.text : (cue.text_en ?? ''))
  }, [])

  const handleTextCommit = useCallback((id: number) => {
    const trimmed = editText.trim()
    updateCue(id, { [editingField]: trimmed })
    setEditingId(null)
  }, [editText, editingField, updateCue])

  const handleTextKeyDown = useCallback((e: React.KeyboardEvent, id: number) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleTextCommit(id)
    } else if (e.key === 'Escape') {
      setEditingId(null)
    }
  }, [handleTextCommit])

  const toggleDropdown = useCallback((cueId: number, type: 'speaker' | 'emotion') => {
    if (dropdownCueId === cueId && dropdownType === type) {
      setDropdownCueId(null)
      setDropdownType(null)
    } else {
      setDropdownCueId(cueId)
      setDropdownType(type)
    }
  }, [dropdownCueId, dropdownType])

  const closeDropdown = useCallback(() => {
    setDropdownCueId(null)
    setDropdownType(null)
  }, [])

  // Context menu state
  const [contextMenu, setContextMenu] = useState<{ cueId: number; x: number; y: number } | null>(null)

  const handleContextMenu = useCallback((e: React.MouseEvent, cue: Cue) => {
    e.preventDefault()
    e.stopPropagation()
    selectCue(cue.id)
    setContextMenu({ cueId: cue.id, x: e.clientX, y: e.clientY })
  }, [selectCue])

  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  /** Helper: create a new empty cue */
  const makeEmptyCue = useCallback((startMs: number, endMs: number, speaker: number): Cue => ({
    id: _nextTempId--,
    episode_id: 0,
    text: '',
    start_ms: startMs,
    end_ms: Math.max(endMs, startMs + 100),
    speaker,
    emotion: 'neutral',
    kind: 'speech',
  }), [])

  const buildContextMenuItems = useCallback((cueId: number): ContextMenuItem[] => {
    const idx = cues.findIndex(c => c.id === cueId)
    if (idx < 0) return []
    const cue = cues[idx]
    const isLast = idx >= cues.length - 1
    const isFirst = idx === 0
    const ct = currentTimeRef.current
    const canSplit = ct > cue.start_ms && ct < cue.end_ms

    const prev = !isFirst ? cues[idx - 1] : null
    const next = !isLast ? cues[idx + 1] : null

    return [
      {
        label: '在播放头处分割',
        shortcut: '\u2318B',
        onClick: () => splitCue(cueId, ct),
        disabled: !canSplit,
        dividerAfter: true,
      },
      {
        label: '在前方插入',
        onClick: () => {
          const newStart = prev ? prev.end_ms : Math.max(0, cue.start_ms - 1000)
          const newCue = makeEmptyCue(newStart, cue.start_ms, cue.speaker)
          insertCue(idx, newCue)
          selectCue(newCue.id)
          setTimeout(() => handleDoubleClick(newCue, 'text'), 50)
        },
      },
      {
        label: '在后方插入',
        onClick: () => {
          const newEnd = next ? next.start_ms : cue.end_ms + 1000
          const newCue = makeEmptyCue(cue.end_ms, newEnd, cue.speaker)
          insertCue(idx + 1, newCue)
          selectCue(newCue.id)
          setTimeout(() => handleDoubleClick(newCue, 'text'), 50)
        },
      },
      {
        label: '与下一条合并',
        shortcut: '\u2318M',
        onClick: () => mergeWithNext(cueId),
        disabled: isLast,
        dividerAfter: true,
      },
      {
        label: cue.kind === 'singing' ? '设为对白' : '设为歌唱',
        onClick: () => updateCue(cueId, { kind: cue.kind === 'singing' ? 'speech' : 'singing' }),
        dividerAfter: true,
      },
      {
        label: '删除',
        shortcut: '\u232B',
        onClick: () => {
          deleteCue(cueId)
          const remaining = cues.filter(c => c.id !== cueId)
          if (remaining.length > 0) {
            const newIdx = Math.min(idx, remaining.length - 1)
            selectCue(remaining[newIdx].id)
          }
        },
      },
    ]
  }, [cues, splitCue, mergeWithNext, insertCue, deleteCue, selectCue, makeEmptyCue, updateCue])

  if (!loaded) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        请选择一集开始编辑
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
          const cue = cues[virtualRow.index]
          const isSelected = cue.id === selectedCueId
          const isPlayingNow = cue.id === playingCueId
          const isEditing = cue.id === editingId
          const bgColor = speakerColor(cue.speaker, speakerList)
          const isInvalidRole = roles.length > 0 && !roles.some(r => r.id === cue.speaker)
          const badgeColor = isInvalidRole ? 'bg-gray-600' : speakerBadgeColor(cue.speaker, speakerList)
          const emoColor = emotionColor(cue.emotion)

          return (
            <div
              key={cue.id}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
                zIndex: dropdownCueId === cue.id ? 10 : 1,
              }}
              className={`
                flex items-start gap-2 px-3 py-1 border-b border-gray-700 cursor-pointer
                ${isPlayingNow ? 'bg-green-900/50 border-l-[3px] border-l-green-400' : bgColor}
                ${isSelected && !isPlayingNow ? 'ring-1 ring-blue-400' : ''}
                hover:bg-gray-700/50
                transition-colors duration-150
              `}
              onClick={() => handleClick(cue)}
              onDoubleClick={() => handleDoubleClick(cue)}
              onContextMenu={e => handleContextMenu(e, cue)}
            >
              {/* Time: start + end */}
              <div className="text-xs text-gray-400 font-mono w-28 shrink-0 pt-1 leading-tight">
                <div>{msToDisplay(cue.start_ms)}</div>
                <div className="text-gray-600">{msToDisplay(cue.end_ms)}</div>
              </div>

              {/* Speaker badge — click to open role dropdown */}
              <SpeakerBadge
                cue={cue}
                badgeColor={badgeColor}
                roles={roles}
                isOpen={dropdownCueId === cue.id && dropdownType === 'speaker'}
                onToggle={() => toggleDropdown(cue.id, 'speaker')}
                onClose={closeDropdown}
              />

              {/* Emotion badge — click to open dropdown */}
              <EmotionBadge
                cue={cue}
                emoColor={emoColor}
                emotions={emotions}
                isOpen={dropdownCueId === cue.id && dropdownType === 'emotion'}
                onToggle={() => toggleDropdown(cue.id, 'emotion')}
                onSelect={(emo) => { updateCue(cue.id, { emotion: emo }); closeDropdown() }}
                onClose={closeDropdown}
              />

              {/* Type toggle: speech / singing */}
              {cue.kind === 'singing' && (
                <button
                  onClick={e => { e.stopPropagation(); updateCue(cue.id, { kind: 'speech' }) }}
                  className="shrink-0 pt-1 text-xs px-1.5 py-0.5 rounded bg-pink-800 text-pink-200 hover:brightness-125"
                  title="歌唱（点击切换为对白）"
                >
                  歌唱
                </button>
              )}

              {/* Text (Chinese + English) */}
              <div className="flex-1 min-w-0">
                {isEditing && editingField === 'text' ? (
                  <input
                    type="text"
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    onBlur={() => handleTextCommit(cue.id)}
                    onKeyDown={e => handleTextKeyDown(e, cue.id)}
                    className="w-full bg-gray-800 text-gray-100 px-2 py-1 rounded text-sm outline-none ring-1 ring-blue-400"
                    placeholder="输入中文原文..."
                    autoFocus
                  />
                ) : (
                  <div
                    className={`text-sm truncate px-1 py-0.5 rounded cursor-text hover:bg-gray-700/50 ${cue.text ? (isPlayingNow ? 'text-green-100 font-medium' : '') : 'text-gray-600 italic'}`}
                    onDoubleClick={e => { e.stopPropagation(); handleDoubleClick(cue, 'text') }}
                  >
                    {cue.text || '(空)'}
                  </div>
                )}
                <div
                  className={`text-xs truncate px-1 py-0.5 rounded mt-0.5 min-h-[20px] leading-[20px] ${
                    cue.text_en
                      ? 'text-gray-500'
                      : 'text-gray-600 italic'
                  }`}
                >
                  {cue.text_en || '暂无翻译'}
                </div>
              </div>

              {/* Duration */}
              <div className="text-xs text-gray-500 shrink-0 pt-1">
                {((cue.end_ms - cue.start_ms) / 1000).toFixed(1)}s
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
          items={buildContextMenuItems(contextMenu.cueId)}
          onClose={closeContextMenu}
        />
      )}
    </div>
  )
}

/** role_type sort priority: lead > supporting > extra > narrator */
const ROLE_TYPE_PRIORITY: Record<string, number> = { lead: 0, supporting: 1, extra: 2, narrator: 3 }

function roleTypePriority(rt: string): number {
  return ROLE_TYPE_PRIORITY[rt] ?? 2
}

/** Sort roles by role_type priority, then name alphabetically */
function sortRolesByType(roles: Role[]): Role[] {
  return [...roles].sort((a, b) => {
    const pa = roleTypePriority(a.role_type)
    const pb = roleTypePriority(b.role_type)
    if (pa !== pb) return pa - pb
    return a.name.localeCompare(b.name, 'zh-Hans-CN')
  })
}

/** Clickable speaker badge with role assignment dropdown (tab: Recent | All) */
function SpeakerBadge({ cue, badgeColor, roles, isOpen, onToggle, onClose }: {
  cue: Cue
  badgeColor: string
  roles: Role[]
  isOpen: boolean
  onToggle: () => void
  onClose: () => void
}) {
  const anchorRef = useRef<HTMLDivElement>(null)
  const updateCue = useModelStore(s => s.updateCue)
  const cues = useModelStore(s => s.cues)

  const [filter, setFilter] = useState('')
  const [highlightIdx, setHighlightIdx] = useState(-1)
  const [tab, setTab] = useState<'recent' | 'all'>('recent')

  /** Display name for the current speaker */
  const speakerName = roles.find(r => r.id === cue.speaker)?.name ?? String(cue.speaker)

  const handleRoleSelect = (roleId: number) => {
    updateCue(cue.id, { speaker: roleId })
    onClose()
    setFilter('')
    setHighlightIdx(-1)
  }

  const handleAddRole = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed) return
    const tempId = -Date.now()
    const newRole: Role = { id: tempId, name: trimmed, voice_type: '', role_type: 'extra' }
    const currentRoles = useModelStore.getState().roles
    useModelStore.setState({ roles: [...currentRoles, newRole] })
    setFilter('')
    setHighlightIdx(-1)
    onClose()
    const doSave = async () => {
      await useModelStore.getState().saveRoles()
      const savedRoles = useModelStore.getState().roles
      const saved = savedRoles.find(r => r.name === trimmed)
      if (saved) {
        useModelStore.getState().updateCue(cue.id, { speaker: saved.id })
      }
    }
    doSave()
  }

  // Recent speakers: unique speaker IDs by cue position (reverse order)
  const recentRoles = useMemo(() => {
    const seen = new Set<number>()
    const result: Role[] = []
    for (let i = cues.length - 1; i >= 0; i--) {
      const spk = cues[i].speaker
      if (spk && !seen.has(spk)) {
        seen.add(spk)
        const role = roles.find(r => r.id === spk)
        if (role) result.push(role)
      }
    }
    return result
  }, [cues, roles])

  const allRolesSorted = useMemo(() => sortRolesByType(roles), [roles])

  const isFiltering = filter.trim() !== ''

  // The visible list for the current state (filter overrides tabs, supports pinyin initials)
  const visibleRoles = useMemo(() => {
    if (isFiltering) {
      return sortRolesByType(roles.filter(r => matchPinyin(r.name, filter.trim())))
    }
    return tab === 'recent' ? recentRoles : allRolesSorted
  }, [isFiltering, filter, roles, tab, recentRoles, allRolesSorted])

  const exactMatch = roles.some(r => r.name.toLowerCase() === filter.toLowerCase())
  const hasCreate = isFiltering && !exactMatch
  const totalItems = visibleRoles.length + (hasCreate ? 1 : 0)
  const createIdx = hasCreate ? visibleRoles.length : -1

  const selectByIndex = (idx: number) => {
    if (idx >= 0 && idx < visibleRoles.length) {
      handleRoleSelect(visibleRoles[idx].id)
    } else if (idx === createIdx) {
      handleAddRole(filter)
    }
  }

  return (
    <div ref={anchorRef} className="shrink-0 pt-1 relative">
      <button
        onClick={e => { e.stopPropagation(); onToggle(); setFilter(''); setHighlightIdx(-1); setTab('recent') }}
        className={`text-xs px-1.5 py-0.5 rounded ${badgeColor} text-white min-w-[24px] text-center hover:brightness-125 flex items-center gap-1`}
        title="分配角色"
      >
        {speakerName}
        <span className="text-[10px] opacity-60">▾</span>
      </button>
      {isOpen && (
        <InlineDropdown anchorRef={anchorRef} onClose={() => { onClose(); setFilter(''); setHighlightIdx(-1) }}>
          {/* Search input */}
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
                  if (highlightIdx >= 0) {
                    selectByIndex(highlightIdx)
                  } else if (visibleRoles.length === 1) {
                    handleRoleSelect(visibleRoles[0].id)
                  } else if (hasCreate) {
                    handleAddRole(filter)
                  }
                } else if (e.key === 'Escape') {
                  onClose(); setFilter(''); setHighlightIdx(-1)
                }
              }}
              onClick={e => e.stopPropagation()}
              className="w-full bg-gray-700 text-gray-100 px-1.5 py-0.5 rounded text-xs outline-none ring-1 ring-gray-500 focus:ring-blue-400"
              placeholder="搜索或创建..."
              autoFocus
            />
          </div>
          {/* Tabs (hidden when filtering) */}
          {!isFiltering && (
            <div className="flex border-b border-gray-700 mx-1">
              <button
                onClick={e => { e.stopPropagation(); setTab('recent'); setHighlightIdx(-1) }}
                className={`flex-1 text-[10px] py-1 text-center ${
                  tab === 'recent' ? 'text-blue-400 border-b border-blue-400' : 'text-gray-500 hover:text-gray-300'
                }`}
              >常用</button>
              <button
                onClick={e => { e.stopPropagation(); setTab('all'); setHighlightIdx(-1) }}
                className={`flex-1 text-[10px] py-1 text-center ${
                  tab === 'all' ? 'text-blue-400 border-b border-blue-400' : 'text-gray-500 hover:text-gray-300'
                }`}
              >全部</button>
            </div>
          )}
          {/* Role list */}
          {visibleRoles.map((role, i) => (
            <button
              key={role.id}
              onClick={e => { e.stopPropagation(); handleRoleSelect(role.id) }}
              onMouseEnter={() => setHighlightIdx(i)}
              className={`block w-full text-left px-2 py-1 text-xs ${
                i === highlightIdx ? 'bg-gray-600' : 'hover:bg-gray-700'
              } ${cue.speaker === role.id ? 'text-blue-400' : 'text-gray-300'}`}
            >
              {role.name}
              {(isFiltering || tab === 'all') && (
                <span className="text-[10px] text-gray-500 ml-1">({role.role_type})</span>
              )}
            </button>
          ))}
          {!isFiltering && visibleRoles.length === 0 && (
            <div className="px-2 py-2 text-[10px] text-gray-600 italic text-center">
              {tab === 'recent' ? '暂无常用角色' : '暂无角色'}
            </div>
          )}
          {hasCreate && (
            <div className="border-t border-gray-600 mt-1 pt-1">
              <button
                onClick={e => { e.stopPropagation(); handleAddRole(filter) }}
                onMouseEnter={() => setHighlightIdx(createIdx)}
                className={`block w-full text-left px-2 py-1 text-green-400 ${
                  highlightIdx === createIdx ? 'bg-gray-600' : 'hover:bg-gray-700'
                }`}
              >
                + 创建「{filter.trim()}」
              </button>
            </div>
          )}
        </InlineDropdown>
      )}
    </div>
  )
}

/** Clickable emotion badge with inline dropdown + arrow key navigation */
function EmotionBadge({ cue, emoColor, emotions, isOpen, onToggle, onSelect, onClose }: {
  cue: Cue
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
        title="更改情绪"
      >
        {emotions.find(e => e.key === cue.emotion)?.name ?? cue.emotion}
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
                  } ${cue.emotion === emo.key ? 'font-medium' : ''}`}
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
