import { useEffect, useState } from 'react'
import { api, qs } from '../api'
import type { Offer, OfferCompareRow } from '../types'
import { useProfiles } from '../App'

/** Offer 比较：结构化事实并排，价值判断留给你 */
export default function OffersPage() {
  const { activeId } = useProfiles()
  const [offers, setOffers] = useState<Offer[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [compare, setCompare] = useState<OfferCompareRow[] | null>(null)
  const [err, setErr] = useState('')

  const load = () => {
    if (!activeId) return
    api.get<Offer[]>(`/offers${qs({ profile_id: activeId })}`).then((os) => {
      setOffers(os.filter((o) => o.status !== 'declined' && o.status !== 'expired'))
    })
  }
  useEffect(load, [activeId])

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

  const setStatus = async (id: string, status: string) => {
    await api.post(`/offers/${id}/status`, { status })
    load()
  }

  return (
    <div>
      <h1>Offer 比较</h1>
      <p className="page-sub">
        只呈现结构化事实（薪资/城市/福利/自定义维度），不做价值判断——权重和取舍是你的决定。
      </p>
      {err && <div className="error-box">{err}</div>}
      {offers.length === 0 ? (
        <div className="empty">
          还没有 Offer 记录。在投递看板的「面试中」卡片上点 + Offer 录入。
        </div>
      ) : (
        <>
          <table className="data">
            <thead>
              <tr>
                <th></th>
                <th>公司/岗位</th>
                <th>月薪(K)</th>
                <th>月数</th>
                <th>年薪(K)</th>
                <th>城市</th>
                <th>截止</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {offers.map((o) => (
                <tr key={o.id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(o.id)}
                      onChange={() => toggle(o.id)}
                      aria-label="选择比较"
                    />
                  </td>
                  <td>{o.id.slice(0, 8)}…（详情见比较视图）</td>
                  <td>{o.base_salary_k}</td>
                  <td>{o.salary_months ?? '—'}</td>
                  <td>
                    <b>{Math.round(o.base_salary_k * (o.salary_months ?? 12))}</b>
                  </td>
                  <td>{o.city ?? '—'}</td>
                  <td>{o.deadline ?? '—'}</td>
                  <td>
                    <span className={`tag ${o.status === 'accepted' ? 'green' : ''}`}>{o.status}</span>{' '}
                    {o.status === 'considering' && (
                      <>
                        <button className="btn small" onClick={() => setStatus(o.id, 'accepted')}>
                          接受
                        </button>{' '}
                        <button className="btn small danger" onClick={() => setStatus(o.id, 'declined')}>
                          放弃
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={doCompare} disabled={selected.size < 2}>
              并排比较（选 {selected.size} 个，至少 2 个）
            </button>
          </div>
        </>
      )}

      {compare && (
        <div style={{ overflowX: 'auto' }}>
          <h2>比较视图</h2>
          <table className="data">
            <thead>
              <tr>
                <th></th>
                {compare.map((c) => (
                  <th key={c.id}>
                    {c.employer_name}
                    <br />
                    <span style={{ fontWeight: 400 }}>{c.job_title}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ['月薪(K)', (c: OfferCompareRow) => String(c.base_salary_k)],
                  ['月数', (c: OfferCompareRow) => String(c.salary_months ?? '—')],
                  ['年基本薪资(K)', (c: OfferCompareRow) => String(c.annual_base_k)],
                  ['城市', (c: OfferCompareRow) => c.city ?? '—'],
                  ['办公模式', (c: OfferCompareRow) => c.work_mode ?? '—'],
                  ['福利', (c: OfferCompareRow) => c.benefits.join('、') || '—'],
                  ['接受截止', (c: OfferCompareRow) => c.deadline ?? '—'],
                  ['备注', (c: OfferCompareRow) => c.notes ?? '—'],
                ] as [string, (c: OfferCompareRow) => string][]
              ).map(([label, fn]) => (
                <tr key={label}>
                  <td style={{ color: 'var(--muted)' }}>{label}</td>
                  {compare.map((c) => (
                    <td key={c.id}>{fn(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
