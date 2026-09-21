import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api'
import type { Application, ApplicationEvent } from '../types'
import { APPLICATION_STATUS_LABELS, BOARD_COLUMNS } from '../types'
import { useProfiles } from '../App'
import {
  EmptyState,
  EVENT_KIND_LABELS,
  Modal,
  useToast,
} from '../components/ui'

/** 每列在卡片上直接提供的高频动作（漏斗内允许跳步，投后严格） */
const QUICK_ACTIONS: Record<string, string[]> = {
  discovered: ['ready_to_apply', 'saved'],
  saved: ['ready_to_apply', 'shortlisted', 'withdrawn'],
  shortlisted: ['ready_to_apply', 'preparing', 'withdrawn'],
  preparing: ['ready_to_apply', 'materials_ready', 'withdrawn'],
  materials_ready: ['ready_to_apply', 'preparing'],
  ready_to_apply: [], // 已投递确认走独立按钮（诚实语义：只有你能确认）
  applied_confirmed: ['interviewing', 'assessment', 'rejected', 'withdrawn'],
  assessment: ['interviewing', 'rejected'],
  interviewing: ['offer', 'rejected'],
  offer: [],
  rejected: [],
  withdrawn: [],
}

const HIDE_FROM_QUICK = new Set(['rejected', 'withdrawn']) // 负面终态走详情确认，不做一键

interface OfferForm {
  base: string
  months: string
  city: string
  workMode: string
  deadline: string
  benefits: string
  notes: string
}

