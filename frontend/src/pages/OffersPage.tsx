import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api'
import type { Offer, OfferCompareRow } from '../types'
import { useProfiles } from '../App'
import { ConfirmDialog, EmptyState, OFFER_STATUS_LABELS, useToast } from '../components/ui'

type OfferRow = Offer & { job_title?: string; employer_name?: string; job_city?: string }

/** Offer 比较：结构化事实并排，价值判断留给你 */
export default function OffersPage() {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [offers, setOffers] = useState<OfferRow[]>([])
  const [showDeclined, setShowDeclined] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [compare, setCompare] = useState<OfferCompareRow[] | null>(null)
  const [err, setErr] = useState('')
  const [confirmAction, setConfirmAction] = useState<{ id: string; status: 'accepted' | 'declined' } | null>(null)

  const load = () => {
    if (!activeId) return
    api
      .get<OfferRow[]>(`/offers${qs({ profile_id: activeId })}`)
      .then((os) => setOffers(os))
      .catch((e) => setErr(e.message))
  }
  useEffect(load, [activeId])

  const visible = offers.filter((o) =>
    showDeclined ? true : o.status !== 'declined' && o.status !== 'expired',
  )
  const active = visible.filter((o) => o.status === 'considering' || o.status === 'accepted')

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const doCompare = async () => {
    setErr('')
    try {
      setCompare(await api.post<OfferCompareRow[]>('/offers/compare', { offer_ids: [...selected] }))
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const runConfirm = async () => {
    if (!confirmAction) return
    const { id, status } = confirmAction
    setConfirmAction(null)
    try {
      await api.post(`/offers/${id}/status`, { status })
      toast('success', status === 'accepted' ? '已标记接受 🎉' : '已婉拒（可切换视图查看历史记录）')
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  return (
    <div>
      <h1>Offer 比较</h1>
      <p className="page-sub">
        只呈现结构化事实（薪资/城市/福利/自定义维度），不做价值判断——权重和取舍是你的决定。
      </p>
      {err && <div className="error-box">{err}</div>}

      {offers.length === 0 ? (
        <EmptyState
          icon="🤝"
          title="还没有 Offer 记录"
          hint="在投递看板的「面试中」卡片上点「+ 录入 Offer」，把口头或书面 Offer 的关键条款记下来比较。"
          action={<Link className="btn primary" to="/applications">去投递看板</Link>}
        />
      ) : (
        <>
          <label className="hint" style={{ display: 'block', marginBottom: 10 }}>
            <input
              type="checkbox"
              checked={showDeclined}
              onChange={(e) => setShowDeclined(e.target.checked)}
            />{' '}
            显示已婉拒/已过期的 Offer（历史可查）
          </label>
          <table className="data">
            <thead>
              <tr>
                <th>比较</th>
                <th>岗位 / 公司</th>
                <th>状态</th>
                <th>月基本 (K)</th>
                <th>月数</th>
                <th>年薪 (K)</th>
                <th>城市</th>
                <th>福利</th>
                <th>截止</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((o) => (
                <tr key={o.id} style={{ opacity: o.status === 'declined' || o.status === 'expired' ? 0.55 : 1 }}>
                  <td>
                    {(o.status === 'considering' || o.status === 'accepted') && (
                      <input type="checkbox" checked={selected.has(o.id)} onChange={() => toggle(o.id)} aria-label="选择比较" />
                    )}
                  </td>
                  <td>
                    <b>{o.job_title ?? '岗位已删除'}</b>
                    <div className="meta">{o.employer_name}</div>
                  </td>
                  <td>
                    <span className={`tag ${o.status === 'accepted' ? 'green' : o.status === 'declined' ? '' : 'amber'}`}>
                      {OFFER_STATUS_LABELS[o.status] ?? o.status}
                    </span>
                  </td>
                  <td>{o.base_salary_k}</td>
                  <td>{o.salary_months ?? '—'}</td>
                  <td><b>{Math.round(o.base_salary_k * (o.salary_months ?? 12))}</b></td>
                  <td>{o.city ?? '—'}</td>
                  <td>{o.benefits?.length ? o.benefits.join('、') : '—'}</td>
                  <td>{o.deadline ?? '—'}</td>
                  <td>
                    {o.status === 'considering' && (
                      <div className="row" style={{ gap: 4 }}>
                        <button className="btn small primary" onClick={() => setConfirmAction({ id: o.id, status: 'accepted' })}>
                          接受
                        </button>
                        <button className="btn small" onClick={() => setConfirmAction({ id: o.id, status: 'declined' })}>
                          婉拒
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {active.length >= 1 && (
            <div className="row" style={{ marginTop: 14 }}>
              <button className="btn primary" onClick={doCompare} disabled={selected.size < 1}>
                并排比较选中（{selected.size}）
              </button>
              <span className="hint">勾选两行以上比较更有意义</span>
            </div>
          )}

          {compare && (
            <div className="card" style={{ marginTop: 14, overflowX: 'auto' }}>
              <h2 style={{ marginTop: 0 }}>并排比较（事实并置，不含价值判断）</h2>
              <table className="data">
                <thead>
                  <tr>
                    <th>维度</th>
                    {compare.map((c) => <th key={c.id}>{c.job_title ?? c.id.slice(0, 8)}</th>)}
                  </tr>
                </thead>
                <tbody>
                  <tr><td>公司</td>{compare.map((c) => <td key={c.id}>{c.employer_name ?? '—'}</td>)}</tr>
                  <tr><td>年总包 (K)</td>{compare.map((c) => <td key={c.id}><b>{c.annual_base_k}</b></td>)}</tr>
                  <tr><td>月基本/月数</td>{compare.map((c) => <td key={c.id}>{c.base_salary_k} × {c.salary_months ?? 12}</td>)}</tr>
                  <tr><td>城市</td>{compare.map((c) => <td key={c.id}>{c.job_city ?? c.city ?? '—'}</td>)}</tr>
                  <tr><td>工作方式</td>{compare.map((c) => <td key={c.id}>{c.work_mode ?? '—'}</td>)}</tr>
                  <tr><td>福利</td>{compare.map((c) => <td key={c.id}>{(c.benefits ?? []).join('、') || '—'}</td>)}</tr>
                  <tr><td>试用/ probation</td>{compare.map((c) => <td key={c.id}>{(c as { probation_months?: number | null }).probation_months ?? '—'}</td>)}</tr>
                  <tr><td>截止</td>{compare.map((c) => <td key={c.id}>{c.deadline ?? '—'}</td>)}</tr>
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={!!confirmAction}
        title={confirmAction?.status === 'accepted' ? '接受这个 Offer？' : '婉拒这个 Offer？'}
        body={
          confirmAction?.status === 'accepted'
            ? '接受后该 Offer 标记为已接受。求职是你的决定——系统只记录事实。'
            : '婉拒后从默认列表隐藏（可勾选「显示已婉拒」找回）。可反悔改回考虑中。'
        }
        confirmText={confirmAction?.status === 'accepted' ? '确认接受' : '确认婉拒'}
        danger={confirmAction?.status === 'declined'}
        onConfirm={runConfirm}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}
