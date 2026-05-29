import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Drama, Episode, PaginatedResponse } from '../types/asr-model'
import { fetchJson, postJson, uploadFile } from '../utils/api'
import { timeAgo } from '../utils/time-ago'

const STATUS_PRIORITY: Record<string, number> = {
  running: 0,
  review: 1,
  failed: 2,
  succeeded: 3,
  ready: 4,
}

const STATUS_LABEL: Record<string, { label: string; color: string; dot: string }> = {
  running:   { label: '进行中', color: 'text-blue-400',   dot: 'bg-blue-500 animate-pulse' },
  review:    { label: '待审核', color: 'text-amber-400',  dot: 'bg-amber-500' },
  failed:    { label: '失败',   color: 'text-red-400',    dot: 'bg-red-500' },
  succeeded: { label: '完成',   color: 'text-green-400',  dot: 'bg-green-500' },
  ready:     { label: '就绪',   color: 'text-gray-400',   dot: 'bg-gray-500' },
}

type ViewMode = 'list' | 'grid'
type StatusFilter = 'all' | 'running' | 'completed' | 'not_started'
type SortField = 'updated_at' | 'created_at' | 'name'

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'running', label: '进行中' },
  { value: 'completed', label: '已完成' },
  { value: 'not_started', label: '未开始' },
]

const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: 'updated_at', label: '最近更新' },
  { value: 'created_at', label: '创建时间' },
  { value: 'name', label: '名称' },
]

const PAGE_SIZE = 10

