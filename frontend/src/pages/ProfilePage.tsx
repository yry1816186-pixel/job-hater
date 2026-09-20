import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Evidence, Preset, ProfileView } from '../types'
import { useProfiles } from '../App'

/** 我的画像：档案 + 证据复核 + 求职偏好（无任何默认用户假设） */
export default function ProfilePage() {
  const { profiles, activeId, refresh } = useProfiles()
  const [view, setView] = useState<ProfileView | null>(null)
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [presets, setPresets] = useState<Preset[]>([])
  const [err, setErr] = useState('')
  const [newName, setNewName] = useState('')
  const [newHeadline, setNewHeadline] = useState('')

  // 表单态（保持简单受控）
  const [skillName, setSkillName] = useState('')
  const [skillAliases, setSkillAliases] = useState('')
  const [evText, setEvText] = useState('')
  const [presetForm, setPresetForm] = useState({
    name: '', roles: '', cities: '', salaryMin: '', types: 'campus', maxExp: '', gradYear: '',
  })

  const load = () => {
    if (!activeId) return
    setErr('')
    Promise.all([
      api.get<ProfileView>(`/profiles/${activeId}`),
      api.get<Evidence[]>(`/profiles/${activeId}/evidence`),
      api.get<Preset[]>(`/profiles/${activeId}/presets`),
    ])
      .then(([v, e, p]) => {
        setView(v)
        setEvidence(e)
        setPresets(p)
      })
      .catch((er) => setErr(er.message))
  }
  useEffect(load, [activeId])

  const createProfile = async () => {
    if (!newName.trim()) return
    const p = await api.post<ProfileView['profile']>('/profiles', {
      display_name: newName.trim(),
      headline: newHeadline.trim() || null,
    })
    setNewName('')
    setNewHeadline('')
    await refresh()
    localStorage.setItem('jobhater-active-profile', p.id)
    window.location.reload()
  }

  const addSkill = async () => {
    if (!skillName.trim() || !activeId) return
    try {
      await api.post(`/profiles/${activeId}/skills`, {
        name: skillName.trim(),
        aliases: skillAliases.split(/[,，\s]+/).filter(Boolean),
      })
      setSkillName('')
      setSkillAliases('')
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const addEvidence = async () => {
    if (!evText.trim() || !activeId) return
    await api.post(`/profiles/${activeId}/evidence`, {
      original_text: evText.trim(),
      source_kind: 'paste',
    })
    setEvText('')
    load()
  }

  const confirmAll = async () => {
    const unconfirmed = evidence.filter((e) => !e.user_confirmed).map((e) => e.id)
    if (!unconfirmed.length || !activeId) return
    const r = await api.post<{ confirmed: number }>(`/profiles/${activeId}/evidence/confirm`, {
      evidence_ids: unconfirmed,
    })
    setMsg(`已确认 ${r.confirmed} 条事实`)
    load()
  }
  const [msg, setMsg] = useState('')

  const createPreset = async () => {
    if (!activeId || !presetForm.name.trim()) return
    try {
      const p = await api.post<Preset>(`/profiles/${activeId}/presets`, {
        name: presetForm.name.trim(),
        target_roles: presetForm.roles.split(/[,，\s]+/).filter(Boolean),
        target_cities: presetForm.cities.split(/[,，\s]+/).filter(Boolean),
        salary_min_k: presetForm.salaryMin ? Number(presetForm.salaryMin) : null,
        employment_types: presetForm.types ? [presetForm.types] : [],
        max_experience_years_required: presetForm.maxExp ? Number(presetForm.maxExp) : null,
        graduation_year: presetForm.gradYear ? Number(presetForm.gradYear) : null,
      })
      await api.post(`/presets/${p.id}/activate`)
      setPresetForm({ name: '', roles: '', cities: '', salaryMin: '', types: 'campus', maxExp: '', gradYear: '' })
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const resetFeedback = async () => {
    if (!activeId) return
    const r = await api.del<{ deleted: number }>(`/feedback?profile_id=${activeId}`)
    setMsg(`已清空 ${r.deleted} 条历史反馈（排序影响回到零）`)
  }

  if (!activeId) {
    return (
      <div>
        <h1>我的画像</h1>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>建立你的档案</h2>
          <label className="field">
            姓名
            <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          <label className="field">
            一句话介绍（如：2027届本科 · 设计×AI / 3年Java后端）
            <input type="text" value={newHeadline} onChange={(e) => setNewHeadline(e.target.value)} />
          </label>
          <button className="btn primary" onClick={createProfile} disabled={!newName.trim()}>
            创建画像
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <h1>我的画像</h1>
      <p className="page-sub">
        {view?.profile.display_name}
        {view?.profile.headline ? ` · ${view.profile.headline}` : ''} ·{' '}
        {profiles.length > 1 ? `${profiles.length} 个画像` : ''}
      </p>
      {err && <div className="error-box">{err}</div>}
      {msg && <div className="card" style={{ borderColor: 'var(--accent)' }}>{msg}</div>}

      <h2>事实证据（简历生成只引用这里的内容）</h2>
      <div className="card">
        <label className="field">
          添加一条事实（来自你的材料原文：证书/实习证明/项目事实/获奖…）
          <textarea
            value={evText}
            onChange={(e) => setEvText(e.target.value)}
            style={{ minHeight: 70 }}
            placeholder="例如：实习期间主导3场用户访谈，满意度提升12%"
          />
        </label>
        <div className="row">
          <button className="btn" onClick={addEvidence} disabled={!evText.trim()}>
            添加证据
          </button>
          <button className="btn primary" onClick={confirmAll}>
            确认全部待复核事实（{evidence.filter((e) => !e.user_confirmed).length}）
          </button>
        </div>
        {evidence.length === 0 ? (
          <div className="empty" style={{ marginTop: 12 }}>
            还没有证据。每条简历 bullet 都必须能指回这里的原文——这是「事实不可捏造」的根基。
          </div>
        ) : (
          <table className="data" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>原文</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((e) => (
                <tr key={e.id}>
                  <td>{e.original_text}</td>
                  <td>{e.user_confirmed ? <span className="tag green">已确认</span> : <span className="tag amber">待复核</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2>技能（同义词由你维护，匹配引擎据此识别 JD 写法）</h2>
      <div className="card">
        <div className="row">
          <input type="text" placeholder="技能名，如 Java" value={skillName} onChange={(e) => setSkillName(e.target.value)} style={{ flex: 1 }} />
          <input type="text" placeholder="同义词（逗号分隔），如 spring, jvm" value={skillAliases} onChange={(e) => setSkillAliases(e.target.value)} style={{ flex: 2 }} />
          <button className="btn" onClick={addSkill} disabled={!skillName.trim()}>
            添加
          </button>
        </div>
        {view?.skills.length ? (
          <p style={{ marginTop: 10 }}>
            {view.skills.map((s) => (
              <span className="tag" key={s.id}>
                {s.name}
                {s.aliases.length ? `（${s.aliases.join('/')}）` : ''}
              </span>
            ))}
          </p>
        ) : (
          <div className="empty" style={{ marginTop: 10 }}>还没有技能。</div>
        )}
      </div>

      <h2>求职偏好（匹配引擎的全部依据，随时可改）</h2>
      <div className="card">
        {presets.map((p) => (
          <div key={p.id} className="between" style={{ borderBottom: '1px solid var(--line)', padding: '8px 0' }}>
            <div>
              <b>{p.name}</b>{p.is_active && <span className="tag green" style={{ marginLeft: 8 }}>使用中</span>}
              <div style={{ fontSize: 13, color: 'var(--muted)' }}>
                {p.employment_types.join('/')} · {p.target_roles.join('、') || '角色不限'} ·{' '}
                {p.target_cities.join('、') || '城市不限'}
                {p.salary_min_k ? ` · 期望≥${p.salary_min_k}K` : ''}
                {p.graduation_year ? ` · ${p.graduation_year}届` : ''}
              </div>
            </div>
            {!p.is_active && (
              <button className="btn small" onClick={async () => {
                await api.post(`/presets/${p.id}/activate`); load()
              }}>
                启用
              </button>
            )}
          </div>
        ))}
        <div className="row" style={{ marginTop: 12 }}>
          <input type="text" placeholder="名称，如 2027秋招-产品" value={presetForm.name} onChange={(e) => setPresetForm({ ...presetForm, name: e.target.value })} style={{ flex: 1 }} />
          <select value={presetForm.types} onChange={(e) => setPresetForm({ ...presetForm, types: e.target.value })}>
            <option value="campus">校招</option>
            <option value="social">社招</option>
            <option value="internship">实习</option>
            <option value="">不限批次</option>
          </select>
          <input type="text" placeholder="目标角色（逗号分隔）" value={presetForm.roles} onChange={(e) => setPresetForm({ ...presetForm, roles: e.target.value })} style={{ flex: 2 }} />
          <input type="text" placeholder="目标城市" value={presetForm.cities} onChange={(e) => setPresetForm({ ...presetForm, cities: e.target.value })} />
          <input type="number" placeholder="期望下限K" value={presetForm.salaryMin} onChange={(e) => setPresetForm({ ...presetForm, salaryMin: e.target.value })} style={{ width: 100 }} />
          <input type="number" placeholder="经验容忍上限(年)" value={presetForm.maxExp} onChange={(e) => setPresetForm({ ...presetForm, maxExp: e.target.value })} style={{ width: 130 }} />
          <input type="number" placeholder="毕业年份" value={presetForm.gradYear} onChange={(e) => setPresetForm({ ...presetForm, gradYear: e.target.value })} style={{ width: 100 }} />
          <button className="btn primary" onClick={createPreset} disabled={!presetForm.name.trim()}>
            创建并启用
          </button>
        </div>
      </div>

      <h2>个性化反馈</h2>
      <div className="card">
        <p style={{ margin: 0, fontSize: 14 }}>
          你在岗位详情页的 👍/👎 等反馈会影响排序。全部记录可查、可一键重置。
        </p>
        <button className="btn danger" style={{ marginTop: 8 }} onClick={resetFeedback}>
          清空全部反馈
        </button>
      </div>
    </div>
  )
}
