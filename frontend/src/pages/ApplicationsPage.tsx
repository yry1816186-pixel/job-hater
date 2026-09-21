// 投递看板（Huntr 式）：拖拽推进 + 批量操作 + 详情工作台（状态/标签/联系人/提醒/面试练习）。
// 状态机裁决在后端：422 的 detail 列出合法去向，UI 原文照搬不加工。
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api'
import type {
  Application,
  ApplicationEvent,
  Contact,
  InterviewReviewInfo,
  InterviewSessionInfo,
  InterviewSessionStats,
  Job,
  Reminder,
  ReminderSuggestion,
} from '../types'
import { APPLICATION_STATUS_LABELS, BOARD_COLUMNS } from '../types'
import { useProfiles } from '../App'
import {
  ConfirmDialog,
  EmptyState,
  EVENT_KIND_LABELS,
  INTERVIEW_OUTCOME_LABELS,
  Modal,
  useToast,
} from '../components/ui'

// ---------- 本页常量 ----------

/** 面试排期（types.ts 未收录，按后端 Interview 模型声明 UI 实际用到的字段） */
interface InterviewItem {
  id: string
  application_id: string
  round: number
  kind?: string | null
  scheduled_at?: string | null
  location?: string | null
  status: string
  outcome?: string | null
  notes?: string | null
}

interface ColumnGroup {
  key: string
  /** 归入这一列的状态（组列如 待投递=材料就绪+待投递） */
  statuses: string[]
  tooltip: string
}

const COLUMN_TOOLTIPS: Record<string, string> = {
  discovered: '刚入库，还没细看',
  saved: '看过，值得进一步准备',
  shortlisted: '已进入你的候选名单',
  preparing: '正在准备简历/材料',
  ready_to_apply: '材料就绪、等你亲手投递；进入「已投递」必须走「我已投递」确认',
  applied_confirmed: '你确认已投出；测评中的投递也归这一列',
  interviewing: '面试流程中',
  offer: '已拿到 Offer；建议进详情录入薪资明细，去「Offer 比较」页并排看',
  rejected: '被拒记录（复盘素材）',
  ended: '已关闭/已撤回的存档',
}

/** 看板列：主干严格用 BOARD_COLUMNS 顺序；discovered 与 closed/withdrawn 归入首尾扩展列 */
const COLUMN_GROUPS: ColumnGroup[] = [
  { key: 'discovered', statuses: ['discovered'], tooltip: COLUMN_TOOLTIPS.discovered },
  ...BOARD_COLUMNS.map(
    (key): ColumnGroup => ({
      key,
      statuses:
        key === 'ready_to_apply'
          ? ['materials_ready', 'ready_to_apply']
          : key === 'applied_confirmed'
            ? ['applied_confirmed', 'assessment']
            : [key],
      tooltip: COLUMN_TOOLTIPS[key] ?? '',
    }),
  ),
  { key: 'ended', statuses: ['closed', 'withdrawn'], tooltip: COLUMN_TOOLTIPS.ended },
]

const colLabel = (key: string): string =>
  key === 'ended' ? '已结束' : (APPLICATION_STATUS_LABELS[key] ?? key)

/** 与后端状态机一致的 UI 提示（最终裁决在后端，非法会被 422 拒绝） */
const NEXT_STATUSES: Record<string, string[]> = {
  discovered: ['saved', 'shortlisted', 'preparing', 'materials_ready', 'ready_to_apply', 'rejected', 'closed'],
  saved: ['shortlisted', 'preparing', 'materials_ready', 'ready_to_apply', 'withdrawn', 'closed'],
  shortlisted: ['preparing', 'materials_ready', 'ready_to_apply', 'withdrawn', 'closed'],
  preparing: ['materials_ready', 'ready_to_apply', 'withdrawn', 'closed'],
  materials_ready: ['ready_to_apply', 'preparing', 'withdrawn', 'closed'],
  ready_to_apply: ['preparing', 'withdrawn', 'closed'],
  applied_confirmed: ['assessment', 'interviewing', 'rejected', 'withdrawn', 'closed'],
  assessment: ['interviewing', 'rejected', 'withdrawn', 'closed'],
  interviewing: ['offer', 'rejected', 'withdrawn', 'closed'],
  offer: ['closed'],
  rejected: ['closed'],
  withdrawn: ['closed'],
  closed: [],
}

/** 负面/存档去向：反映真实决定，走二次确认，不做一键直推 */
const NEGATIVE_TARGETS = new Set(['rejected', 'withdrawn', 'closed'])

/** 批量推进候选：applied_confirmed 只能逐条经「我已投递」确认，不提供批量 */
const BULK_TARGETS = [
  'saved', 'shortlisted', 'preparing', 'materials_ready', 'ready_to_apply',
  'assessment', 'interviewing', 'rejected', 'withdrawn', 'closed',
]

const CHANNEL_OPTIONS = [
  { value: '官网', label: '公司官网' },
  { value: 'BOSS', label: 'BOSS直聘' },
  { value: '内推', label: '内推' },
  { value: '猎聘', label: '猎聘' },
  { value: '其他', label: '其他' },
]

const INTERVIEW_KINDS = [
  { value: 'behavioral', label: '行为面' },
  { value: 'technical', label: '技术面' },
  { value: 'case', label: '案例面' },
  { value: 'hr', label: 'HR面' },
  { value: 'group', label: '群面' },
  { value: 'final', label: '终面' },
]
const INTERVIEW_KIND_LABELS: Record<string, string> = Object.fromEntries(
  INTERVIEW_KINDS.map((k) => [k.value, k.label]),
)

const INTERVIEW_STATUS_LABELS: Record<string, string> = {
  planned: '已排期',
  done: '已完成',
  cancelled: '已取消',
}

type ScoreKey = 'structure' | 'clarity' | 'technical' | 'evidence_consistency'
const SCORE_DIMS: Array<{ key: ScoreKey; label: string }> = [
  { key: 'structure', label: '结构' },
  { key: 'clarity', label: '表达' },
  { key: 'technical', label: '技术' },
  { key: 'evidence_consistency', label: '证据一致' },
]

const SECTION_TITLE = { margin: '18px 0 8px', fontSize: 14 }

interface OfferForm {
  base: string
  months: string
  city: string
  workMode: string
  deadline: string
  benefits: string
  notes: string
}

/** 多行文本 → 行数组（自评的亮点/待改进） */
function lines(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean)
}

/** 提醒到期展示：今天→「今天」，过期→标红由样式处理 */
function dueInfo(due: string): { text: string; overdue: boolean } {
  const d = due.slice(0, 10)
  const today = new Date().toISOString().slice(0, 10)
  return { text: d === today ? '今天' : d, overdue: d < today }
}

/** 事件 payload 的最小人性化（不过度解析，审计原文保留） */
function humanPayload(raw: string | null): string {
  if (!raw) return ''
  try {
    const p = JSON.parse(raw) as Record<string, unknown>
    const parts: string[] = []
    if (p.from && p.to) parts.push(`${String(p.from)} → ${String(p.to)}`)
    if (p.user_confirmed) parts.push('用户确认')
    if (p.channel) parts.push(`渠道：${String(p.channel)}`)
    if (p.offer_id) parts.push('收到 Offer')
    if (p.interview_id) parts.push('面试排期')
    if (p.round) parts.push(`第 ${String(p.round)} 轮`)
    if (p.tag) parts.push(`标签：${String(p.tag)}`)
    return parts.join(' · ') || raw.slice(0, 80)
  } catch {
    return raw.slice(0, 80)
  }
}

