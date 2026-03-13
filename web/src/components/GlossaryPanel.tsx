import { useEffect, useState, useCallback } from 'react'
import type { Drama } from '../types/asr-model'
import { fetchJson, postJson, putJson, deleteJson } from '../utils/api'

interface DictEntry {
  id: number
  drama_id: number
  drama_name: string | null
  type: string
  src: string
  target: string
}

const TYPE_LABEL: Record<string, string> = {
  name: '人名',
  slang: '俚语',
  term: '术语',
}

const TYPE_COLOR: Record<string, string> = {
  name: 'bg-blue-500/10 text-blue-400',
  slang: 'bg-amber-500/10 text-amber-400',
  term: 'bg-emerald-500/10 text-emerald-400',
}

interface Props {
  dramaId?: number
  dramaName?: string
}

export function GlossaryPanel({ dramaId, dramaName }: Props) {
  const embedded = dramaId != null

  const [entries, setEntries] = useState<DictEntry[]>([])
  const [dramas, setDramas] = useState<Drama[]>([])
  const [loading, setLoading] = useState(true)
  const [filterDrama, setFilterDrama] = useState<number | 'all'>('all')
  const [editing, setEditing] = useState<number | null>(null)
  const [editData, setEditData] = useState({ drama_id: 0, type: 'name', src: '', target: '' })
  const [adding, setAdding] = useState(false)
  const [newData, setNewData] = useState({ drama_id: dramaId ?? 0, type: 'name', src: '', target: '' })

  const load = useCallback(async () => {
    try {
      const glossaryUrl = embedded ? `/glossary?drama_id=${dramaId}` : '/glossary'
      if (embedded) {
        const e = await fetchJson<DictEntry[]>(glossaryUrl)
        setEntries(e)
      } else {
        const [dResp, e] = await Promise.all([
          fetchJson<{ items: Drama[] }>('/dramas'),
          fetchJson<DictEntry[]>(glossaryUrl),
        ])
        const d = dResp.items ?? []
        setDramas(d)
        setEntries(e)
        if (d.length > 0 && newData.drama_id === 0) {
          setNewData(prev => ({ ...prev, drama_id: d[0].id }))
        }
      }
    } finally {
      setLoading(false)
    }
  }, [dramaId, embedded])

  useEffect(() => { load() }, [load])

  const filtered = !embedded && filterDrama !== 'all'
    ? entries.filter(e => e.drama_id === filterDrama)
    : entries

  const handleSave = async (id: number) => {
    await putJson(`/glossary/${id}`, editData)
    setEditing(null)
    await load()
  }

  const handleDelete = async (id: number) => {
    await deleteJson(`/glossary/${id}`)
    await load()
  }

  const handleAdd = async () => {
    const targetDramaId = dramaId ?? newData.drama_id
    if (!targetDramaId || !newData.src.trim() || !newData.target.trim()) return
    await postJson('/glossary', { ...newData, drama_id: targetDramaId })
    setAdding(false)
    setNewData(prev => ({ ...prev, src: '', target: '' }))
    await load()
  }

  // Grid template: embedded hides drama column
  const gridCols = embedded
    ? 'grid-cols-[1fr_1fr_80px_60px]'
    : 'grid-cols-[1fr_1fr_80px_120px_60px]'

  return (
    <div className="min-h-full px-8 py-6 max-w-[1000px]">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            {embedded ? `${dramaName} 术语表` : '术语表'}
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">共 {entries.length} 条</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Drama filter — only in standalone mode */}
          {!embedded && (
            <select
              value={filterDrama}
              onChange={e => setFilterDrama(e.target.value === 'all' ? 'all' : Number(e.target.value))}
              className="h-8 px-3 text-xs rounded-lg bg-[#14161e] border border-white/[0.06] text-gray-300 outline-none"
            >
              <option value="all">全部项目</option>
              {dramas.map(d => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          )}
          <button
            onClick={() => setAdding(true)}
            disabled={!embedded && dramas.length === 0}
            className="h-8 px-4 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            + 添加
          </button>
        </div>
      </div>

      {/* Add form */}
      {adding && (
        <div className="mb-4 p-4 rounded-xl bg-[#14161e] border border-white/[0.06]">
          <div className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider block mb-1">原文</label>
              <input
                autoFocus
                value={newData.src}
                onChange={e => setNewData(d => ({ ...d, src: e.target.value }))}
                onKeyDown={e => e.key === 'Escape' && setAdding(false)}
                className="w-full h-8 px-3 text-sm rounded-lg bg-[#0a0b10] border border-white/10 outline-none focus:border-blue-500/60 placeholder:text-gray-600"
                placeholder="e.g. 老汉"
              />
            </div>
            <div className="flex-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider block mb-1">译文</label>
              <input
                value={newData.target}
                onChange={e => setNewData(d => ({ ...d, target: e.target.value }))}
                onKeyDown={e => { if (e.key === 'Enter') handleAdd(); if (e.key === 'Escape') setAdding(false) }}
                className="w-full h-8 px-3 text-sm rounded-lg bg-[#0a0b10] border border-white/10 outline-none focus:border-blue-500/60 placeholder:text-gray-600"
                placeholder="e.g. daddy"
              />
            </div>
            <select
              value={newData.type}
              onChange={e => setNewData(d => ({ ...d, type: e.target.value }))}
              className="h-8 px-2 text-xs rounded-lg bg-[#0a0b10] border border-white/10 outline-none"
            >
              <option value="name">人名</option>
              <option value="slang">俚语</option>
              <option value="term">术语</option>
            </select>
            {/* Drama selector — only in standalone mode */}
            {!embedded && (
              <select
                value={newData.drama_id}
                onChange={e => setNewData(d => ({ ...d, drama_id: Number(e.target.value) }))}
                className="h-8 px-2 text-xs rounded-lg bg-[#0a0b10] border border-white/10 outline-none max-w-[120px]"
              >
                {dramas.map(d => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            )}
            <button onClick={handleAdd} className="h-8 px-4 text-xs font-medium rounded-lg bg-blue-600 hover:bg-blue-500 transition-colors shrink-0">保存</button>
            <button onClick={() => setAdding(false)} className="h-8 px-3 text-xs rounded-lg text-gray-400 hover:text-gray-200 hover:bg-white/[0.04] transition-colors shrink-0">取消</button>
          </div>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="space-y-2">
          {[1,2,3].map(i => <div key={i} className="h-10 rounded-lg bg-[#14161e] animate-pulse" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-gray-600 text-sm">暂无条目</div>
      ) : (
        <div className="rounded-xl border border-white/[0.04] overflow-hidden">
          {/* Header */}
          <div className={`grid ${gridCols} gap-4 px-5 py-2 text-[10px] font-medium uppercase tracking-wider text-gray-600 bg-[#12141c] border-b border-white/[0.04]`}>
            <span>原文</span>
            <span>译文</span>
            <span>类型</span>
            {!embedded && <span>项目</span>}
            <span />
          </div>

          {filtered.map((entry, i) => (
            <div
              key={entry.id}
              className={`grid ${gridCols} gap-4 items-center px-5 py-2.5 hover:bg-[#14161e] transition-colors ${
                i < filtered.length - 1 ? 'border-b border-white/[0.03]' : ''
              }`}
            >
              {editing === entry.id ? (
                <>
                  <input
                    value={editData.src}
                    onChange={e => setEditData(d => ({ ...d, src: e.target.value }))}
                    className="h-7 px-2 text-sm rounded bg-[#0a0b10] border border-white/10 outline-none focus:border-blue-500/60"
                  />
                  <input
                    value={editData.target}
                    onChange={e => setEditData(d => ({ ...d, target: e.target.value }))}
                    onKeyDown={e => { if (e.key === 'Enter') handleSave(entry.id); if (e.key === 'Escape') setEditing(null) }}
                    className="h-7 px-2 text-sm rounded bg-[#0a0b10] border border-white/10 outline-none focus:border-blue-500/60"
                  />
                  <select
                    value={editData.type}
                    onChange={e => setEditData(d => ({ ...d, type: e.target.value }))}
                    className="h-7 px-1 text-xs rounded bg-[#0a0b10] border border-white/10 outline-none"
                  >
                    <option value="name">人名</option>
                    <option value="slang">俚语</option>
                    <option value="term">术语</option>
                  </select>
                  {!embedded && <span className="text-xs text-gray-500 truncate">{entry.drama_name}</span>}
                  <div className="flex gap-1">
                    <button onClick={() => handleSave(entry.id)} className="text-[10px] text-blue-400 hover:text-blue-300">保存</button>
                    <button onClick={() => setEditing(null)} className="text-[10px] text-gray-500 hover:text-gray-300">取消</button>
                  </div>
                </>
              ) : (
                <>
                  <span className="text-sm truncate">{entry.src}</span>
                  <span className="text-sm text-gray-300 truncate">{entry.target}</span>
                  <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded w-fit ${TYPE_COLOR[entry.type] ?? 'bg-gray-500/10 text-gray-400'}`}>
                    {TYPE_LABEL[entry.type] ?? entry.type}
                  </span>
                  {!embedded && <span className="text-xs text-gray-500 truncate">{entry.drama_name}</span>}
                  <div className="flex gap-2">
                    <button
                      onClick={() => { setEditing(entry.id); setEditData({ drama_id: entry.drama_id, type: entry.type, src: entry.src, target: entry.target }) }}
                      className="text-[10px] text-gray-600 hover:text-gray-300 transition-colors"
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => handleDelete(entry.id)}
                      className="text-[10px] text-gray-600 hover:text-red-400 transition-colors"
                    >
                      删除
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
