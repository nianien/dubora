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

  // global player
  const [currentAudioUrl, setCurrentAudioUrl] = useState<string | null>(null)
  const [playingLabel, setPlayingLabel] = useState('')
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

  // ── play helper ───────────────────────────────────────────────────────

  const playAudio = useCallback((url: string, label: string) => {
    setCurrentAudioUrl(url)
    setPlayingLabel(label)
    setTimeout(() => {
      const el = audioRef.current
      if (el) { el.load(); el.play() }
    }, 0)
  }, [])

  const handleTrial = useCallback((voice: Voice) => {
    if (voice.trial_url) playAudio(voice.trial_url, `${voice.name} (trial)`)
  }, [playAudio])

  // ── assign voice ──────────────────────────────────────────────────────

  const handleAssign = useCallback((voiceId: string) => {
    if (!roles) return
    if (editingDefault) {
      setRoles({ ...roles, default_roles: { ...roles.default_roles, [editingDefault]: voiceId } })
      setDirty(true)
    } else if (selectedRole) {
      setRoles({ ...roles, roles: { ...roles.roles, [selectedRole]: voiceId } })
      setDirty(true)
    }
  }, [roles, selectedRole, editingDefault])

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
      const emotionLabel = voice?.emotions.find(e => e.value === selectedEmotion)
      playAudio(data.audio_url, `${voice?.name ?? expandedVoice} / ${emotionLabel?.label ?? (selectedEmotion || 'neutral')}`)

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
  }, [expandedVoice, selectedEmotion, text, voiceMap, playAudio])

  // ── download ──────────────────────────────────────────────────────────

  const handleDownload = useCallback(() => {
    if (!currentAudioUrl) return
    const a = document.createElement('a')
    a.href = currentAudioUrl
    a.download = `${playingLabel.replace(/[/ ]+/g, '_') || 'voice'}.wav`
    a.click()
  }, [currentAudioUrl, playingLabel])

  // ── render ────────────────────────────────────────────────────────────

  if (loadError) {
    return (
      <div className="h-full flex items-center justify-center text-red-400">
        Failed to load voices: {loadError}
      </div>
    )
  }

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

      {/* Global audio player */}
      {currentAudioUrl && (
        <div className="flex items-center gap-3 px-4 py-2 bg-gray-800/60 border-b border-gray-700 shrink-0">
          <span className="text-xs text-gray-400 shrink-0 w-44 truncate">{playingLabel}</span>
          <audio ref={audioRef} src={currentAudioUrl} controls className="flex-1 h-8" />
          <button onClick={handleDownload} className="px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 shrink-0">
            Download
          </button>
        </div>
      )}

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
                <h2 className="text-[10px] text-gray-500 uppercase tracking-wide mb-2">Roles</h2>
                <div className="space-y-0.5">
                  {Object.keys(roles.roles).length === 0 && (
                    <div className="text-xs text-gray-600 italic py-1">No roles defined</div>
                  )}
                  {Object.entries(roles.roles).map(([roleId, voiceId]) => {
                    const isActive = selectedRole === roleId && editingDefault === null
                    const voice = voiceId ? voiceMap[voiceId] : null
                    return (
                      <button
                        key={roleId}
                        onClick={() => handleSelectRole(roleId)}
                        className={`w-full text-left px-2 py-1.5 rounded text-xs transition-colors ${
                          isActive
                            ? 'bg-blue-600/30 border border-blue-500/50'
                            : 'hover:bg-gray-800 border border-transparent'
                        }`}
                      >
                        <div className="font-medium text-gray-200">{roleId}</div>
                        <div className="text-[10px] text-gray-500 mt-0.5">
                          {voice ? voice.name : voiceId ? voiceId : '(none)'}
                        </div>
                      </button>
                    )
                  })}
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

                      {/* Trial play */}
                      <button
                        onClick={e => { e.stopPropagation(); handleTrial(v) }}
                        disabled={!v.trial_url}
                        className="text-blue-400 hover:text-blue-300 disabled:text-gray-600 shrink-0 text-base"
                        title={v.trial_url ? 'Play trial audio' : 'No trial available'}
                      >&#9654;</button>

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
                        </div>

                        {/* History for this voice */}
                        {voiceHistory.length > 0 && (
                          <div>
                            <div className="text-[10px] text-gray-500 mb-1">History</div>
                            <div className="space-y-1 max-h-32 overflow-y-auto">
                              {voiceHistory.map(h => (
                                <div
                                  key={h.key}
                                  className="flex items-center gap-2 text-xs text-gray-400 hover:text-gray-200 cursor-pointer group"
                                  onClick={() => playAudio(`/api/voices/audio/${h.key}`, `${h.voice_name} / ${h.emotion || 'neutral'}`)}
                                >
                                  <span className="text-blue-400 group-hover:text-blue-300 shrink-0">&#9654;</span>
                                  {h.emotion && (
                                    <span className="px-1 py-0.5 rounded bg-gray-700/50 text-[10px] shrink-0">{h.emotion}</span>
                                  )}
                                  <span className="truncate">{h.text}</span>
                                </div>
                              ))}
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