// ---------- 页面 ----------

/** 投递看板：拖拽推进 + 批量 + 双视图；「已投递」只能经用户确认进入 */
export default function ApplicationsPage() {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [apps, setApps] = useState<Application[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [loadFailed, setLoadFailed] = useState(false)
  const [view, setView] = useState<'board' | 'list'>('board')
  const [tagFilter, setTagFilter] = useState('')
  const [bulkMode, setBulkMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkTarget, setBulkTarget] = useState('')
  const [bulkTag, setBulkTag] = useState('')
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [dropCol, setDropCol] = useState<string | null>(null)
  const [detailApp, setDetailApp] = useState<Application | null>(null)
  const [confirmFor, setConfirmFor] = useState<Application | null>(null)
  const [channel, setChannel] = useState('官网')
  const [offerFor, setOfferFor] = useState<Application | null>(null)
  const [offerForm, setOfferForm] = useState<OfferForm>({ base: '20', months: '14', city: '', workMode: '', deadline: '', benefits: '', notes: '' })
  const [newJobId, setNewJobId] = useState('')
  const [pickJob, setPickJob] = useState('')
  const [recentJobs, setRecentJobs] = useState<Job[]>([])

  const load = () => {
    if (!activeId) return
    setLoadFailed(false)
    api
      .get<Application[]>(`/applications${qs({ profile_id: activeId })}`)
      .then((as) => {
        setApps(as)
        // 详情若开着，同步为最新数据（页面级操作也会刷新它）
        setDetailApp((cur) => (cur ? (as.find((a) => a.id === cur.id) ?? null) : null))
        setLoading(false)
      })
      .catch((e) => {
        setLoadFailed(true)
        setErr((e as Error).message)
        setLoading(false)
      })
  }
  useEffect(load, [activeId])

  // 新建投递用的最近岗位下拉（失败不砸页面，只是下拉为空）
  useEffect(() => {
    if (!activeId) {
      setRecentJobs([])
      return
    }
    api
      .get<{ total: number; items: Job[] }>(`/jobs${qs({ limit: 8 })}`)
      .then((r) => setRecentJobs(r.items))
      .catch(() => setRecentJobs([]))
  }, [activeId])

  /** 推进状态；成功返回 true。422 的 detail 会列出合法去向，原文展示 */
  const applyTransition = async (id: string, status: string): Promise<boolean> => {
    try {
      await api.post<Application>(`/applications/${id}/transition`, { status })
      toast('success', `已推进到「${APPLICATION_STATUS_LABELS[status] ?? status}」`)
      load()
      return true
    } catch (e) {
      toast('error', (e as Error).message)
      return false
    }
  }

  const doConfirmApplied = async () => {
    if (!confirmFor) return
    const app = confirmFor
    setConfirmFor(null)
    try {
      await api.post<Application>(`/applications/${app.id}/confirm-applied`, { channel: channel || null })
      toast('success', '已记录「你确认投递」——这是投后阶段的唯一入口')
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  /** 拖放落点 → 状态推进；applied_confirmed 列特殊：引导走确认门 */
  const handleDrop = (appId: string, colKey: string) => {
    const app = apps.find((a) => a.id === appId)
    const group = COLUMN_GROUPS.find((g) => g.key === colKey)
    if (!app || !group) return
    if (group.statuses.includes(app.status)) {
      toast('info', '这条投递已经在这一列了')
      return
    }
    if (colKey === 'applied_confirmed') {
      toast('info', '「已投递」只能经「我已投递」确认进入——请如实回填')
      setConfirmFor(app)
      return
    }
    void applyTransition(appId, group.statuses[0])
  }

  const createApplication = async () => {
    if (!activeId || !newJobId.trim()) return
    try {
      await api.post<{ id: string }>('/applications', { job_id: newJobId.trim(), profile_id: activeId })
      toast('success', '已新建投递跟踪，卡片在「刚发现」列')
      setNewJobId('')
      setPickJob('')
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  /** 批量推进：逐条调用状态机（非法会被拒绝），失败汇总成一条提示 */
  const bulkTransition = async () => {
    if (!bulkTarget || selectedIds.size === 0) return
    const failures: string[] = []
    let ok = 0
    for (const id of selectedIds) {
      const app = apps.find((a) => a.id === id)
      try {
        await api.post(`/applications/${id}/transition`, { status: bulkTarget })
        ok += 1
      } catch (e) {
        const name = app?.job_title ?? app?.employer_name ?? id.slice(0, 8)
        failures.push(`${name}：${(e as Error).message}`)
      }
    }
    if (failures.length > 0) {
      toast(
        'error',
        `批量推进：成功 ${ok}、失败 ${failures.length}。${failures.slice(0, 2).join('；')}${failures.length > 2 ? '…' : ''}`,
      )
    } else {
      toast('success', `已批量推进 ${ok} 条投递`)
    }
    setSelectedIds(new Set())
    setBulkTarget('')
    load()
  }

  const bulkAddTag = async () => {
    const t = bulkTag.trim()
    if (!t || selectedIds.size === 0) return
    const failures: string[] = []
    let ok = 0
    for (const id of selectedIds) {
      try {
        await api.post<Application>(`/applications/${id}/tags/${encodeURIComponent(t)}`)
        ok += 1
      } catch (e) {
        failures.push(`${id.slice(0, 8)}：${(e as Error).message}`)
      }
    }
    if (failures.length > 0) {
      toast('error', `批量加标签：成功 ${ok}、失败 ${failures.length}。${failures.slice(0, 2).join('；')}`)
    } else {
      toast('success', `已为 ${ok} 条投递加上「${t}」`)
    }
    setSelectedIds(new Set())
    setBulkTag('')
    load()
  }

  /** 详情内改动（状态/标签）后同步列表与详情 */
  const updateApp = (next: Application) => {
    setApps((prev) => prev.map((a) => (a.id === next.id ? next : a)))
    setDetailApp((cur) => (cur && cur.id === next.id ? next : cur))
  }

  const removeCardTag = async (app: Application, tag: string) => {
    try {
      const next = await api.del<Application>(`/applications/${app.id}/tags/${encodeURIComponent(tag)}`)
      updateApp(next)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const submitOffer = async () => {
    if (!offerFor) return
    const app = offerFor
    setOfferFor(null)
    try {
      await api.post('/offers', {
        application_id: app.id,
        base_salary_k: Number(offerForm.base),
        salary_months: offerForm.months ? Number(offerForm.months) : null,
        city: offerForm.city || null,
        work_mode: offerForm.workMode || null,
        deadline: offerForm.deadline || null,
        benefits: offerForm.benefits.split(/[,，\s]+/).filter(Boolean),
        notes: offerForm.notes || null,
      })
      toast('success', 'Offer 已录入，去「Offer 比较」页并排看')
      await applyTransition(app.id, 'offer')
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  if (!activeId) {
    return (
      <div>
        <h1>投递看板</h1>
        <EmptyState
          icon="👤"
          title="还没有画像"
          hint="先建立求职画像，才能开始跟踪投递。"
          action={<Link className="btn primary" to="/profile">去建档</Link>}
        />
      </div>
    )
  }

  const allTags = Array.from(new Set(apps.flatMap((a) => a.tags))).sort((a, b) => a.localeCompare(b, 'zh'))
  const visibleApps = tagFilter ? apps.filter((a) => a.tags.includes(tagFilter)) : apps

  return (
    <div>
      <div className="between">
        <div>
          <h1>投递看板</h1>
          <p className="page-sub">
            {apps.length} 条跟踪 · 拖动卡片推进状态，「已投递」仅在你亲手确认后生效
          </p>
        </div>
        <a
          className="btn"
          href={`/api/calendar/ics?profile_id=${activeId}`}
          download="jobhater.ics"
          title="投递截止+面试排期导出为 iCalendar，可导入系统/Google 日历"
        >
          📅 导出日历
        </a>
      </div>
      {err && loadFailed && <div className="error-box">加载失败：{err}</div>}
      {loading && !loadFailed && <p className="loading">加载中…</p>}

      {/* 工具栏：视图切换 / 批量 / 导出 / 新建投递 */}
      <div className="board-toolbar">
        <span className="row" style={{ gap: 4 }}>
          <button className={`btn small${view === 'board' ? ' primary' : ''}`} onClick={() => setView('board')}>看板</button>
          <button className={`btn small${view === 'list' ? ' primary' : ''}`} onClick={() => setView('list')}>列表</button>
        </span>
        <button
          className={`btn small${bulkMode ? ' primary' : ''}`}
          onClick={() => {
            setBulkMode((v) => !v)
            setSelectedIds(new Set())
          }}
          title="勾选多张卡片后批量推进状态或加标签"
        >
          {bulkMode ? '退出批量' : '批量选择'}
        </button>
        <a
          className="btn small"
          href={`/api/export/applications.csv?profile_id=${activeId}`}
          download="applications.csv"
          title="导出全部投递为 CSV（Excel 可直接打开）"
        >
          ⬇ 导出 CSV
        </a>
        <form
          className="row"
          style={{ marginLeft: 'auto' }}
          onSubmit={(e) => {
            e.preventDefault()
            void createApplication()
          }}
        >
          <select
            value={pickJob}
            onChange={(e) => {
              setPickJob(e.target.value)
              setNewJobId(e.target.value)
            }}
            style={{ width: 'auto', maxWidth: 240 }}
            aria-label="从最近岗位选择"
          >
            <option value="">从最近岗位选择…</option>
            {recentJobs.map((j) => (
              <option key={j.id} value={j.id}>{j.title} · {j.employer_name}</option>
            ))}
          </select>
          <input
            type="text"
            placeholder="或粘贴 job_id"
            value={newJobId}
            onChange={(e) => {
              setNewJobId(e.target.value)
              setPickJob('')
            }}
            style={{ width: 180 }}
            aria-label="岗位 ID"
          />
          <button className="btn small primary" type="submit" disabled={!newJobId.trim()}>＋ 新建投递</button>
        </form>
      </div>

      {/* 标签筛选 */}
      {allTags.length > 0 && (
        <div className="board-toolbar">
          <span style={{ fontSize: 13, color: 'var(--muted)' }}>按标签筛选：</span>
          {allTags.map((t) => (
            <span
              key={t}
              className="tag-chip"
              role="button"
              tabIndex={0}
              style={{
                cursor: 'pointer',
                ...(tagFilter === t ? { outline: '2px solid var(--accent)', outlineOffset: 1 } : null),
              }}
              onClick={() => setTagFilter((cur) => (cur === t ? '' : t))}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setTagFilter((cur) => (cur === t ? '' : t))
                }
              }}
              title={tagFilter === t ? '点击取消筛选' : '点击只看带此标签的投递'}
            >
              {t}
            </span>
          ))}
          {tagFilter && (
            <button className="btn small ghost" onClick={() => setTagFilter('')}>清除筛选</button>
          )}
        </div>
      )}

      {/* 批量操作条 */}
      {bulkMode && (
        <div className="board-toolbar">
          <b>已选 {selectedIds.size} 条</b>
          <select
            value={bulkTarget}
            onChange={(e) => setBulkTarget(e.target.value)}
            style={{ width: 'auto' }}
            aria-label="批量推进目标状态"
          >
            <option value="">批量推进到…</option>
            {BULK_TARGETS.map((s) => (
              <option key={s} value={s}>{APPLICATION_STATUS_LABELS[s] ?? s}</option>
            ))}
          </select>
          <button className="btn small" disabled={!bulkTarget || selectedIds.size === 0} onClick={() => void bulkTransition()}>
            执行推进
          </button>
          <input
            type="text"
            placeholder="批量加标签"
            value={bulkTag}
            onChange={(e) => setBulkTag(e.target.value)}
            style={{ width: 140 }}
            aria-label="批量加标签"
          />
          <button className="btn small" disabled={!bulkTag.trim() || selectedIds.size === 0} onClick={() => void bulkAddTag()}>
            应用标签
          </button>
          <button className="btn small ghost" disabled={selectedIds.size === 0} onClick={() => setSelectedIds(new Set())}>
            清除选择
          </button>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>逐条走状态机，非法推进会被拒绝并汇总提示</span>
        </div>
      )}

      {apps.length === 0 && !loadFailed && !loading ? (
        <EmptyState
          icon="🗂"
          title="还没有投递跟踪"
          hint="在岗位详情页点「收藏并开始跟踪」，或直接在上面粘贴 job_id 新建。投递永远由你亲手完成——本系统不代替你向平台发送任何东西。"
          action={<Link className="btn primary" to="/jobs">去收件箱找岗位</Link>}
        />
      ) : view === 'board' ? (
        visibleApps.length === 0 && tagFilter ? (
          <p className="hint">没有带「{tagFilter}」标签的投递。点上面的标签可取消筛选。</p>
        ) : (
          <div className="kanban">
          {COLUMN_GROUPS.map((g) => {
            const colApps = visibleApps.filter((a) => g.statuses.includes(a.status))
            return (
              <div
                key={g.key}
                className={`kanban-col${dropCol === g.key ? ' drop-target' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault()
                  e.dataTransfer.dropEffect = 'move'
                  if (dropCol !== g.key) setDropCol(g.key)
                }}
                onDragLeave={() => setDropCol((c) => (c === g.key ? null : c))}
                onDrop={(e) => {
                  e.preventDefault()
                  const id = e.dataTransfer.getData('text/plain')
                  setDropCol(null)
                  if (id) handleDrop(id, g.key)
                }}
              >
                <div className="kanban-col-head" title={g.tooltip}>
                  <span>{colLabel(g.key)}</span>
                  <span className="count">{colApps.length}</span>
                </div>
                {colApps.length === 0 && (
                  <div className="kanban-empty-hint">
                    {g.key === 'applied_confirmed' ? '点「我已投递」确认后进入' : '把卡片拖到这里'}
                  </div>
                )}
                {colApps.map((a) => (
                  <BoardCard
                    key={a.id}
                    app={a}
                    columnKey={g.key}
                    dragging={draggingId === a.id}
                    selected={selectedIds.has(a.id)}
                    bulkMode={bulkMode}
                    onOpen={setDetailApp}
                    onToggleSelect={toggleSelect}
                    onRemoveTag={removeCardTag}
                    onDragStart={setDraggingId}
                    onDragEnd={() => {
                      setDraggingId(null)
                      setDropCol(null)
                    }}
                  />
                ))}
              </div>
            )
          })}
          </div>
        )
      ) : visibleApps.length === 0 && tagFilter ? (
        <p className="hint">没有带「{tagFilter}」标签的投递。点上面的标签可取消筛选。</p>
      ) : (
        <table className="data">
          <thead>
            <tr><th>状态</th><th>岗位</th><th>城市</th><th>标签</th><th>最近更新</th><th></th></tr>
          </thead>
          <tbody>
            {visibleApps.map((a) => (
              <tr key={a.id}>
                <td>{APPLICATION_STATUS_LABELS[a.status] ?? a.status}</td>
                <td><b>{a.employer_name ?? '未知公司'} · {a.job_title ?? a.job_id.slice(0, 12)}</b></td>
                <td>{a.job_city || '—'}</td>
                <td>{a.tags.join('、') || '—'}</td>
                <td>{a.updated_at?.slice(0, 10) ?? '—'}</td>
                <td><button className="btn small" onClick={() => setDetailApp(a)}>详情</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 确认投递模态（拖到「已投递」列时被引导到这里） */}
      <Modal open={!!confirmFor} title="确认已投递" onClose={() => setConfirmFor(null)}>
        <p className="hint">
          「已投递」是你亲手完成投递后的如实回填——这是进入投后阶段的唯一入口，
          系统不会替你投递。确认后记录时间与渠道。
        </p>
        <label className="field">
          投递渠道
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            {CHANNEL_OPTIONS.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </label>
        <div className="modal-actions">
          <button className="btn" onClick={() => setConfirmFor(null)}>取消</button>
          <button className="btn primary" onClick={() => void doConfirmApplied()}>确认已投递</button>
        </div>
      </Modal>

      {/* Offer 录入模态（字段与比较维度对齐） */}
      <Modal open={!!offerFor} title={`录入 Offer：${offerFor?.job_title ?? ''}`} onClose={() => setOfferFor(null)}>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            月基本薪资（K）*
            <input type="number" value={offerForm.base} onChange={(e) => setOfferForm({ ...offerForm, base: e.target.value })} />
          </label>
          <label className="field" style={{ width: 120 }}>
            年薪月数
            <input type="number" placeholder="14" value={offerForm.months} onChange={(e) => setOfferForm({ ...offerForm, months: e.target.value })} />
          </label>
          <label className="field" style={{ width: 130 }}>
            城市
            <input type="text" value={offerForm.city} onChange={(e) => setOfferForm({ ...offerForm, city: e.target.value })} />
          </label>
        </div>
        <div className="row">
          <label className="field" style={{ width: 130 }}>
            工作方式
            <select value={offerForm.workMode} onChange={(e) => setOfferForm({ ...offerForm, workMode: e.target.value })}>
              <option value="">未定</option>
              <option value="onsite">坐班</option>
              <option value="hybrid">混合</option>
              <option value="remote">远程</option>
            </select>
          </label>
          <label className="field" style={{ flex: 1 }}>
            接受截止日
            <input type="date" value={offerForm.deadline} onChange={(e) => setOfferForm({ ...offerForm, deadline: e.target.value })} />
          </label>
        </div>
        <label className="field">
          福利（逗号分隔）
          <input type="text" placeholder="五险一金, 餐补, 房补" value={offerForm.benefits} onChange={(e) => setOfferForm({ ...offerForm, benefits: e.target.value })} />
        </label>
        <label className="field">
          备注
          <textarea style={{ minHeight: 48 }} value={offerForm.notes} onChange={(e) => setOfferForm({ ...offerForm, notes: e.target.value })} />
        </label>
        <div className="modal-actions">
          <button className="btn" onClick={() => setOfferFor(null)}>取消</button>
          <button className="btn primary" onClick={() => void submitOffer()} disabled={!offerForm.base}>保存</button>
        </div>
      </Modal>

      {/* 详情工作台 */}
      {detailApp && (
        <DetailModal
          app={detailApp}
          onClose={() => setDetailApp(null)}
          onAppUpdated={updateApp}
          onReload={load}
          onRecordOffer={(a) => {
            setOfferFor(a)
            setOfferForm((f) => ({ ...f, city: a.job_city ?? f.city }))
          }}
        />
      )}
    </div>
  )
}

// ---------- 看板卡片 ----------

function BoardCard(props: {
  app: Application
  columnKey: string
  dragging: boolean
  selected: boolean
  bulkMode: boolean
  onOpen: (a: Application) => void
  onToggleSelect: (id: string) => void
  onRemoveTag: (a: Application, tag: string) => void
  onDragStart: (id: string) => void
  onDragEnd: () => void
}) {
  const { app, columnKey } = props
  const foreign = app.status !== columnKey // 组列内标注真实状态（如「测评中」在已投递列）
  return (
    <div
      className={`kanban-card${props.dragging ? ' dragging' : ''}${props.selected ? ' selected' : ''}`}
      draggable
      data-app-id={app.id}
      onClick={() => props.onOpen(app)}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', app.id)
        e.dataTransfer.effectAllowed = 'move'
        props.onDragStart(app.id)
      }}
      onDragEnd={props.onDragEnd}
    >
      <div className="card-title">
        {props.bulkMode && (
          <input
            type="checkbox"
            checked={props.selected}
            onChange={() => props.onToggleSelect(app.id)}
            onClick={(e) => e.stopPropagation()}
            aria-label={`选择 ${app.job_title ?? app.job_id}`}
            style={{ marginRight: 6, width: 'auto', verticalAlign: 'middle' }}
          />
        )}
        {app.employer_name ?? '未知公司'} · {app.job_title ?? app.job_id.slice(0, 12)}
      </div>
      <div className="card-sub">
        {app.job_city || '城市未知'}
        {foreign && ` · 实际状态：${APPLICATION_STATUS_LABELS[app.status] ?? app.status}`}
      </div>
      {app.tags.length > 0 && (
        <div className="card-meta">
          {app.tags.map((t) => (
            <span className="tag-chip" key={t}>
              {t}
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  props.onRemoveTag(app, t)
                }}
                aria-label={`移除标签 ${t}`}
                title="移除标签"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------- 详情工作台 ----------

function DetailModal(props: {
  app: Application
  onClose: () => void
  onAppUpdated: (a: Application) => void
  onReload: () => void
  onRecordOffer: (a: Application) => void
}) {
  const { app } = props
  return (
    <Modal
      open
      title={`${app.employer_name ?? '未知公司'} · ${app.job_title ?? app.job_id.slice(0, 12)}`}
      onClose={props.onClose}
    >
      <StatusSection app={app} onAppUpdated={props.onAppUpdated} onRecordOffer={props.onRecordOffer} />
      <TagsSection app={app} onAppUpdated={props.onAppUpdated} />
      <ContactsSection appId={app.id} />
      <RemindersSection app={app} />
      <InterviewsSection app={app} onReload={props.onReload} />
    </Modal>
  )
}

// ---------- ① 基本信息与状态推进 ----------

function StatusSection(props: {
  app: Application
  onAppUpdated: (a: Application) => void
  onRecordOffer: (a: Application) => void
}) {
  const { app } = props
  const { toast } = useToast()
  const [events, setEvents] = useState<ApplicationEvent[] | null>(null)
  const [eventsErr, setEventsErr] = useState('')
  const [confirmStatus, setConfirmStatus] = useState('')
  const [channel, setChannel] = useState('官网')
  const [tick, setTick] = useState(0)

  // app 每次被更新（状态/标签变化）都刷新审计时间线
  useEffect(() => {
    let alive = true
    api
      .get<ApplicationEvent[]>(`/applications/${app.id}/events`)
      .then((es) => {
        if (alive) setEvents(es)
      })
      .catch((e) => {
        if (alive) {
          setEventsErr((e as Error).message)
          setEvents([])
        }
      })
    return () => {
      alive = false
    }
  }, [app, tick])

  const move = async (status: string) => {
    try {
      const next = await api.post<Application>(`/applications/${app.id}/transition`, { status })
      toast('success', `已推进到「${APPLICATION_STATUS_LABELS[status] ?? status}」`)
      props.onAppUpdated(next)
      setTick((t) => t + 1)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const confirmApplied = async () => {
    try {
      const next = await api.post<Application>(`/applications/${app.id}/confirm-applied`, { channel: channel || null })
      toast('success', '已记录「你确认投递」——这是投后阶段的唯一入口')
      props.onAppUpdated(next)
      setTick((t) => t + 1)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const onTarget = (s: string) => {
    if (s === 'offer') {
      props.onRecordOffer(app) // Offer 先录明细再推进（比较页要用）
      return
    }
    if (NEGATIVE_TARGETS.has(s)) {
      setConfirmStatus(s)
      return
    }
    void move(s)
  }

  const targets = NEXT_STATUSES[app.status] ?? []

  return (
    <section>
      <h4 style={{ margin: '4px 0 8px', fontSize: 14 }}>基本信息与状态推进</h4>
      <dl className="kv">
        <dt>当前状态</dt>
        <dd><b>{APPLICATION_STATUS_LABELS[app.status] ?? app.status}</b></dd>
        <dt>城市</dt>
        <dd>{app.job_city || '—'}</dd>
        <dt>投递渠道</dt>
        <dd>{app.apply_channel || '—'}</dd>
        <dt>确认时间</dt>
        <dd>{app.applied_at ? app.applied_at.slice(0, 19).replace('T', ' ') : '—'}</dd>
        <dt>job_id</dt>
        <dd style={{ wordBreak: 'break-all' }}>{app.job_id}</dd>
      </dl>

      {app.status === 'ready_to_apply' && (
        <div className="subcard" style={{ borderColor: 'var(--accent)' }}>
          <b>✅ 确认已投递</b>
          <p className="hint" style={{ margin: '4px 0 8px' }}>
            投后阶段的唯一入口：只有你能确认「我真的投出去了」。确认后记录渠道与时间。
          </p>
          <div className="row">
            <select value={channel} onChange={(e) => setChannel(e.target.value)} style={{ width: 'auto' }} aria-label="投递渠道">
              {CHANNEL_OPTIONS.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
            <button className="btn small primary" onClick={() => void confirmApplied()}>我已投递</button>
          </div>
        </div>
      )}

      {targets.length > 0 && (
        <div className="row" style={{ marginTop: 10 }}>
          <span style={{ fontSize: 13, color: 'var(--muted)' }}>推进到：</span>
          {targets.map((s) => (
            <button
              key={s}
              className={`btn small${NEGATIVE_TARGETS.has(s) ? ' ghost' : ''}`}
              title={NEGATIVE_TARGETS.has(s) ? '负面/存档去向，需二次确认' : undefined}
              onClick={() => onTarget(s)}
            >
              → {APPLICATION_STATUS_LABELS[s] ?? s}
            </button>
          ))}
        </div>
      )}

      <h4 style={SECTION_TITLE}>事件时间线（不可变审计）</h4>
      {eventsErr && <div className="error-box">时间线加载失败：{eventsErr}</div>}
      {events === null && !eventsErr && <p className="hint">加载中…</p>}
      {events && (
        events.length === 0 ? (
          <p className="hint">暂无事件</p>
        ) : (
          <table className="data">
            <thead>
              <tr><th>时间</th><th>事件</th><th>说明</th></tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{e.occurred_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td>{EVENT_KIND_LABELS[e.kind] ?? e.kind}</td>
                  <td>{e.note || humanPayload(e.payload_json ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}

      <ConfirmDialog
        open={confirmStatus !== ''}
        title="确认推进到这个状态？"
        body={`将推进为「${APPLICATION_STATUS_LABELS[confirmStatus] ?? confirmStatus}」。这反映你的真实决定（放弃/存档），审计流会如实记录。`}
        confirmText="确认推进"
        danger
        onConfirm={() => {
          const s = confirmStatus
          setConfirmStatus('')
          if (s) void move(s)
        }}
        onCancel={() => setConfirmStatus('')}
      />
    </section>
  )
}

// ---------- ② 标签 ----------

function TagsSection(props: { app: Application; onAppUpdated: (a: Application) => void }) {
  const { app } = props
  const { toast } = useToast()
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  const add = async () => {
    const t = input.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      const next = await api.post<Application>(`/applications/${app.id}/tags/${encodeURIComponent(t)}`)
      props.onAppUpdated(next)
      setInput('')
      toast('success', `已加标签「${t}」`)
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (t: string) => {
    try {
      const next = await api.del<Application>(`/applications/${app.id}/tags/${encodeURIComponent(t)}`)
      props.onAppUpdated(next)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  return (
    <section>
      <h4 style={SECTION_TITLE}>标签</h4>
      <div className="row">
        {app.tags.map((t) => (
          <span className="tag-chip" key={t}>
            {t}
            <button onClick={() => void remove(t)} aria-label={`移除标签 ${t}`} title="移除标签">×</button>
          </span>
        ))}
        {app.tags.length === 0 && <span className="hint">还没有标签</span>}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <input
          type="text"
          placeholder="新标签，如：内推 / 高优先"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void add()
            }
          }}
          style={{ width: 200 }}
          aria-label="新标签"
        />
        <button className="btn small" disabled={!input.trim() || busy} onClick={() => void add()}>添加</button>
      </div>
    </section>
  )
}

// ---------- ③ 联系人 ----------

interface ContactForm {
  name: string
  role: string
  phone: string
  email: string
  wechat: string
  note: string
}
const EMPTY_CONTACT_FORM: ContactForm = { name: '', role: '', phone: '', email: '', wechat: '', note: '' }

function ContactsSection(props: { appId: string }) {
  const { appId } = props
  const { toast } = useToast()
  const [contacts, setContacts] = useState<Contact[] | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [form, setForm] = useState<ContactForm>(EMPTY_CONTACT_FORM)
  const [adding, setAdding] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<ContactForm>(EMPTY_CONTACT_FORM)
  const [deleteTarget, setDeleteTarget] = useState<Contact | null>(null)

  useEffect(() => {
    let alive = true
    setContacts(null)
    api
      .get<Contact[]>(`/contacts${qs({ application_id: appId })}`)
      .then((cs) => {
        if (alive) setContacts(cs)
      })
      .catch((e) => {
        if (alive) {
          setErr((e as Error).message)
          setContacts([])
        }
      })
    return () => {
      alive = false
    }
  }, [appId, tick])

  const reload = () => setTick((t) => t + 1)

  const add = async () => {
    if (!form.name.trim()) {
      toast('info', '联系人至少要有一个名字')
      return
    }
    setAdding(true)
    try {
      await api.post<Contact>('/contacts', {
        name: form.name.trim(),
        application_id: appId,
        role: form.role || null,
        phone: form.phone || null,
        email: form.email || null,
        wechat: form.wechat || null,
        note: form.note || null,
      })
      toast('success', '已添加联系人')
      setForm(EMPTY_CONTACT_FORM)
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setAdding(false)
    }
  }

  const startEdit = (c: Contact) => {
    setEditingId(c.id)
    setEditForm({
      name: c.name ?? '',
      role: c.role ?? '',
      phone: c.phone ?? '',
      email: c.email ?? '',
      wechat: c.wechat ?? '',
      note: c.note ?? '',
    })
  }

  const saveEdit = async () => {
    if (editingId === null) return
    try {
      // 置空字段传空串（后端约定：None 保持原值，空串=清空）
      await api.patch<Contact>(`/contacts/${editingId}`, {
        name: editForm.name.trim() || undefined,
        role: editForm.role,
        phone: editForm.phone,
        email: editForm.email,
        wechat: editForm.wechat,
        note: editForm.note,
      })
      toast('success', '已保存联系人修改')
      setEditingId(null)
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const doDelete = async (c: Contact) => {
    try {
      await api.del(`/contacts/${c.id}`)
      toast('success', `已删除联系人「${c.name}」`)
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const gridStyle = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 8 } as const

  return (
    <section>
      <h4 style={SECTION_TITLE}>联系人</h4>
      {err && <div className="error-box">联系人加载失败：{err}</div>}
      {contacts === null && !err && <p className="hint">加载中…</p>}
      {contacts && contacts.length === 0 && <p className="hint">还没有联系人。HR、面试官、内推人都可以记在这里。</p>}

      {contacts?.map((c) => (
        <div key={c.id} className="subcard">
          {editingId === c.id ? (
            <>
              <div style={gridStyle}>
                <label className="field">姓名*
                  <input type="text" value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} />
                </label>
                <label className="field">角色
                  <input type="text" value={editForm.role} onChange={(e) => setEditForm({ ...editForm, role: e.target.value })} />
                </label>
                <label className="field">电话
                  <input type="text" value={editForm.phone} onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })} />
                </label>
                <label className="field">邮箱
                  <input type="text" value={editForm.email} onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} />
                </label>
                <label className="field">微信
                  <input type="text" value={editForm.wechat} onChange={(e) => setEditForm({ ...editForm, wechat: e.target.value })} />
                </label>
                <label className="field">备注
                  <input type="text" value={editForm.note} onChange={(e) => setEditForm({ ...editForm, note: e.target.value })} />
                </label>
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn small primary" disabled={!editForm.name.trim()} onClick={() => void saveEdit()}>保存</button>
                <button className="btn small ghost" onClick={() => setEditingId(null)}>取消</button>
              </div>
            </>
          ) : (
            <>
              <div className="between">
                <span><b>{c.name}</b>{c.role ? ` · ${c.role}` : ''}</span>
                <span className="row" style={{ gap: 6 }}>
                  <button className="btn small" onClick={() => startEdit(c)}>编辑</button>
                  <button className="btn small ghost" onClick={() => setDeleteTarget(c)}>删除</button>
                </span>
              </div>
              <p className="hint" style={{ margin: '4px 0 0', wordBreak: 'break-all' }}>
                {[c.phone, c.email, c.wechat].filter(Boolean).join(' · ') || '未留联系方式'}
                {c.note ? ` — ${c.note}` : ''}
              </p>
            </>
          )}
        </div>
      ))}

      <div className="subcard">
        <b>添加联系人</b>
        <div style={gridStyle}>
          <label className="field">姓名*
            <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="field">角色（HR/面试官/内推人）
            <input type="text" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} />
          </label>
          <label className="field">电话
            <input type="text" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          </label>
          <label className="field">邮箱
            <input type="text" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </label>
          <label className="field">微信
            <input type="text" value={form.wechat} onChange={(e) => setForm({ ...form, wechat: e.target.value })} />
          </label>
          <label className="field">备注
            <input type="text" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn small" disabled={adding} onClick={() => void add()}>＋ 添加</button>
        </div>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除联系人"
        body={`删除「${deleteTarget?.name ?? ''}」？删除后不可恢复。`}
        confirmText="删除"
        danger
        onConfirm={() => {
          const c = deleteTarget
          setDeleteTarget(null)
          if (c) void doDelete(c)
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </section>
  )
}

// ---------- ④ 提醒 ----------

function RemindersSection(props: { app: Application }) {
  const { app } = props
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [reminders, setReminders] = useState<Reminder[] | null>(null)
  const [suggestions, setSuggestions] = useState<ReminderSuggestion[]>([])
  const [err, setErr] = useState('')
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    setReminders(null)
    api
      .get<Reminder[]>(`/reminders${qs({ owner_kind: 'application', owner_id: app.id })}`)
      .then((rs) => {
        if (alive) setReminders(rs)
      })
      .catch((e) => {
        if (alive) {
          setErr((e as Error).message)
          setReminders([])
        }
      })
    // 建议失败不砸提醒列表
    if (activeId) {
      api
        .get<ReminderSuggestion[]>(`/reminders/suggestions${qs({ profile_id: activeId })}`)
        .then((all) => {
          if (alive) setSuggestions(all.filter((s) => s.owner_kind === 'application' && s.owner_id === app.id))
        })
        .catch(() => {
          if (alive) setSuggestions([])
        })
    }
    return () => {
      alive = false
    }
  }, [app.id, activeId, tick])

  const reload = () => setTick((t) => t + 1)

  const toggle = async (r: Reminder) => {
    try {
      await api.patch<Reminder>(`/reminders/${r.id}`, { done: !r.done })
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const create = async () => {
    if (!title.trim() || !due) {
      toast('info', '提醒需要标题和日期')
      return
    }
    try {
      await api.post<Reminder>('/reminders', {
        owner_kind: 'application',
        owner_id: app.id,
        due_at: due,
        title: title.trim(),
      })
      toast('success', '已创建提醒')
      setTitle('')
      setDue('')
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const adopt = async (s: ReminderSuggestion) => {
    try {
      await api.post<Reminder>('/reminders', {
        owner_kind: s.owner_kind,
        owner_id: s.owner_id,
        due_at: s.due_at,
        title: s.title,
        kind: s.kind,
      })
      toast('success', '已采纳为提醒')
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  return (
    <section>
      <h4 style={SECTION_TITLE}>提醒</h4>
      {err && <div className="error-box">提醒加载失败：{err}</div>}
      {reminders === null && !err && <p className="hint">加载中…</p>}
      {reminders?.map((r) => {
        const d = dueInfo(r.due_at)
        return (
          <div
            key={r.id}
            className={`reminder-item${r.done ? ' done' : ''}${d.overdue && !r.done ? ' overdue' : ''}`}
          >
            <span className="due">{d.overdue && !r.done ? `⚠ ${d.text}` : d.text}</span>
            <span className="title" style={{ flex: 1 }}>{r.title}</span>
            <button className="btn small" onClick={() => void toggle(r)}>{r.done ? '撤销' : '完成'}</button>
          </div>
        )
      })}
      {reminders !== null && reminders.length === 0 && !err && <p className="hint">暂无提醒</p>}

      <div className="row" style={{ marginTop: 8 }}>
        <input
          type="text"
          placeholder="提醒标题，如：三天没回音就跟进"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          style={{ width: 220 }}
          aria-label="提醒标题"
        />
        <input
          type="date"
          value={due}
          onChange={(e) => setDue(e.target.value)}
          style={{ width: 150 }}
          aria-label="提醒日期"
        />
        <button className="btn small" disabled={!title.trim() || !due} onClick={() => void create()}>＋ 提醒</button>
      </div>

      {suggestions.map((s) => (
        <div key={`${s.kind}:${s.owner_id}:${s.title}`} className="suggestion-item">
          <div>{s.title}</div>
          <div className="why">{s.reason} · 系统建议，不自动创建</div>
          <button className="btn small" onClick={() => void adopt(s)}>采纳</button>
        </div>
      ))}
    </section>
  )
}

// ---------- ⑤ 面试与练习 ----------

function InterviewsSection(props: { app: Application; onReload: () => void }) {
  const { app } = props
  const { toast } = useToast()
  const [interviews, setInterviews] = useState<InterviewItem[] | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [round, setRound] = useState('1')
  const [kind, setKind] = useState('')
  const [scheduledAt, setScheduledAt] = useState('')
  const [location, setLocation] = useState('')
  const [scheduling, setScheduling] = useState(false)
  const [finishFor, setFinishFor] = useState<string | null>(null)
  const [finishOutcome, setFinishOutcome] = useState('pending')
  const [finishNotes, setFinishNotes] = useState('')
  const [practice, setPractice] = useState<InterviewSessionInfo | null>(null)

  useEffect(() => {
    let alive = true
    setInterviews(null)
    api
      .get<InterviewItem[]>(`/applications/${app.id}/interviews`)
      .then((ivs) => {
        if (alive) setInterviews(ivs)
      })
      .catch((e) => {
        if (alive) {
          setErr((e as Error).message)
          setInterviews([])
        }
      })
    return () => {
      alive = false
    }
  }, [app.id, tick])

  const reload = () => setTick((t) => t + 1)

  const schedule = async () => {
    setScheduling(true)
    try {
      await api.post<InterviewItem>(`/applications/${app.id}/interviews`, {
        round: Math.max(Number(round) || 1, 1),
        kind: kind || null,
        scheduled_at: scheduledAt || null,
        location: location || null,
      })
      toast('success', '面试已排期（会出现在「导出日历」里）')
      setScheduledAt('')
      setLocation('')
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setScheduling(false)
    }
  }

  const finishInterview = async (iv: InterviewItem) => {
    try {
      await api.post(`/interviews/${iv.id}/finish`, { outcome: finishOutcome, notes: finishNotes || null })
      toast('success', '已记录该轮结果')
      setFinishFor(null)
      reload()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const startPractice = async (iv: InterviewItem) => {
    try {
      const s = await api.post<InterviewSessionInfo>('/interview-sessions', {
        interview_id: iv.id,
        mode: 'mock',
      })
      if (practice) {
        toast('info', '已开启新练习；上一段未结束的会话仍保留在记录里')
      }
      setPractice(s)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  return (
    <section>
      <h4 style={SECTION_TITLE}>面试与练习</h4>
      {err && <div className="error-box">面试加载失败：{err}</div>}
      {interviews === null && !err && <p className="hint">加载中…</p>}
      {interviews !== null && interviews.length === 0 && !err && (
        <p className="hint">还没有面试排期。有面试了就在下面排一轮。</p>
      )}

      {interviews?.map((iv) => (
        <div key={iv.id} className="subcard">
          <div className="between">
            <b>第 {iv.round} 轮 · {INTERVIEW_KIND_LABELS[iv.kind ?? ''] ?? '类型未定'}</b>
            <span className={`tag${iv.status === 'done' ? ' green' : ''}`}>
              {INTERVIEW_STATUS_LABELS[iv.status] ?? iv.status}
              {iv.outcome ? ` · ${INTERVIEW_OUTCOME_LABELS[iv.outcome] ?? iv.outcome}` : ''}
            </span>
          </div>
          <p className="hint" style={{ margin: '4px 0' }}>
            {iv.scheduled_at ? `时间：${iv.scheduled_at.slice(0, 16).replace('T', ' ')}` : '时间：未定'}
            {iv.location ? ` · 地点：${iv.location}` : ''}
          </p>
          <div className="row">
            <button className="btn small primary" onClick={() => void startPractice(iv)}>▶ 开始练习</button>
            {iv.status !== 'done' && (
              <button
                className="btn small"
                onClick={() => {
                  setFinishFor(iv.id)
                  setFinishOutcome('pending')
                  setFinishNotes('')
                }}
              >
                录入该轮结果
              </button>
            )}
          </div>
          {finishFor === iv.id && (
            <div className="row" style={{ marginTop: 8 }}>
              <select value={finishOutcome} onChange={(e) => setFinishOutcome(e.target.value)} style={{ width: 'auto' }} aria-label="该轮结果">
                {Object.entries(INTERVIEW_OUTCOME_LABELS).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
              <input
                type="text"
                placeholder="备注（可选）"
                value={finishNotes}
                onChange={(e) => setFinishNotes(e.target.value)}
                style={{ width: 180 }}
                aria-label="面试备注"
              />
              <button className="btn small" onClick={() => void finishInterview(iv)}>保存结果</button>
              <button className="btn small ghost" onClick={() => setFinishFor(null)}>取消</button>
            </div>
          )}
        </div>
      ))}

      <div className="subcard">
        <b>排一轮面试</b>
        <div className="row" style={{ marginTop: 6 }}>
          <input
            type="number"
            min={1}
            placeholder="轮次"
            value={round}
            onChange={(e) => setRound(e.target.value)}
            style={{ width: 80 }}
            aria-label="面试轮次"
          />
          <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 'auto' }} aria-label="面试类型">
            <option value="">类型未定</option>
            {INTERVIEW_KINDS.map((k) => (
              <option key={k.value} value={k.value}>{k.label}</option>
            ))}
          </select>
          <input
            type="datetime-local"
            value={scheduledAt}
            onChange={(e) => setScheduledAt(e.target.value)}
            style={{ width: 'auto' }}
            aria-label="面试时间"
          />
          <input
            type="text"
            placeholder="地点/链接"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            style={{ width: 140 }}
            aria-label="面试地点"
          />
          <button className="btn small" disabled={scheduling} onClick={() => void schedule()}>＋ 排期</button>
        </div>
      </div>

      {practice && <PracticeView session={practice} onClosed={() => setPractice(null)} />}
    </section>
  )
}

/** 模拟面试练习器：逐字稿 + 角色轮换 + 确定性统计 + 自评复盘 */
function PracticeView(props: { session: InterviewSessionInfo; onClosed: () => void }) {
  const { session: initialSession } = props
  const { toast } = useToast()
  const [session, setSession] = useState(initialSession)
  const [role, setRole] = useState<'candidate' | 'interviewer'>('candidate')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [ended, setEnded] = useState(Boolean(initialSession.ended_at))
  const [stats, setStats] = useState<InterviewSessionStats | null>(null)
  const [reviews, setReviews] = useState<InterviewReviewInfo[]>([])
  const [scores, setScores] = useState<Record<ScoreKey, number>>({
    structure: 5,
    clarity: 5,
    technical: 5,
    evidence_consistency: 5,
  })
  const [strengths, setStrengths] = useState('')
  const [gaps, setGaps] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const transcriptRef = useRef<HTMLDivElement | null>(null)

  // 新一轮追加时滚到底部
  useEffect(() => {
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [session.transcript.length])

  const send = async () => {
    const content = draft.trim()
    if (!content || sending) return
    setSending(true)
    try {
      const next = await api.post<InterviewSessionInfo>(`/interview-sessions/${session.id}/turns`, {
        role,
        content,
      })
      setSession(next)
      setDraft('')
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setSending(false)
    }
  }

  const end = async () => {
    try {
      await api.post(`/interview-sessions/${session.id}/end`)
    } catch (e) {
      toast('error', (e as Error).message)
      return
    }
    setEnded(true)
    toast('success', '练习已结束——看看统计，如实给自己打个分')
    // 统计与复盘各自加载，互不阻塞（某一项失败只提示那一项）
    api
      .get<InterviewSessionStats>(`/interview-sessions/${session.id}/stats`)
      .then(setStats)
      .catch((e) => toast('error', `统计加载失败：${(e as Error).message}`))
    api
      .get<InterviewReviewInfo[]>(`/interview-sessions/${session.id}/reviews`)
      .then(setReviews)
      .catch((e) => toast('error', `复盘加载失败：${(e as Error).message}`))
  }

  const submitReview = async () => {
    setSubmitting(true)
    try {
      const r = await api.post<InterviewReviewInfo>(`/interview-sessions/${session.id}/reviews/self`, {
        scores: { ...scores },
        strengths: lines(strengths),
        gaps: lines(gaps),
      })
      setReviews((prev) => [...prev, r])
      toast('success', '自评已保存——对照逐字稿，下次专练薄弱项')
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="subcard">
      <div className="between">
        <b>模拟面试练习（mock）</b>
        <button className="btn small ghost" onClick={props.onClosed}>收起练习</button>
      </div>
      <p className="hint" style={{ margin: '4px 0 8px' }}>
        本地逐字稿练习：你既是面试官也是回答者。结束后看确定性统计并自评——数据只在你机器上。
      </p>

      <div className="transcript" ref={transcriptRef}>
        {session.transcript.length === 0 && (
          <div className="hint">还没有内容。切到「面试官提问」，写一个可能被问到的问题开始。</div>
        )}
        {session.transcript.map((t, i) => (
          <div key={i} className={`turn ${t.role}`}>
            <span className="who">{t.role === 'interviewer' ? '面试官' : '我'}</span>
            {t.content}
          </div>
        ))}
      </div>

      <div className="turn-input">
        <select
          value={role}
          disabled={ended}
          onChange={(e) => setRole(e.target.value === 'interviewer' ? 'interviewer' : 'candidate')}
          style={{ width: 'auto' }}
          aria-label="发言角色"
        >
          <option value="candidate">我在回答</option>
          <option value="interviewer">面试官提问</option>
        </select>
        <input
          type="text"
          placeholder={role === 'candidate' ? '我的回答…' : '面试官的问题…'}
          value={draft}
          disabled={ended}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void send()
            }
          }}
          aria-label="发言内容"
        />
        <button className="btn small" disabled={ended || !draft.trim() || sending} onClick={() => void send()}>发送</button>
        {!ended && (
          <button className="btn small primary" disabled={session.transcript.length === 0} onClick={() => void end()}>
            结束练习
          </button>
        )}
      </div>

      {stats && (
        <>
          <h4 style={SECTION_TITLE}>练习统计（确定性计算）</h4>
          <dl className="kv">
            <dt>问题数</dt><dd>{stats.questions}</dd>
            <dt>回答数</dt><dd>{stats.answers}</dd>
            <dt>未接问题</dt>
            <dd>{stats.unanswered_trailing}{stats.unanswered_trailing > 0 ? '（结尾还有问题没回答）' : ''}</dd>
            <dt>平均回答字数</dt><dd>{stats.answer_chars.avg}</dd>
            <dt>时长（分钟）</dt><dd>{stats.duration_min ?? '—'}</dd>
          </dl>

          <h4 style={SECTION_TITLE}>自评（0-10 分，如实打）</h4>
          {SCORE_DIMS.map((d) => (
            <label key={d.key} className="field" style={{ marginBottom: 6 }}>
              {d.label}：<b>{scores[d.key]}</b> / 10
              <input
                type="range"
                min={0}
                max={10}
                step={1}
                value={scores[d.key]}
                onChange={(e) => setScores({ ...scores, [d.key]: Number(e.target.value) })}
              />
            </label>
          ))}
          <label className="field">
            亮点（每行一条）
            <textarea
              style={{ minHeight: 60 }}
              value={strengths}
              onChange={(e) => setStrengths(e.target.value)}
              placeholder={'结构清楚，先结论后细节'}
            />
          </label>
          <label className="field">
            待改进（每行一条）
            <textarea
              style={{ minHeight: 60 }}
              value={gaps}
              onChange={(e) => setGaps(e.target.value)}
              placeholder={'项目数字记不准，卡壳两次'}
            />
          </label>
          <div className="row">
            <button className="btn small primary" disabled={submitting} onClick={() => void submitReview()}>
              提交自评
            </button>
          </div>
        </>
      )}

      {reviews.length > 0 && (
        <>
          <h4 style={SECTION_TITLE}>本次会话的复盘</h4>
          {reviews.map((r) => (
            <div key={r.id} className="subcard">
              <b>{r.ai_generated ? 'AI 复盘' : '自评'}</b>
              <div className="row" style={{ marginTop: 4 }}>
                {SCORE_DIMS.filter((d) => r.scores[d.key] !== undefined).map((d) => (
                  <span key={d.key} className="tag">{d.label} {r.scores[d.key]}/10</span>
                ))}
                {r.overall != null && <span className="tag green">综合 {r.overall}</span>}
              </div>
              {r.strengths.length > 0 && (
                <p style={{ margin: '6px 0 0', fontSize: 13 }}>亮点：{r.strengths.join('；')}</p>
              )}
              {r.gaps.length > 0 && (
                <p style={{ margin: '2px 0', fontSize: 13 }}>待改进：{r.gaps.join('；')}</p>
              )}
              {r.practice_items.length > 0 && (
                <p style={{ margin: '2px 0', fontSize: 13 }}>练习项：{r.practice_items.join('；')}</p>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  )
}
