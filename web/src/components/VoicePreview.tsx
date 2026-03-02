/** VoiceCasting: assign voices to roles with trial previews + inline synthesis */
import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import type { Roles } from '../types/asr-model'

// ── constants ───────────────────────────────────────────────────────────────

const DEFAULT_TEXT =
  'I never thought this day would come, but here we are. ' +
  'After everything we have been through, all the promises, all the lies, ' +
  'you still have the nerve to stand in front of me and act like nothing happened.'

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
  onBack: () => void
  dramas: string[]
  initialDrama: string
}

// ── component ───────────────────────────────────────────────────────────────

export function VoicePreview({ onBack, dramas, initialDrama }: Props) {
  const [drama, setDrama] = useState(initialDrama)
  // voices
  const [voices, setVoices] = useState<Voice[]>([])
  const [loadError, setLoadError] = useState('')
  const [catFilter, setCatFilter] = useState('all')
  const [genderFilter, setGenderFilter] = useState('all')

  // roles
  const [roles, setRoles] = useState<Roles | null>(null)
  const [selectedRole, setSelectedRole] = useState<string | null>(null)
  const [editingDefault, setEditingDefault] = useState<'male' | 'female' | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [addingRole, setAddingRole] = useState(false)
  const [newRoleName, setNewRoleName] = useState('')

  // inline player: track which item is playing
  const [playingKey, setPlayingKey] = useState<string | null>(null) // "trial:{voice_id}" or "hist:{key}"
  const [playingUrl, setPlayingUrl] = useState<string | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [playProgress, setPlayProgress] = useState(0) // 0-1
  const audioRef = useRef<HTMLAudioElement>(null)

  // inline synthesis (per-voice)
  const [expandedVoice, setExpandedVoice] = useState<string | null>(null)
  const [selectedEmotion, setSelectedEmotion] = useState('')
  const [text, setText] = useState(DEFAULT_TEXT)
  const [synthesizing, setSynthesizing] = useState(false)
  const [synthError, setSynthError] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([])

  // scroll-to-voice
  const scrollToVoiceRef = useRef<string | null>(null)

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
    if (!drama) { setRoles(null); return }
    fetch(`/api/episodes/${encodeURIComponent(drama)}/roles`)
      .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json() })
      .then((data: Roles) => {
        setRoles(data)
        setDirty(false)
        setSelectedRole(null)
        setEditingDefault(null)
      })
      .catch(() => setRoles({ roles: {}, default_roles: {} }))
  }, [drama])

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

  // The voice_id currently assigned to the active selection (role or default)
  const assignedVoiceId = useMemo(() => {
    if (!roles) return null
    if (editingDefault) return roles.default_roles[editingDefault] ?? null
    if (selectedRole) return roles.roles[selectedRole] ?? null
    return null
  }, [roles, selectedRole, editingDefault])

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
    // small delay to let React render
    setTimeout(() => {
      document.getElementById(`voice-${targetId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 50)
  }, [selectedRole, editingDefault])

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

  // ── play helper ───────────────────────────────────────────────────────

  const togglePlay = useCallback((key: string, url: string) => {
    const el = audioRef.current
    if (!el) return
    if (playingKey === key) {
      // same item: toggle pause/resume
      if (el.paused) el.play()
      else el.pause()
    } else {
      // new item: switch and play
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

  // ── assign voice ──────────────────────────────────────────────────────

  const handleAssign = useCallback((voiceId: string) => {
    if (!roles) return
    if (editingDefault) {
      if (roles.default_roles[editingDefault] === voiceId) return
      setRoles({ ...roles, default_roles: { ...roles.default_roles, [editingDefault]: voiceId } })
      setDirty(true)
    } else if (selectedRole) {
      if (roles.roles[selectedRole] === voiceId) return
      setRoles({ ...roles, roles: { ...roles.roles, [selectedRole]: voiceId } })
      setDirty(true)
    }
  }, [roles, selectedRole, editingDefault])

  const handleUnassign = useCallback((roleId: string) => {
    if (!roles) return
    setRoles({ ...roles, roles: { ...roles.roles, [roleId]: '' } })
    setDirty(true)
  }, [roles])

  // ── select role ───────────────────────────────────────────────────────

  const handleSelectRole = useCallback((roleId: string) => {
    setSelectedRole(roleId)
    setEditingDefault(null)
    // schedule scroll to assigned voice
    if (roles) {
      const voiceId = roles.roles[roleId]
      if (voiceId) scrollToVoiceRef.current = voiceId
    }
  }, [roles])

  const handleSelectDefault = useCallback((gender: 'male' | 'female') => {
    setEditingDefault(gender)
    setSelectedRole(null)
    // schedule scroll to assigned voice
    if (roles) {
      const voiceId = roles.default_roles[gender]
      if (voiceId) scrollToVoiceRef.current = voiceId
    }
  }, [roles])

  // ── save ──────────────────────────────────────────────────────────────

  const handleSave = useCallback(async () => {
    if (!roles || !drama) return
    setSaving(true)
    try {
      const res = await fetch(`/api/episodes/${encodeURIComponent(drama)}/roles`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(roles),
      })
      if (res.ok) {
        setRoles(await res.json())
        setDirty(false)
      }
    } finally {
      setSaving(false)
    }
  }, [roles, drama])

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

      // append to local history
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

  // ── render ────────────────────────────────────────────────────────────

  if (loadError) {
    return (
      <div className="h-full flex items-center justify-center text-red-400">
        Failed to load voices: {loadError}
      </div>
    )
  }

  // pinyin-sorted role entries, split by assignment status
  const { unassignedRoles, assignedRoles } = useMemo(() => {
    if (!roles) return { unassignedRoles: [], assignedRoles: [] }
    const entries = Object.entries(roles.roles).sort(([a], [b]) => a.localeCompare(b, 'zh-Hans-CN'))
    return {
      unassignedRoles: entries.filter(([_, v]) => !v),
      assignedRoles: entries.filter(([_, v]) => !!v),
    }
  }, [roles])

  const handleAddNewRole = useCallback(() => {
    const name = newRoleName.trim()
    if (!name || !roles) return
    if (name in roles.roles) return
    setRoles({ ...roles, roles: { ...roles.roles, [name]: '' } })
    setDirty(true)
    setNewRoleName('')
    setAddingRole(false)
    setSelectedRole(name)
    setEditingDefault(null)
  }, [newRoleName, roles])

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const handleDeleteRole = useCallback((roleId: string) => {
    if (confirmDelete !== roleId) {
      setConfirmDelete(roleId)
      return
    }
    if (!roles) return
    const { [roleId]: _, ...rest } = roles.roles
    setRoles({ ...roles, roles: rest })
    setDirty(true)
    setConfirmDelete(null)
    if (selectedRole === roleId) {
      setSelectedRole(null)
    }
  }, [roles, selectedRole, confirmDelete])

  // 点击其他地方取消确认
  useEffect(() => {
    if (!confirmDelete) return
    const handler = () => setConfirmDelete(null)
    window.addEventListener('click', handler)
    return () => window.removeEventListener('click', handler)
  }, [confirmDelete])

  const hasSelection = selectedRole !== null || editingDefault !== null

  return (
    <div className="h-full flex flex-col bg-gray-900 text-gray-100">
      {/* Header */}
      <header className="flex items-center gap-3 px-4 py-2 bg-gray-800 border-b border-gray-700 shrink-0">
        <button onClick={onBack} className="text-sm text-gray-400 hover:text-gray-200">&larr; Back</button>
        <h1 className="text-sm font-bold text-gray-300">Voice Casting</h1>
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
        <div className="flex-1" />
        {synthError && <span className="text-xs text-red-400 mr-2">{synthError}</span>}
        {dirty && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        )}
      </header>

      {/* Hidden audio element */}
      <audio ref={audioRef} className="hidden" />

      {/* Main split layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* ── Left: Roles panel ──────────────────────────────────────── */}
        <div className="w-64 shrink-0 border-r border-gray-700 flex flex-col overflow-y-auto">
          {!drama ? (
            <div className="flex-1 flex items-center justify-center text-xs text-gray-500 px-4 text-center">
              Select a drama to start voice casting
            </div>
          ) : !roles ? (
            <div className="flex-1 flex items-center justify-center text-xs text-gray-500">
              Loading roles...
            </div>
          ) : (
            <>
              {/* Role list */}
              <div className="px-3 py-2">
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-[10px] text-gray-500 uppercase tracking-wide">Roles</h2>
                  <button
                    onClick={() => setAddingRole(true)}
                    className="text-[10px] text-gray-500 hover:text-gray-300 px-1"
                    title="Add role"
                  >+ Add</button>
                </div>
                {addingRole && (
                  <div className="flex gap-1 mb-1">
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
                <div className="space-y-2">
                  {unassignedRoles.length === 0 && assignedRoles.length === 0 && (
                    <div className="text-xs text-gray-600 italic py-1">No roles defined</div>
                  )}

                  {/* Unassigned group */}
                  <div>
                    <div className="text-[10px] text-yellow-500/70 mb-0.5 px-1">Unassigned ({unassignedRoles.length})</div>
                    <div className="space-y-0.5">
                      {unassignedRoles.length === 0 && (
                        <div className="text-[10px] text-gray-600 italic px-2 py-1">--</div>
                      )}
                      {unassignedRoles.map(([roleId]) => {
                        const isActive = selectedRole === roleId && editingDefault === null
                        const isConfirming = confirmDelete === roleId
                        return (
                          <div
                            key={roleId}
                            className={`group flex items-center rounded text-xs transition-colors ${
                              isActive
                                ? 'bg-blue-600/30 border border-blue-500/50'
                                : 'hover:bg-gray-800 border border-transparent'
                            }`}
                          >
                            <button
                              onClick={() => handleSelectRole(roleId)}
                              className="flex-1 text-left px-2 py-1.5 min-w-0"
                            >
                              <div className="font-medium text-gray-200">{roleId}</div>
                              <div className="text-[10px] text-yellow-500/50 mt-0.5">(none)</div>
                            </button>
                            <button
                              onClick={e => { e.stopPropagation(); handleDeleteRole(roleId) }}
                              className={`${isConfirming ? 'opacity-100 text-red-400' : 'opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400'} px-1.5 py-1 text-[10px] shrink-0`}
                              title={isConfirming ? 'Click again to confirm' : 'Delete role'}
                            >{isConfirming ? 'Confirm?' : 'Delete'}</button>
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* Assigned group */}
                  <div>
                    <div className="text-[10px] text-green-500/70 mb-0.5 px-1">Assigned ({assignedRoles.length})</div>
                    <div className="space-y-0.5">
                      {assignedRoles.length === 0 && (
                        <div className="text-[10px] text-gray-600 italic px-2 py-1">--</div>
                      )}
                      {assignedRoles.map(([roleId, voiceId]) => {
                        const isActive = selectedRole === roleId && editingDefault === null
                        const voice = voiceMap[voiceId]
                        const isConfirming = confirmDelete === roleId
                        return (
                          <div
                            key={roleId}
                            className={`group flex items-center rounded text-xs transition-colors ${
                              isActive
                                ? 'bg-blue-600/30 border border-blue-500/50'
                                : 'hover:bg-gray-800 border border-transparent'
                            }`}
                          >
                            <button
                              onClick={() => handleSelectRole(roleId)}
                              className="flex-1 text-left px-2 py-1.5 min-w-0"
                            >
                              <div className="font-medium text-gray-200">{roleId}</div>
                              <div className="text-[10px] text-gray-500 mt-0.5">
                                {voice ? voice.name : voiceId}
                              </div>
                            </button>
                            <button
                              onClick={e => { e.stopPropagation(); handleUnassign(roleId) }}
                              className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-yellow-400 px-1.5 py-1 text-[10px] shrink-0"
                              title="Clear voice assignment"
                            >Clear</button>
                            <button
                              onClick={e => { e.stopPropagation(); handleDeleteRole(roleId) }}
                              className={`${isConfirming ? 'opacity-100 text-red-400' : 'opacity-0 group-hover:opacity-100 text-gray-600 hover:text-red-400'} px-1.5 py-1 text-[10px] shrink-0`}
                              title={isConfirming ? 'Click again to confirm' : 'Delete role'}
                            >{isConfirming ? 'Confirm?' : 'Delete'}</button>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </div>
              </div>

              {/* Default roles */}
              <div className="px-3 py-2 border-t border-gray-700/50 mt-auto">
                <h2 className="text-[10px] text-gray-500 uppercase tracking-wide mb-2">Default Roles</h2>
                <div className="space-y-0.5">
                  {(['male', 'female'] as const).map(gender => {
                    const isActive = editingDefault === gender
                    const voiceId = roles.default_roles[gender] ?? ''
                    const voice = voiceId ? voiceMap[voiceId] : null
                    return (
                      <button
                        key={gender}
                        onClick={() => handleSelectDefault(gender)}
                        className={`w-full text-left px-2 py-1.5 rounded text-xs transition-colors ${
                          isActive
                            ? 'bg-blue-600/30 border border-blue-500/50'
                            : 'hover:bg-gray-800 border border-transparent'
                        }`}
                      >
                        <div className="font-medium text-gray-200">{gender}</div>
                        <div className="text-[10px] text-gray-500 mt-0.5">
                          {voice ? voice.name : voiceId ? voiceId : '(none)'}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            </>
          )}
        </div>

        {/* ── Right: Voice catalogue ─────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-y-auto">
          <section className="px-4 py-3">
            <h2 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Voice Catalogue</h2>

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
                      onClick={() => hasSelection && handleAssign(v.voice_id)}
                      className={`flex items-center gap-3 px-3 py-2 rounded-t transition-colors ${
                        isAssigned
                          ? 'bg-blue-600/20 ring-1 ring-blue-500/40'
                          : 'bg-gray-800 hover:bg-gray-800/80'
                      } ${hasSelection ? 'cursor-pointer' : ''} ${!isExpanded ? 'rounded-b' : ''}`}
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
