/** VoiceCasting: assign voices to roles with trial previews + inline synthesis */
import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import type { Role } from '../types/asr-model'
import { useModelStore } from '../stores/model-store'
import { matchPinyin } from '../utils/pinyin-match'

// ── constants ───────────────────────────────────────────────────────────────

const ROLE_TYPES = ['lead', 'supporting', 'extra', 'narrator'] as const
const ROLE_TYPE_LABELS: Record<string, string> = { lead: '主', supporting: '配', extra: '群', narrator: '旁白' }
const ROLE_TYPE_COLORS: Record<string, string> = {
  lead: 'bg-red-600/60 text-red-200',
  supporting: 'bg-yellow-600/60 text-yellow-200',
  extra: 'bg-gray-600/60 text-gray-300',
  narrator: 'bg-purple-600/60 text-purple-200',
}
const ROLE_TYPE_PRI: Record<string, number> = { lead: 0, supporting: 1, extra: 2, narrator: 3 }

const DEFAULT_TEXT =
  'I never thought this day would come, but here we are. ' +
  'After everything we have been through, all the promises, all the lies, ' +
  'you still have the nerve to stand in front of me and act like nothing happened.'

const LS_RECENT_VOICES_KEY = 'dubora:recentVoices'

// ── types ───────────────────────────────────────────────────────────────────

interface Emotion {
  value: string
  label: string
  icon: string
}

interface VoiceLang {
  lang: string
  text: string
  flag: string
}

interface Voice {
  name: string
  voice_id: string
  gender: string
  age: string
  description: string
  avatar: string
  trial_url: string
  categories: string[]
  languages: VoiceLang[]
  emotions: Emotion[]
  resource_id: string
}

interface HistoryEntry {
  key: string
  voice_id: string
  voice_name: string
  emotion: string
  text: string
  created_at: string
}

interface Props {
  onBack?: () => void
  dramas?: string[]
  initialDrama: string
  embedded?: boolean
}

// ── component ───────────────────────────────────────────────────────────────