export function DramaListPage() {
  const navigate = useNavigate()

  // View & filter state
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [sort, setSort] = useState<SortField>('updated_at')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)

  // Data
  const [dramas, setDramas] = useState<Drama[]>([])
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newEpisodes, setNewEpisodes] = useState('')
  const [newSynopsis, setNewSynopsis] = useState('')
  const [newCover, setNewCover] = useState<File | null>(null)
  const [newCoverPreview, setNewCoverPreview] = useState('')
  const [creating, setCreating] = useState(false)

  // Debounce ref for search
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  const loadDramas = useCallback(async (p: number, s: string, sf: StatusFilter, so: SortField) => {
    setLoading(true)
    try {
      const params = new URLSearchParams({
        page: String(p),
        page_size: String(PAGE_SIZE),
        sort: so,
      })
      if (s) params.set('search', s)
      if (sf !== 'all') params.set('status', sf)

      const res = await fetchJson<PaginatedResponse<Drama>>(`/dramas?${params}`)
      setDramas(res.items)
      setTotal(res.total)
      setPage(res.page)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadEpisodes = useCallback(async () => {
    try {
      const e = await fetchJson<Episode[]>('/episodes')
      setEpisodes(e)
    } catch { /* episodes are optional for stats */ }
  }, [])

  // Initial load
  useEffect(() => {
    loadDramas(1, '', 'all', 'updated_at')
    loadEpisodes()
  }, [loadDramas, loadEpisodes])

  // Reload dramas when filter/sort/page change (not search — that's debounced)
  const reload = useCallback((p?: number) => {
    loadDramas(p ?? page, search, statusFilter, sort)
  }, [loadDramas, page, search, statusFilter, sort])

  const handleSearchChange = (value: string) => {
    setSearch(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setPage(1)
      loadDramas(1, value, statusFilter, sort)
    }, 300)
  }

  const handleStatusChange = (sf: StatusFilter) => {
    setStatusFilter(sf)
    setPage(1)
    loadDramas(1, search, sf, sort)
  }

  const handleSortChange = (so: SortField) => {
    setSort(so)
    setPage(1)
    loadDramas(1, search, statusFilter, so)
  }

  const handlePageChange = (p: number) => {
    setPage(p)
    loadDramas(p, search, statusFilter, sort)
  }

  const resetNewForm = () => {
    setNewName('')
    setNewEpisodes('')
    setNewSynopsis('')
    setNewCover(null)
    setNewCoverPreview('')
    setShowNew(false)
  }

  const handleCoverSelect = (file: File | null) => {
    setNewCover(file)
    if (newCoverPreview) URL.revokeObjectURL(newCoverPreview)
    setNewCoverPreview(file ? URL.createObjectURL(file) : '')
  }

  const canCreate = newName.trim() && Number(newEpisodes) >= 1

  const handleCreate = async () => {
    if (!canCreate) return
    setCreating(true)
    try {
      const res = await postJson<{ id: number }>('/dramas', {
        name: newName.trim(),
        total_episodes: Number(newEpisodes),
        synopsis: newSynopsis.trim(),
      })
      if (newCover) {
        try { await uploadFile(`/dramas/${res.id}/cover`, newCover) } catch {}
      }
      resetNewForm()
      reload(1)
      loadEpisodes()
      navigate(`/drama/${res.id}`)
    } finally {
      setCreating(false)
    }
  }

  // Recent episodes: sort by status priority then updated_at, take top 3
  const recentEpisodes = useMemo(() => {
    return [...episodes]
      .sort((a, b) => {
        const pa = STATUS_PRIORITY[a.status] ?? 4
        const pb = STATUS_PRIORITY[b.status] ?? 4
        if (pa !== pb) return pa - pb
        const ta = a.updated_at ? new Date(a.updated_at).getTime() : 0
        const tb = b.updated_at ? new Date(b.updated_at).getTime() : 0
        return tb - ta
      })
      .slice(0, 3)
  }, [episodes])

  // Global stats from episodes
  const stats = useMemo(() => {
    const totalEps = episodes.length
    const done = episodes.filter(e => e.status === 'succeeded' || e.dubbed_video).length
    const inProgress = episodes.filter(e => e.status !== 'ready' && e.status !== 'succeeded').length
    return { totalEps, done, inProgress }
  }, [episodes])

  // Episode map for drama cards
  const episodesByDrama = useMemo(() => {
    const map: Record<number, Episode[]> = {}
    for (const e of episodes) {
      ;(map[e.drama_id] ??= []).push(e)
    }
    return map
  }, [episodes])

  // Pagination
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const paginationRange = useMemo(() => {
    const pages: (number | '...')[] = []
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i)
    } else {
      pages.push(1)
      if (page > 3) pages.push('...')
      for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) {
        pages.push(i)
      }
      if (page < totalPages - 2) pages.push('...')
      pages.push(totalPages)
    }
    return pages
  }, [page, totalPages])

  return (
    <div className="min-h-full flex flex-col">
      <div className="flex-1 px-8 py-6">

        {/* Title bar */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-baseline gap-3">
            <h2 className="text-lg font-semibold tracking-tight">我的项目</h2>
            {!loading && (
              <span className="text-xs text-gray-600">共 {total} 个</span>
            )}
            {!loading && stats.totalEps > 0 && (
              <div className="flex items-center gap-3 text-xs text-gray-500 ml-2">
                <span>{stats.totalEps} 集</span>
                {stats.done > 0 && <span className="text-green-400">{stats.done} 已完成</span>}
                {stats.inProgress > 0 && <span className="text-blue-400">{stats.inProgress} 进行中</span>}
              </div>
            )}
          </div>
          <button
            onClick={() => setShowNew(true)}
            className="h-8 px-4 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 transition-colors flex items-center gap-1.5"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
            新建项目
          </button>
        </div>

        {/* Create Modal */}
        {showNew && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            {/* Backdrop */}
            <div className="absolute inset-0 bg-black/60" onClick={resetNewForm} />
            {/* Modal */}
            <div className="relative w-full max-w-md mx-4 rounded-2xl bg-[#14161e] border border-white/[0.08] shadow-2xl">
              <div className="px-6 pt-5 pb-6">
                <h3 className="text-base font-semibold mb-5">新建项目</h3>

                {/* 剧名 */}
                <label className="block mb-4">
                  <span className="text-xs text-gray-400 mb-1.5 block">剧名 <span className="text-red-400">*</span></span>
                  <input
                    autoFocus
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    placeholder="输入剧名..."
                    className="w-full h-9 px-3 text-sm rounded-lg bg-[#0a0b10] border border-white/10 outline-none focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 transition-colors placeholder:text-gray-600"
                  />
                </label>

                {/* 集数 */}
                <label className="block mb-4">
                  <span className="text-xs text-gray-400 mb-1.5 block">集数 <span className="text-red-400">*</span></span>
                  <input
                    type="number"
                    min={1}
                    value={newEpisodes}
                    onChange={e => setNewEpisodes(e.target.value)}
                    placeholder="总集数"
                    className="w-full h-9 px-3 text-sm rounded-lg bg-[#0a0b10] border border-white/10 outline-none focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 transition-colors placeholder:text-gray-600"
                  />
                </label>

                {/* 简介 */}
                <label className="block mb-4">
                  <span className="text-xs text-gray-400 mb-1.5 block">简介</span>
                  <textarea
                    value={newSynopsis}
                    onChange={e => setNewSynopsis(e.target.value)}
                    placeholder="项目简介（选填）"
                    rows={3}
                    className="w-full px-3 py-2 text-sm rounded-lg bg-[#0a0b10] border border-white/10 outline-none focus:border-blue-500/60 focus:ring-1 focus:ring-blue-500/20 transition-colors placeholder:text-gray-600 resize-none"
                  />
                </label>

                {/* 封面 */}
                <label className="block mb-1">
                  <span className="text-xs text-gray-400 mb-1.5 block">封面</span>
                  <div className="flex items-center gap-3">
                    {newCoverPreview ? (
                      <div className="relative w-16 h-20 rounded-lg overflow-hidden shrink-0 border border-white/10">
                        <img src={newCoverPreview} alt="封面预览" className="w-full h-full object-cover" />
                        <button
                          type="button"
                          onClick={e => { e.preventDefault(); handleCoverSelect(null) }}
                          className="absolute top-0.5 right-0.5 w-5 h-5 rounded-full bg-black/70 flex items-center justify-center text-gray-300 hover:text-white"
                        >
                          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    ) : (
                      <label className="flex items-center justify-center w-16 h-20 rounded-lg border border-dashed border-white/10 hover:border-white/20 bg-[#0a0b10] cursor-pointer transition-colors">
                        <svg className="w-5 h-5 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                        </svg>
                        <input
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          className="hidden"
                          onChange={e => handleCoverSelect(e.target.files?.[0] ?? null)}
                        />
                      </label>
                    )}
                    <span className="text-xs text-gray-600">支持 JPG / PNG / WebP</span>
                  </div>
                </label>
              </div>

              {/* Footer */}
              <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-white/[0.06]">
                <button
                  onClick={resetNewForm}
                  className="h-9 px-4 text-sm rounded-lg text-gray-400 hover:text-gray-200 hover:bg-white/[0.04] transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating || !canCreate}
                  className="h-9 px-5 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {creating ? '创建中...' : '创建'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Toolbar: search | status filter | sort | view toggle */}
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          {/* Search */}
          <div className="relative flex-1 min-w-[200px] max-w-[320px]">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-600 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
            </svg>
            <input
              value={search}
              onChange={e => handleSearchChange(e.target.value)}
              placeholder="搜索项目..."
              className="w-full h-8 pl-9 pr-3 text-sm rounded-lg bg-[#14161e] border border-white/[0.06] outline-none focus:border-blue-500/40 focus:ring-1 focus:ring-blue-500/20 transition-colors placeholder:text-gray-600"
            />
          </div>

          {/* Status filter buttons */}
          <div className="flex items-center rounded-lg bg-[#14161e] border border-white/[0.06] p-0.5">
            {STATUS_FILTERS.map(f => (
              <button
                key={f.value}
                onClick={() => handleStatusChange(f.value)}
                className={`h-7 px-3 text-xs rounded-md transition-colors ${
                  statusFilter === f.value
                    ? 'bg-white/[0.08] text-white font-medium'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {/* Sort dropdown */}
          <select
            value={sort}
            onChange={e => handleSortChange(e.target.value as SortField)}
            className="h-8 px-3 text-xs rounded-lg bg-[#14161e] border border-white/[0.06] text-gray-400 outline-none focus:border-blue-500/40 cursor-pointer"
          >
            {SORT_OPTIONS.map(s => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>

          {/* View toggle */}
          <div className="flex items-center rounded-lg bg-[#14161e] border border-white/[0.06] p-0.5 ml-auto">
            <button
              onClick={() => setViewMode('list')}
              className={`h-7 w-8 flex items-center justify-center rounded-md transition-colors ${
                viewMode === 'list' ? 'bg-white/[0.08] text-white' : 'text-gray-600 hover:text-gray-400'
              }`}
              title="列表视图"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
              </svg>
            </button>
            <button
              onClick={() => setViewMode('grid')}
              className={`h-7 w-8 flex items-center justify-center rounded-md transition-colors ${
                viewMode === 'grid' ? 'bg-white/[0.08] text-white' : 'text-gray-600 hover:text-gray-400'
              }`}
              title="网格视图"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z" />
              </svg>
            </button>
          </div>
        </div>

        {/* Content area */}
        {loading ? (
          <div className="space-y-2">
            {[1,2,3,4,5].map(i => (
              <div key={i} className="h-14 rounded-lg bg-[#14161e] animate-pulse" />
            ))}
          </div>
        ) : viewMode === 'list' ? (
          /* ── List view ── */
          <div className="rounded-xl border border-white/[0.06] overflow-hidden">
            {/* Table header */}
            <div className="grid grid-cols-[60px_1fr_160px_100px] gap-3 px-4 py-2.5 text-[11px] font-medium text-gray-600 uppercase tracking-wider bg-[#0e1018] border-b border-white/[0.04]">
              <span>封面</span>
              <span>剧名</span>
              <span>进度</span>
              <span className="text-right">最后更新</span>
            </div>
            {/* Rows */}
            {dramas.length === 0 ? (
              <div className="px-4 py-12 text-center text-sm text-gray-600">
                {search || statusFilter !== 'all' ? '没有找到匹配的项目' : '还没有项目，点击上方"新建项目"开始'}
              </div>
            ) : (
              dramas.map(d => {
                const dramaEps = episodesByDrama[d.id] ?? []
                const doneCount = dramaEps.filter(e => e.status === 'succeeded' || e.dubbed_video).length
                const progress = d.total_episodes > 0 ? (doneCount / d.total_episodes) * 100 : 0

                return (
                  <button
                    key={d.id}
                    onClick={() => navigate(`/drama/${d.id}`)}
                    className="grid grid-cols-[60px_1fr_160px_100px] gap-3 px-4 py-2.5 items-center w-full text-left hover:bg-white/[0.03] border-b border-white/[0.03] last:border-b-0 transition-colors group"
                  >
                    {/* Cover */}
                    <div className="w-10 h-[60px] rounded-md overflow-hidden bg-[#0e1018] flex items-center justify-center shrink-0">
                      {d.cover_image ? (
                        <img src={d.cover_image} alt={d.name} className="w-full h-full object-cover" />
                      ) : (
                        <svg className="w-5 h-5 text-gray-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909M3.75 21h16.5A2.25 2.25 0 0 0 22.5 18.75V5.25A2.25 2.25 0 0 0 20.25 3H3.75A2.25 2.25 0 0 0 1.5 5.25v13.5A2.25 2.25 0 0 0 3.75 21Z" />
                        </svg>
                      )}
                    </div>
                    {/* Name */}
                    <span className="text-sm font-medium truncate group-hover:text-white transition-colors">
                      {d.name}
                    </span>
                    {/* Progress: xx/yy 集 + bar */}
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500 shrink-0 w-16">
                        {doneCount}/{d.total_episodes} 集
                      </span>
                      <div className="flex-1 h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                        {progress > 0 && (
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-blue-400 to-emerald-400 transition-all duration-500"
                            style={{ width: `${progress}%` }}
                          />
                        )}
                      </div>
                    </div>
                    {/* Updated at */}
                    <span className="text-xs text-gray-600 text-right">
                      {d.updated_at ? timeAgo(d.updated_at) : '-'}
                    </span>
                  </button>
                )
              })
            )}
          </div>
        ) : (
          /* ── Grid view ── */
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {dramas.length === 0 ? (
              <div className="col-span-full px-4 py-12 text-center text-sm text-gray-600">
                {search || statusFilter !== 'all' ? '没有找到匹配的项目' : '还没有项目'}
              </div>
            ) : (
              dramas.map(d => {
                const dramaEps = episodesByDrama[d.id] ?? []
                const doneCount = dramaEps.filter(e => e.status === 'succeeded' || e.dubbed_video).length
                const inProgressCount = dramaEps.filter(e => e.status !== 'ready' && e.status !== 'succeeded').length
                const progress = d.total_episodes > 0 ? (doneCount / d.total_episodes) * 100 : 0

                return (
                  <button
                    key={d.id}
                    onClick={() => navigate(`/drama/${d.id}`)}
                    className="group text-left rounded-xl bg-gradient-to-br from-blue-600/[0.12] via-violet-600/[0.08] to-indigo-600/[0.04] border border-white/[0.06] hover:border-white/[0.14] hover:from-blue-600/[0.18] hover:via-violet-600/[0.12] hover:to-indigo-600/[0.06] hover:-translate-y-0.5 hover:shadow-lg hover:shadow-blue-500/[0.06] transition-all duration-200"
                  >
                    <div className="h-1 rounded-t-xl overflow-hidden bg-white/[0.03]">
                      {progress > 0 && (
                        <div
                          className="h-full bg-gradient-to-r from-blue-400 to-emerald-400 transition-all duration-500"
                          style={{ width: `${progress}%` }}
                        />
                      )}
                    </div>
                    <div className="px-5 pt-4 pb-4">
                      <div className="flex items-center gap-2.5 mb-1">
                        {d.cover_image && (
                          <img
                            src={d.cover_image}
                            alt={d.name}
                            className="w-8 h-8 rounded-lg object-cover shrink-0"
                          />
                        )}
                        <h3 className="font-semibold text-[15px] group-hover:text-white transition-colors truncate">
                          {d.name}
                        </h3>
                      </div>
                      <p className="text-xs text-gray-500 mb-3">
                        {doneCount}/{d.total_episodes} 集
                        {d.updated_at ? ` \u00B7 ${timeAgo(d.updated_at)}` : ''}
                      </p>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          {inProgressCount > 0 && (
                            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-400">
                              {inProgressCount} 进行中
                            </span>
                          )}
                        </div>
                        <svg className="w-4 h-4 text-gray-600 group-hover:text-gray-400 group-hover:translate-x-0.5 transition-all" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                        </svg>
                      </div>
                    </div>
                  </button>
                )
              })
            )}
          </div>
        )}

        {/* Pagination */}
        {!loading && totalPages > 1 && (
          <div className="flex items-center justify-center gap-1 mt-5">
            <button
              onClick={() => handlePageChange(page - 1)}
              disabled={page <= 1}
              className="h-8 w-8 flex items-center justify-center rounded-lg text-gray-500 hover:text-white hover:bg-white/[0.06] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
              </svg>
            </button>
            {paginationRange.map((p, i) =>
              p === '...' ? (
                <span key={`dots-${i}`} className="h-8 w-8 flex items-center justify-center text-xs text-gray-600">...</span>
              ) : (
                <button
                  key={p}
                  onClick={() => handlePageChange(p as number)}
                  className={`h-8 w-8 flex items-center justify-center rounded-lg text-xs transition-colors ${
                    page === p
                      ? 'bg-blue-600 text-white font-medium'
                      : 'text-gray-500 hover:text-white hover:bg-white/[0.06]'
                  }`}
                >
                  {p}
                </button>
              )
            )}
            <button
              onClick={() => handlePageChange(page + 1)}
              disabled={page >= totalPages}
              className="h-8 w-8 flex items-center justify-center rounded-lg text-gray-500 hover:text-white hover:bg-white/[0.06] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
              </svg>
            </button>
          </div>
        )}

        {/* Recent episodes */}
        {!loading && recentEpisodes.length > 0 && (
          <div className="mt-8 mb-8">
            <h2 className="text-sm font-medium text-gray-500 mb-3">最近访问</h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {recentEpisodes.map(ep => {
                const meta = STATUS_LABEL[ep.status] ?? STATUS_LABEL.ready
                return (
                  <button
                    key={ep.id}
                    onClick={() => navigate(`/ide/${ep.id}`)}
                    className="group text-left rounded-xl bg-[#14161e] border border-white/[0.04] hover:border-white/[0.1] hover:bg-[#181b25] transition-all p-4"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs text-gray-500 truncate">{ep.drama}</span>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
                        <span className={`text-[10px] font-medium ${meta.color}`}>{meta.label}</span>
                      </div>
                    </div>
                    <h3 className="text-sm font-medium mb-2 group-hover:text-white transition-colors">
                      {ep.name || `第 ${ep.episode} 集`}
                    </h3>
                    {ep.updated_at && (
                      <p className="text-[10px] text-gray-600">{timeAgo(ep.updated_at)}</p>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* Quick links */}
        <div className="mb-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">快捷入口</h2>
          <div className="grid grid-cols-2 gap-4">
            <button
              onClick={() => navigate('/voices')}
              className="group text-left rounded-xl bg-[#14161e] border border-white/[0.04] hover:border-white/[0.1] hover:bg-[#181b25] transition-all p-4"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-violet-500/10 flex items-center justify-center shrink-0">
                  <svg className="w-4 h-4 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
                  </svg>
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-medium group-hover:text-white transition-colors">音色分配</h3>
                  <p className="text-xs text-gray-600 mt-0.5">管理角色音色分配，预览合成效果</p>
                </div>
                <svg className="w-4 h-4 text-gray-700 group-hover:text-gray-400 shrink-0 ml-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </div>
            </button>

            <button
              onClick={() => navigate('/glossary')}
              className="group text-left rounded-xl bg-[#14161e] border border-white/[0.04] hover:border-white/[0.1] hover:bg-[#181b25] transition-all p-4"
            >
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center shrink-0">
                  <svg className="w-4 h-4 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
                  </svg>
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-medium group-hover:text-white transition-colors">术语表</h3>
                  <p className="text-xs text-gray-600 mt-0.5">维护翻译术语和专有名词</p>
                </div>
                <svg className="w-4 h-4 text-gray-700 group-hover:text-gray-400 shrink-0 ml-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                </svg>
              </div>
            </button>
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="shrink-0 border-t border-white/[0.04] px-8">
        <div className="py-4">
          <p className="text-[11px] text-gray-700">
            Dubora v1.0 &middot; AI 短剧译制平台 &middot; Built by Ning
          </p>
        </div>
      </footer>
    </div>
  )
}