/** 投递看板：状态机驱动，确认投递是独立动作；漏斗可跳步直达待投递 */
export default function ApplicationsPage() {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [apps, setApps] = useState<Application[]>([])
  const [err, setErr] = useState('')
  const [loadFailed, setLoadFailed] = useState(false)
  const [detail, setDetail] = useState<{ app: Application; events: ApplicationEvent[] } | null>(null)
  const [offerFor, setOfferFor] = useState<Application | null>(null)
  const [offerForm, setOfferForm] = useState<OfferForm>({ base: '20', months: '14', city: '', workMode: '', deadline: '', benefits: '', notes: '' })
  const [confirmFor, setConfirmFor] = useState<Application | null>(null)
  const [channel, setChannel] = useState('官网')

  const load = () => {
    if (!activeId) return
    setLoadFailed(false)
    api
      .get<(Application & { job_title?: string; employer_name?: string; job_city?: string })[]>(
        `/applications${qs({ profile_id: activeId })}`,
      )
      .then((as) => setApps(as))
      .catch((e) => {
        setLoadFailed(true)
        setErr(e.message)
      })
  }
  useEffect(load, [activeId])

  const transition = async (id: string, status: string) => {
    try {
      await api.post(`/applications/${id}/transition`, { status })
      toast('success', `已推进到「${APPLICATION_STATUS_LABELS[status] ?? status}」`)
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const doConfirmApplied = async () => {
    if (!confirmFor) return
    const app = confirmFor
    setConfirmFor(null)
    try {
      await api.post(`/applications/${app.id}/confirm-applied`, { channel: channel || null })
      toast('success', '已记录「你确认投递」——这是投后阶段的唯一入口')
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const openDetail = async (app: Application) => {
    try {
      const events = await api.get<ApplicationEvent[]>(`/applications/${app.id}/events`)
      setDetail({ app, events })
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
      await transition(app.id, 'offer')
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const emptyBoard = !apps.length && !loadFailed

  return (
    <div>
      <h1>投递看板</h1>
      <p className="page-sub">
        {apps.length} 条跟踪 · 状态按真实生命周期推进，「已投递」仅在你亲口确认后生效
      </p>
      {err && loadFailed && <div className="error-box">加载失败：{err}</div>}

      {emptyBoard ? (
        <EmptyState
          icon="🗂"
          title="还没有投递跟踪"
          hint="在岗位详情页点「收藏并开始跟踪」，你的求职管线就在这里生长。投递永远由你亲手完成——本系统不代替你向平台发送任何东西。"
          action={<Link className="btn primary" to="/jobs">去收件箱找岗位</Link>}
        />
      ) : (
        <div className="board">
          {BOARD_COLUMNS.map((col) => {
            const colApps = apps.filter((a) => a.status === col)
            const actions = (QUICK_ACTIONS[col] ?? []).filter((s) => !HIDE_FROM_QUICK.has(s))
            return (
              <div className={`board-col${colApps.length ? '' : ' empty-col'}`} key={col}>
                <h3 title={COLUMN_TOOLTIPS[col]}>
                  {APPLICATION_STATUS_LABELS[col]}
                  <span className="count">{colApps.length}</span>
                </h3>
                {colApps.map((a) => (
                  <div className="board-card" key={a.id}>
                    <b>{a.job_title ?? a.job_id.slice(0, 12)}</b>
                    <span className="meta">{a.employer_name}</span>
                    <div className="row" style={{ marginTop: 6, gap: 4, flexWrap: 'wrap' }}>
                      {actions.map((s) => (
                        <button key={s} className="btn small" onClick={() => transition(a.id, s)}>
                          {s === 'ready_to_apply' ? '⚡ 待投递' : `→ ${APPLICATION_STATUS_LABELS[s]}`}
                        </button>
                      ))}
                      {(QUICK_ACTIONS[a.status] ?? []).includes('withdrawn') && (
                        <button
                          className="btn small ghost"
                          title="放弃这个机会（不可撤销）"
                          onClick={() => {
                            if (window.confirm(`确认放弃「${a.job_title ?? '该岗位'}」？此操作不可撤销。`)) {
                              transition(a.id, 'withdrawn')
                            }
                          }}
                        >
                          放弃
                        </button>
                      )}
                      {a.status === 'ready_to_apply' && (
                        <button className="btn small primary" onClick={() => setConfirmFor(a)}>
                          ✅ 我已投递
                        </button>
                      )}
                      {a.status === 'interviewing' && (
                        <button
                          className="btn small primary"
                          onClick={() => {
                            setOfferFor(a)
                            setOfferForm((f) => ({ ...f, city: a.job_city ?? f.city }))
                          }}
                        >
                          + 录入 Offer
                        </button>
                      )}
                      <button className="btn small" onClick={() => openDetail(a)}>
                        时间线
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}

      {detail && (
        <div className="card">
          <div className="between">
            <h2 style={{ margin: 0 }}>事件时间线（不可变审计）</h2>
            <button className="btn small" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <table className="data">
            <thead>
              <tr><th>时间</th><th>事件</th><th>说明</th></tr>
            </thead>
            <tbody>
              {detail.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.occurred_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td>{EVENT_KIND_LABELS[e.kind] ?? e.kind}</td>
                  <td>{e.note || humanPayload(e.payload_json ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 确认投递模态 */}
      <Modal open={!!confirmFor} title="确认已投递" onClose={() => setConfirmFor(null)}>
        <p className="hint">
          「已投递」是你亲手完成投递后的如实回填——这是进入投后阶段的唯一入口，
          系统不会替你投递。确认后记录时间与渠道。
        </p>
        <label className="field">
          投递渠道
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="官网">公司官网</option>
            <option value="BOSS">BOSS直聘</option>
            <option value="内推">内推</option>
            <option value="猎聘">猎聘</option>
            <option value="其他">其他</option>
          </select>
        </label>
        <div className="modal-actions">
          <button className="btn" onClick={() => setConfirmFor(null)}>取消</button>
          <button className="btn primary" onClick={doConfirmApplied}>确认已投递</button>
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
          <button className="btn primary" onClick={submitOffer} disabled={!offerForm.base}>保存</button>
        </div>
      </Modal>
    </div>
  )
}

const COLUMN_TOOLTIPS: Record<string, string> = {
  discovered: '刚收藏，还没细看',
  saved: '看过，值得进一步准备',
  shortlisted: '已进入你的候选名单',
  preparing: '正在准备简历/材料',
  materials_ready: '材料已就绪',
  ready_to_apply: '万事俱备，等你亲手投递',
  applied_confirmed: '你确认已投出',
  assessment: '笔试/测评中',
  interviewing: '面试流程中',
  offer: '已拿到 Offer（去 Offer 比较页）',
}

/** 事件 payload 的最小人性化（不过度解析，审计原文保留） */
function humanPayload(raw: string | null): string {
  if (!raw) return ''
  try {
    const p = JSON.parse(raw) as Record<string, unknown>
    const parts: string[] = []
    if (p.from && p.to) parts.push(`${p.from} → ${p.to}`)
    if (p.user_confirmed) parts.push('用户确认')
    if (p.channel) parts.push(`渠道：${p.channel}`)
    if (p.offer_id) parts.push('收到 Offer')
    if (p.interview_id) parts.push('面试排期')
    if (p.round) parts.push(`第 ${p.round} 轮`)
    return parts.join(' · ') || raw.slice(0, 80)
  } catch {
    return raw.slice(0, 80)
  }
}