export function VoicePreview({ onBack, dramas, initialDrama, embedded }: Props) {
  const [drama, setDrama] = useState(initialDrama)
  // voices
  const [voices, setVoices] = useState<Voice[]>([])
  const [loadError, setLoadError] = useState('')
  const [catFilter, setCatFilter] = useState('all')
  const [genderFilter, setGenderFilter] = useState('all')

  // roles (from store, DB-backed)
  const roles = useModelStore(s => s.roles)
  const loadRoles = useModelStore(s => s.loadRoles)
  const updateRoles = useModelStore(s => s.updateRoles)
  const [selectedRole, setSelectedRole] = useState<number | null>(null)
  const [addingRole, setAddingRole] = useState(false)
  const [newRoleName, setNewRoleName] = useState('')
  const [roleSearch, setRoleSearch] = useState('')
  const [expandingTypeBadge, setExpandingTypeBadge] = useState<number | null>(null)
  const [menuRoleId, setMenuRoleId] = useState<number | null>(null)

  // inline player: track which item is playing
  const [playingKey, setPlayingKey] = useState<string | null>(null)
  const [playingUrl, setPlayingUrl] = useState<string | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [playProgress, setPlayProgress] = useState(0)
  const audioRef = useRef<HTMLAudioElement>(null)

  // inline synthesis (per-voice)
  const [expandedVoice, setExpandedVoice] = useState<string | null>(null)
  const [selectedEmotion, setSelectedEmotion] = useState('')
  const [text, setText] = useState(DEFAULT_TEXT)
  const [synthesizing, setSynthesizing] = useState(false)
  const [synthError, setSynthError] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([])

  // recently used voices (localStorage)
  const [recentVoiceIds, setRecentVoiceIds] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(LS_RECENT_VOICES_KEY) || '[]') }
    catch { return [] }
  })

  // scroll-to-voice
  const scrollToVoiceRef = useRef<string | null>(null)

  // refs for click-outside detection
  const badgeContainerRef = useRef<HTMLDivElement>(null)
  const menuContainerRef = useRef<HTMLDivElement>(null)

  // ── voiceMap ──────────────────────────────────────────────────────────

  const voiceMap = useMemo(() => {
    const m: Record<string, Voice> = {}
    for (const v of voices) m[v.voice_id] = v
    return m
  }, [voices])

  // ── load data ─────────────────────────────────────────────────────────

  useEffect(() => {
    fetch('/api/voices')
      .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json() })
      .then((data: Voice[]) => setVoices(data))
      .catch(e => setLoadError(e.message))
  }, [])

  useEffect(() => {
    fetch('/api/voices/history')
      .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json() })
      .then((data: HistoryEntry[]) => setHistory(data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    setSelectedRole(null)
    if (!drama) return
    useModelStore.setState({ currentDrama: drama })
    loadRoles(drama)
  }, [drama, loadRoles])

  // ── derived ───────────────────────────────────────────────────────────

  const allCategories = useMemo(() => {
    const set = new Set<string>()
    for (const v of voices) v.categories.forEach(c => set.add(c))
    return Array.from(set)
  }, [voices])

  const filteredVoices = useMemo(() => {
    return voices.filter(v => {
      if (catFilter !== 'all' && !v.categories.includes(catFilter)) return false
      if (genderFilter !== 'all' && v.gender !== genderFilter) return false
      return true
    })
  }, [voices, catFilter, genderFilter])

  const assignedVoiceId = useMemo(() => {
    if (selectedRole == null) return null
    const role = roles.find(r => r.id === selectedRole)
    return role?.voice_type ?? null
  }, [roles, selectedRole])

  // Sorted roles by role_type priority, then name
  const sortedRoles = useMemo(() => {
    return [...roles].sort((a, b) => {
      const pa = ROLE_TYPE_PRI[a.role_type] ?? 2
      const pb = ROLE_TYPE_PRI[b.role_type] ?? 2
      if (pa !== pb) return pa - pb
      return a.name.localeCompare(b.name, 'zh-Hans-CN')
    })
  }, [roles])

  // Filtered by search (supports pinyin initials)
  const displayedRoles = useMemo(() => {
    if (!roleSearch.trim()) return sortedRoles
    return sortedRoles.filter(r => matchPinyin(r.name, roleSearch.trim()))
  }, [sortedRoles, roleSearch])

  // Recently used voices (resolved from voiceMap)
  const recentVoices = useMemo(() =>
    recentVoiceIds.map(id => voiceMap[id]).filter((v): v is Voice => !!v),
    [recentVoiceIds, voiceMap],
  )

  // sync emotion + clear error when expanded voice changes
  useEffect(() => {
    setSynthError('')
    if (!expandedVoice) return
    const voice = voiceMap[expandedVoice]
    const emotions = voice?.emotions ?? []
    if (emotions.length > 0) {
      setSelectedEmotion(emotions[0].value)
    } else {
      setSelectedEmotion('')
    }
  }, [expandedVoice, voiceMap])

  // ── scroll to assigned voice ──────────────────────────────────────────

  useEffect(() => {
    const targetId = scrollToVoiceRef.current
    if (!targetId) return
    scrollToVoiceRef.current = null
    setTimeout(() => {
      document.getElementById(`voice-${targetId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 50)
  }, [selectedRole])

  // ── audio event listeners ────────────────────────────────────────────

  useEffect(() => {
    const el = audioRef.current
    if (!el) return
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)
    const onEnded = () => { setIsPlaying(false); setPlayProgress(0) }
    const onTime = () => {
      if (el.duration > 0) setPlayProgress(el.currentTime / el.duration)
    }
    el.addEventListener('play', onPlay)
    el.addEventListener('pause', onPause)
    el.addEventListener('ended', onEnded)
    el.addEventListener('timeupdate', onTime)
    return () => {
      el.removeEventListener('play', onPlay)
      el.removeEventListener('pause', onPause)
      el.removeEventListener('ended', onEnded)
      el.removeEventListener('timeupdate', onTime)
    }
  }, [])

  // ── keyboard: Space to toggle play/pause ─────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.key === ' ') {
        e.preventDefault()
        const el = audioRef.current
        if (!el || !playingKey) return
        if (el.paused) el.play()
        else el.pause()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [playingKey])

  // ── click-outside: close badge expander ──────────────────────────────

  useEffect(() => {
    if (expandingTypeBadge == null) return
    function handler(e: MouseEvent) {
      if (badgeContainerRef.current && !badgeContainerRef.current.contains(e.target as Node)) {
        setExpandingTypeBadge(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [expandingTypeBadge])

  // ── click-outside: close three-dot menu ──────────────────────────────

  useEffect(() => {
    if (menuRoleId == null) return
    function handler(e: MouseEvent) {
      if (menuContainerRef.current && !menuContainerRef.current.contains(e.target as Node)) {
        setMenuRoleId(null)
        setConfirmDelete(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuRoleId])

  // ── play helper ───────────────────────────────────────────────────────

  const togglePlay = useCallback((key: string, url: string) => {
    const el = audioRef.current
    if (!el) return
    if (playingKey === key) {
      if (el.paused) el.play()
      else el.pause()
    } else {
      setPlayingKey(key)
      setPlayingUrl(url)
      setPlayProgress(0)
      el.src = url
      el.load()
      el.play()
    }
  }, [playingKey])

  const handleTrial = useCallback((voice: Voice) => {
    if (voice.trial_url) togglePlay(`trial:${voice.voice_id}`, voice.trial_url)
  }, [togglePlay])

  // ── assign voice (+ track recent) ─────────────────────────────────────

  const handleAssign = useCallback((voiceId: string) => {
    if (selectedRole == null) return
    const current = roles.find(r => r.id === selectedRole)
    if (current?.voice_type === voiceId) return
    updateRoles(roles.map(r => r.id === selectedRole ? { ...r, voice_type: voiceId } : r))
    // Track recent voices in localStorage
    setRecentVoiceIds(prev => {
      const next = [voiceId, ...prev.filter(id => id !== voiceId)].slice(0, 5)
      localStorage.setItem(LS_RECENT_VOICES_KEY, JSON.stringify(next))
      return next
    })
  }, [roles, selectedRole, updateRoles])

  const handleUnassign = useCallback((roleId: number) => {
    updateRoles(roles.map(r => r.id === roleId ? { ...r, voice_type: '' } : r))
    setMenuRoleId(null)
  }, [roles, updateRoles])

  // ── select role ───────────────────────────────────────────────────────

  const handleSelectRole = useCallback((roleId: number) => {
    setSelectedRole(roleId)
    setExpandingTypeBadge(null)
    const role = roles.find(r => r.id === roleId)
    if (role?.voice_type) scrollToVoiceRef.current = role.voice_type
  }, [roles])

  // ── inline synthesis ──────────────────────────────────────────────────

  const handleSynthesize = useCallback(async () => {
    if (!expandedVoice || !text.trim()) return
    setSynthesizing(true)
    setSynthError('')
    try {
      const body: Record<string, string> = { voice_id: expandedVoice, text: text.trim() }
      if (selectedEmotion) body.emotion = selectedEmotion

      const res = await fetch('/api/voices/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail ?? `HTTP ${res.status}`)
      }
      const data = await res.json()
      const voice = voiceMap[expandedVoice]
      togglePlay(`hist:${data.key}`, data.audio_url)

      const newEntry: HistoryEntry = {
        key: data.key,
        voice_id: expandedVoice,
        voice_name: voice?.name ?? expandedVoice,
        emotion: selectedEmotion,
        text: text.trim(),
        created_at: new Date().toISOString(),
      }
      setHistory(prev => prev.some(h => h.key === data.key) ? prev : [newEntry, ...prev])
    } catch (e: any) {
      setSynthError(e.message ?? String(e))
    } finally {
      setSynthesizing(false)
    }
  }, [expandedVoice, selectedEmotion, text, voiceMap, togglePlay])

  // ── download ──────────────────────────────────────────────────────────

  const handleDownload = useCallback(() => {
    if (!playingUrl) return
    const a = document.createElement('a')
    a.href = playingUrl
    a.download = `${playingKey?.replace(/[/:]+/g, '_') || 'voice'}.wav`
    a.click()
  }, [playingUrl, playingKey])

  // ── role management ───────────────────────────────────────────────────

  const handleAddNewRole = useCallback(() => {
    const name = newRoleName.trim()
    if (!name) return
    if (roles.some(r => r.name === name)) return
    const newRole: Role = { id: -Date.now(), name, voice_type: '', role_type: 'extra' }
    updateRoles([...roles, newRole])
    setNewRoleName('')
    setAddingRole(false)
    setSelectedRole(newRole.id)
  }, [newRoleName, roles, updateRoles])

  const handleChangeRoleType = useCallback((roleId: number, newType: string) => {
    updateRoles(roles.map(r => r.id === roleId ? { ...r, role_type: newType } : r))
    setExpandingTypeBadge(null)
  }, [roles, updateRoles])

  const [renamingRole, setRenamingRole] = useState<number | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)

  const handleStartRename = useCallback((role: Role) => {
    setRenamingRole(role.id)
    setRenameValue(role.name)
    setMenuRoleId(null)
  }, [])

  const handleFinishRename = useCallback(() => {
    if (renamingRole == null) return
    const name = renameValue.trim()
    if (!name || roles.some(r => r.id !== renamingRole && r.name === name)) {
      setRenamingRole(null)
      return
    }
    const old = roles.find(r => r.id === renamingRole)
    if (old && old.name !== name) {
      updateRoles(roles.map(r => r.id === renamingRole ? { ...r, name } : r))
    }
    setRenamingRole(null)
  }, [renamingRole, renameValue, roles, updateRoles])

  const handleDeleteRole = useCallback((roleId: number) => {
    if (confirmDelete !== roleId) {
      setConfirmDelete(roleId)
      return
    }
    updateRoles(roles.filter(r => r.id !== roleId))
    setConfirmDelete(null)
    setMenuRoleId(null)
    if (selectedRole === roleId) setSelectedRole(null)
  }, [roles, selectedRole, confirmDelete, updateRoles])

  const hasSelection = selectedRole !== null

  // ── render ────────────────────────────────────────────────────────────

  if (loadError) {
    return (
      <div className="h-full flex items-center justify-center text-red-400">
        Failed to load voices: {loadError}
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col bg-gray-900 text-gray-100">
      {/* Header */}
      {!embedded && (
        <header className="flex items-center gap-3 px-4 py-2 bg-gray-800 border-b border-gray-700 shrink-0">
          {onBack && <button onClick={onBack} className="text-sm text-gray-400 hover:text-gray-200">&larr; Back</button>}
          <h1 className="text-sm font-bold text-gray-300">Voice Casting</h1>
          {dramas && (
            <select
              value={drama}
              onChange={e => setDrama(e.target.value)}
              className="bg-gray-700 text-gray-200 text-sm rounded px-2 py-1 outline-none"
            >
              <option value="">Select drama...</option>
              {dramas.map(d => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          )}
          <div className="flex-1" />
          {synthError && <span className="text-xs text-red-400 mr-2">{synthError}</span>}
        </header>
      )}

      {/* Hidden audio element */}
      <audio ref={audioRef} className="hidden" />

      {/* Main split layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* ── Left: Roles panel (260px) ──────────────────────────────── */}
        <div className="w-[260px] shrink-0 border-r border-gray-700 flex flex-col">
          {!drama ? (
            <div className="flex-1 flex items-center justify-center text-xs text-gray-500 px-4 text-center">
              Select a drama to start voice casting
            </div>
          ) : (
            <>
              {/* Header + search */}
              <div className="px-3 pt-2 pb-1 space-y-1.5 shrink-0">
                <div className="flex items-center justify-between">
                  <h2 className="text-[10px] text-gray-500 uppercase tracking-wide">Roles</h2>
                  <button
                    onClick={() => setAddingRole(true)}
                    className="text-[10px] text-gray-500 hover:text-gray-300 px-1"
                    title="Add role"
                  >+ Add</button>
                </div>
                {addingRole && (
                  <div className="flex gap-1">
                    <input
                      type="text"
                      value={newRoleName}
                      onChange={e => setNewRoleName(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') handleAddNewRole()
                        if (e.key === 'Escape') { setAddingRole(false); setNewRoleName('') }
                      }}
                      className="flex-1 bg-gray-700 text-gray-200 text-xs rounded px-2 py-1 outline-none ring-1 ring-gray-500 focus:ring-blue-400"
                      placeholder="Role name..."
                      autoFocus
                    />
                    <button onClick={handleAddNewRole} className="text-xs text-green-400 hover:text-green-300 px-1">OK</button>
                    <button onClick={() => { setAddingRole(false); setNewRoleName('') }} className="text-xs text-gray-500 hover:text-gray-300 px-1">X</button>
                  </div>
                )}
                <input
                  type="text"
                  value={roleSearch}
                  onChange={e => setRoleSearch(e.target.value)}
                  className="w-full bg-gray-800 text-gray-200 text-xs rounded px-2 py-1 outline-none ring-1 ring-gray-700 focus:ring-gray-500"
                  placeholder="Search roles..."
                />
              </div>

              {/* Role list */}
              <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
                {displayedRoles.length === 0 && (
                  <div className="text-xs text-gray-600 italic py-2 text-center">
                    {roles.length === 0 ? 'No roles defined' : 'No match'}
                  </div>
                )}
                {displayedRoles.map(role => {
                  const isActive = selectedRole === role.id
                  const isRenaming = renamingRole === role.id
                  const voice = role.voice_type ? voiceMap[role.voice_type] : undefined
                  const rtColor = ROLE_TYPE_COLORS[role.role_type] || ROLE_TYPE_COLORS.extra
                  const rtLabel = ROLE_TYPE_LABELS[role.role_type] || ROLE_TYPE_LABELS.extra
                  const isBadgeExpanded = expandingTypeBadge === role.id
                  const isMenuOpen = menuRoleId === role.id

                  return (
                    <div
                      key={role.id}
                      onClick={() => !isRenaming && handleSelectRole(role.id)}
                      className={`group relative flex items-center rounded text-xs cursor-pointer transition-colors ${
                        isActive
                          ? 'bg-blue-600/20 border-l-2 border-l-blue-400'
                          : 'hover:bg-gray-800 border-l-2 border-l-transparent'
                      }`}
                    >
                      {/* Main content */}
                      <div className="flex-1 px-2 py-1.5 min-w-0">
                        {isRenaming ? (
                          <input
                            type="text"
                            value={renameValue}
                            onChange={e => setRenameValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') handleFinishRename()
                              if (e.key === 'Escape') setRenamingRole(null)
                            }}
                            onBlur={handleFinishRename}
                            onClick={e => e.stopPropagation()}
                            className="w-full bg-gray-700 text-gray-200 text-xs rounded px-2 py-1 outline-none ring-1 ring-blue-400"
                            autoFocus
                          />
                        ) : (
                          <>
                            <div className="flex items-center gap-1.5">
                              <span className="font-medium text-gray-200 truncate">{role.name}</span>
                              {/* Type badge: click to expand inline picker */}
                              {isBadgeExpanded ? (
                                <div ref={badgeContainerRef} className="flex items-center gap-0.5 shrink-0">
                                  {ROLE_TYPES.map(rt => {
                                    const active = (role.role_type || 'extra') === rt
                                    return (
                                      <button
                                        key={rt}
                                        onClick={e => { e.stopPropagation(); handleChangeRoleType(role.id, rt) }}
                                        className={`px-1 py-0 rounded text-[10px] transition-colors ${
                                          active ? ROLE_TYPE_COLORS[rt] + ' ring-1 ring-white/30' : 'text-gray-500 hover:text-gray-200 hover:bg-gray-700'
                                        }`}
                                      >{ROLE_TYPE_LABELS[rt]}</button>
                                    )
                                  })}
                                </div>
                              ) : (
                                <span
                                  onClick={e => { e.stopPropagation(); setExpandingTypeBadge(role.id) }}
                                  className={`px-1 py-0 rounded text-[10px] cursor-pointer hover:brightness-125 shrink-0 ${rtColor}`}
                                >{rtLabel}</span>
                              )}
                            </div>
                            {voice && (
                              <div className="text-[10px] text-gray-500 mt-0.5 truncate">{voice.name}</div>
                            )}
                          </>
                        )}
                      </div>

                      {/* Three-dot menu trigger */}
                      {!isRenaming && (
                        <div ref={isMenuOpen ? menuContainerRef : undefined} className="relative shrink-0">
                          <button
                            onClick={e => {
                              e.stopPropagation()
                              setMenuRoleId(isMenuOpen ? null : role.id)
                              setConfirmDelete(null)
                            }}
                            className={`px-1.5 py-1 text-gray-500 hover:text-gray-300 text-sm leading-none ${
                              isMenuOpen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                            }`}
                          >&#x22EF;</button>
                          {/* Dropdown menu */}
                          {isMenuOpen && (
                            <div className="absolute right-0 top-full z-50 bg-gray-800 border border-gray-600 rounded shadow-lg py-1 min-w-[120px]">
                              {role.voice_type && (
                                <button
                                  onClick={e => { e.stopPropagation(); handleUnassign(role.id) }}
                                  className="block w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700"
                                >Clear Voice</button>
                              )}
                              <button
                                onClick={e => { e.stopPropagation(); handleStartRename(role) }}
                                className="block w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-700"
                              >Rename</button>
                              <button
                                onClick={e => { e.stopPropagation(); handleDeleteRole(role.id) }}
                                className={`block w-full text-left px-3 py-1.5 text-xs ${
                                  confirmDelete === role.id ? 'text-red-400 font-medium' : 'text-gray-300 hover:bg-gray-700'
                                }`}
                              >{confirmDelete === role.id ? 'Confirm?' : 'Delete'}</button>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>

        {/* ── Right: Voice catalogue ─────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-y-auto">
          <section className="px-4 py-3">
            <h2 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Voice Catalogue</h2>

            {/* Recently used voices */}
            {recentVoices.length > 0 && (
              <div className="mb-3">
                <div className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Recently Used</div>
                <div className="flex gap-1.5 flex-wrap">
                  {recentVoices.map(v => {
                    const isAssigned = hasSelection && assignedVoiceId === v.voice_id
                    return (
                      <div
                        key={v.voice_id}
                        className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs ${
                          isAssigned ? 'bg-blue-600/20 ring-1 ring-blue-500/40' : 'bg-gray-800'
                        }`}
                      >
                        <button
                          onClick={() => handleTrial(v)}
                          disabled={!v.trial_url}
                          className="text-blue-400 hover:text-blue-300 disabled:text-gray-600 text-sm"
                          title="Play trial"
                        >{'\u25B6'}</button>
                        <span className="text-gray-300">{v.name}</span>
                        <span className="text-[10px] text-gray-500">{v.gender}</span>
                        <button
                          onClick={() => handleAssign(v.voice_id)}
                          disabled={!hasSelection}
                          className="px-1.5 py-0.5 text-[10px] rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-30 disabled:cursor-not-allowed text-white"
                        >Assign</button>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Filters */}
            <div className="flex gap-4 mb-3 flex-wrap">
              <div className="flex gap-1 items-center flex-wrap">
                <span className="text-[10px] text-gray-500 mr-1">Category:</span>
                <button
                  onClick={() => setCatFilter('all')}
                  className={`px-2 py-0.5 text-[11px] rounded ${catFilter === 'all' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                >All</button>
                {allCategories.map(cat => (
                  <button
                    key={cat}
                    onClick={() => setCatFilter(cat)}
                    className={`px-2 py-0.5 text-[11px] rounded ${catFilter === cat ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                  >{cat}</button>
                ))}
              </div>
              <div className="flex gap-1 items-center">
                <span className="text-[10px] text-gray-500 mr-1">Gender:</span>
                {['all', '男', '女'].map(g => (
                  <button
                    key={g}
                    onClick={() => setGenderFilter(g)}
                    className={`px-2 py-0.5 text-[11px] rounded ${genderFilter === g ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                  >{g === 'all' ? 'All' : g}</button>
                ))}
              </div>
            </div>

            {/* Voice cards */}
            <div className="space-y-1">
              {filteredVoices.map(v => {
                const isAssigned = hasSelection && assignedVoiceId === v.voice_id
                const isExpanded = expandedVoice === v.voice_id
                const voiceHistory = history.filter(h => h.voice_id === v.voice_id)
                return (
                  <div key={v.voice_id} id={`voice-${v.voice_id}`}>
                    <div
                      className={`flex items-center gap-3 px-3 py-2 rounded-t transition-colors ${
                        isAssigned
                          ? 'bg-blue-600/20 ring-1 ring-blue-500/40'
                          : 'bg-gray-800 hover:bg-gray-800/80'
                      } ${!isExpanded ? 'rounded-b' : ''}`}
                    >
                      {/* Radio indicator */}
                      <span className={`w-3.5 h-3.5 rounded-full border-2 shrink-0 flex items-center justify-center ${
                        isAssigned ? 'border-blue-500' : 'border-gray-600'
                      }`}>
                        {isAssigned && <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />}
                      </span>

                      {/* Trial play/pause */}
                      {(() => {
                        const trialKey = `trial:${v.voice_id}`
                        const isCurrent = playingKey === trialKey
                        const showPause = isCurrent && isPlaying
                        return (
                          <div className="relative shrink-0 flex items-center">
                            <button
                              onClick={e => { e.stopPropagation(); handleTrial(v) }}
                              disabled={!v.trial_url}
                              className={`w-7 h-7 rounded-full flex items-center justify-center text-sm transition-colors ${
                                showPause
                                  ? 'bg-blue-600 text-white'
                                  : 'text-blue-400 hover:text-blue-300 disabled:text-gray-600 hover:bg-gray-700'
                              }`}
                              title={v.trial_url ? (showPause ? 'Pause' : 'Play trial') : 'No trial available'}
                            >{showPause ? '\u23F8' : '\u25B6'}</button>
                            {isCurrent && playProgress > 0 && (
                              <div className="absolute -bottom-1 left-0 w-7 h-0.5 bg-gray-600 rounded overflow-hidden">
                                <div className="h-full bg-blue-400 transition-all" style={{ width: `${playProgress * 100}%` }} />
                              </div>
                            )}
                          </div>
                        )
                      })()}

                      {/* Avatar */}
                      {v.avatar && (
                        <img src={v.avatar} alt="" className="w-8 h-8 rounded-full shrink-0 bg-gray-700" />
                      )}

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-gray-200">{v.name}</span>
                          <span className="text-[10px] text-gray-500">{v.gender} / {v.age}</span>
                          <span className="text-[10px] text-gray-600">{v.voice_id}</span>
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                          {v.languages.map(l => (
                            <span key={l.lang} className="text-[10px] px-1 py-0.5 rounded bg-gray-700/50 text-gray-400">
                              {l.flag} {l.lang}
                            </span>
                          ))}
                          {v.emotions.map(em => (
                            <span key={em.value} className="text-[10px] px-1 py-0.5 rounded bg-gray-700/50 text-gray-400">
                              {em.icon} {em.value}
                            </span>
                          ))}
                          {v.categories.map(c => (
                            <span key={c} className="text-[10px] px-1 py-0.5 rounded bg-gray-700/30 text-gray-500">{c}</span>
                          ))}
                        </div>
                      </div>

                      {/* Try button */}
                      <button
                        onClick={e => {
                          e.stopPropagation()
                          setExpandedVoice(prev => prev === v.voice_id ? null : v.voice_id)
                        }}
                        className={`px-2 py-1 text-[11px] rounded shrink-0 transition-colors ${
                          isExpanded
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200'
                        }`}
                      >Try</button>

                      {/* Assign button */}
                      <button
                        onClick={e => { e.stopPropagation(); handleAssign(v.voice_id) }}
                        disabled={!hasSelection}
                        className="px-2 py-1 text-[11px] rounded shrink-0 transition-colors bg-blue-600 hover:bg-blue-500 disabled:opacity-30 disabled:cursor-not-allowed text-white"
                      >Assign</button>
                    </div>

                    {/* Inline synthesis panel */}
                    {isExpanded && (
                      <div className="bg-gray-800/60 border border-gray-700 border-t-0 rounded-b px-4 py-3 space-y-3">
                        <div className="flex items-center gap-3 flex-wrap">
                          <div className="flex items-center gap-2">
                            <label className="text-[11px] text-gray-400">Emotion</label>
                            <select
                              value={selectedEmotion}
                              onChange={e => setSelectedEmotion(e.target.value)}
                              disabled={v.emotions.length === 0}
                              className="bg-gray-700 text-gray-200 text-xs rounded px-2 py-1 outline-none disabled:opacity-40"
                            >
                              {v.emotions.length === 0 && <option value="">(none)</option>}
                              {v.emotions.map(em => (
                                <option key={em.value} value={em.value}>{em.icon} {em.label}</option>
                              ))}
                            </select>
                          </div>
                          <input
                            type="text"
                            value={text}
                            onChange={e => setText(e.target.value)}
                            placeholder="Enter text to synthesize..."
                            className="flex-1 min-w-[200px] bg-gray-700 text-gray-200 text-xs rounded px-2 py-1 outline-none"
                          />
                          <button
                            onClick={handleSynthesize}
                            disabled={synthesizing || !text.trim()}
                            className="px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                          >
                            {synthesizing ? 'Synthesizing...' : 'Synthesize'}
                          </button>
                          {playingUrl && playingKey?.startsWith(`trial:${v.voice_id}`) && (
                            <button
                              onClick={handleDownload}
                              className="px-2 py-1 text-[11px] rounded bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200 shrink-0"
                              title="Download"
                            >Download</button>
                          )}
                        </div>

                        {/* History for this voice */}
                        {voiceHistory.length > 0 && (
                          <div>
                            <div className="text-[10px] text-gray-500 mb-1">History</div>
                            <div className="space-y-1 max-h-32 overflow-y-auto">
                              {voiceHistory.map(h => {
                                const histKey = `hist:${h.key}`
                                const isCurrent = playingKey === histKey
                                const showPause = isCurrent && isPlaying
                                return (
                                  <div
                                    key={h.key}
                                    className={`flex items-center gap-2 text-xs cursor-pointer group ${
                                      isCurrent ? 'text-blue-300' : 'text-gray-400 hover:text-gray-200'
                                    }`}
                                    onClick={() => togglePlay(histKey, `/api/voices/audio/${h.key}`)}
                                  >
                                    <span className={`shrink-0 ${showPause ? 'text-blue-300' : 'text-blue-400 group-hover:text-blue-300'}`}>
                                      {showPause ? '\u23F8' : '\u25B6'}
                                    </span>
                                    {h.emotion && (
                                      <span className="px-1 py-0.5 rounded bg-gray-700/50 text-[10px] shrink-0">{h.emotion}</span>
                                    )}
                                    <span className="truncate flex-1">{h.text}</span>
                                    {isCurrent && playProgress > 0 && (
                                      <div className="w-12 h-1 bg-gray-600 rounded overflow-hidden shrink-0">
                                        <div className="h-full bg-blue-400 transition-all" style={{ width: `${playProgress * 100}%` }} />
                                      </div>
                                    )}
                                    {isCurrent && (
                                      <button
                                        onClick={e => { e.stopPropagation(); handleDownload() }}
                                        className="opacity-0 group-hover:opacity-100 text-[10px] text-gray-500 hover:text-gray-300 shrink-0"
                                      >DL</button>
                                    )}
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
