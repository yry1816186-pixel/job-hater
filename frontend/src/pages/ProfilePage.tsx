import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import type { Evidence, Preset, ProfileView } from '../types'
import { useProfiles } from '../App'
import { ConfirmDialog, EmptyState, StepBadge, useToast } from '../components/ui'

/**
 * 我的画像：一切匹配与简历的原料。按「第1步/第2步/…」组织，
 * 每节开头一句「这步影响什么」——新用户不再需要猜。
 */

function SectionHead({ n, title, done, affects }: { n: number; title: string; done: boolean; affects: string }) {
  return (
    <div className="section-head">
      <StepBadge n={n} done={done} />
      <div>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <p className="hint" style={{ margin: '2px 0 0' }}>{affects}</p>
      </div>
    </div>
  )
}

export default function ProfilePage() {
  const { profiles, activeId, refresh } = useProfiles()
  const { toast } = useToast()
  const [params] = useSearchParams()
  const [view, setView] = useState<ProfileView | null>(null)
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [presets, setPresets] = useState<Preset[]>([])
  const [err, setErr] = useState('')
  const [showCreate, setShowCreate] = useState(params.get('new') === '1')
  const [newName, setNewName] = useState('')
  const [newHeadline, setNewHeadline] = useState('')
  const [newPhone, setNewPhone] = useState('')
  const [newEmail, setNewEmail] = useState('')
  const [contact, setContact] = useState({ phone: '', email: '', summary: '', name: '' })

  // 各表单受控态
  const [skillName, setSkillName] = useState('')
  const [skillAliases, setSkillAliases] = useState('')
  const [evText, setEvText] = useState('')
  const [presetForm, setPresetForm] = useState({
    name: '', roles: '', cities: '', salaryMin: '', types: '', maxExp: '', gradYear: '',
  })
  const [expForm, setExpForm] = useState({ employer: '', title: '', tags: '', desc: '' })
  const [eduForm, setEduForm] = useState({ school: '', degree: '', major: '' })
  const [prjForm, setPrjForm] = useState({ name: '', role: '', desc: '' })
  const [confirmReset, setConfirmReset] = useState(false)

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
        setContact({
          phone: v.profile.phone ?? '',
          email: v.profile.email ?? '',
          summary: v.profile.summary ?? '',
          name: v.profile.display_name,
        })
      })
      .catch((er) => setErr(er.message))
  }
  useEffect(load, [activeId])

  const guard = async (fn: () => Promise<void>) => {
    try {
      await fn()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const createProfile = () =>
    guard(async () => {
      if (!newName.trim()) return
      const p = await api.post<ProfileView['profile']>('/profiles', {
        display_name: newName.trim(),
        headline: newHeadline.trim() || null,
        phone: newPhone.trim() || null,
        email: newEmail.trim() || null,
      })
      setNewName('')
      setNewHeadline('')
      setNewPhone('')
      setNewEmail('')
      setShowCreate(false)
      await refresh()
      localStorage.setItem('jobhater-active-profile', p.id)
      toast('success', `画像「${p.display_name}」已创建`)
    })

  const saveContact = () =>
    guard(async () => {
      if (!activeId) return
      const changed: Record<string, string> = {}
      if (contact.phone.trim() !== (view?.profile.phone ?? '')) changed.phone = contact.phone.trim()
      if (contact.email.trim() !== (view?.profile.email ?? '')) changed.email = contact.email.trim()
      if (contact.summary.trim() !== (view?.profile.summary ?? '')) changed.summary = contact.summary.trim()
      if (contact.name.trim() !== view?.profile.display_name) changed.display_name = contact.name.trim()
      if (!Object.keys(changed).length) return
      await api.patch(`/profiles/${activeId}`, changed)
      if (changed.display_name) await refresh()
      toast('success', '已保存（写入简历头部与求职信落款）')
      load()
    })

  const addSkill = () =>
    guard(async () => {
      if (!skillName.trim() || !activeId) return
      await api.post(`/profiles/${activeId}/skills`, {
        name: skillName.trim(),
        aliases: skillAliases.split(/[,，\s]+/).filter(Boolean),
      })
      setSkillName('')
      setSkillAliases('')
      toast('success', '技能已添加（内置常见同义词组会自动生效）')
      load()
    })

  const addEvidence = () =>
    guard(async () => {
      if (!evText.trim() || !activeId) return
      await api.post(`/profiles/${activeId}/evidence`, {
        original_text: evText.trim(),
        source_kind: 'paste',
      })
      setEvText('')
      toast('success', '事实已录入，待你复核确认')
      load()
    })

  const confirmOne = (id: string) =>
    guard(async () => {
      if (!activeId) return
      await api.post(`/profiles/${activeId}/evidence/confirm`, { evidence_ids: [id] })
      load()
    })

  const confirmAll = () =>
    guard(async () => {
      const unconfirmed = evidence.filter((e) => !e.user_confirmed).map((e) => e.id)
      if (!unconfirmed.length || !activeId) return
      const r = await api.post<{ confirmed: number }>(`/profiles/${activeId}/evidence/confirm`, {
        evidence_ids: unconfirmed,
      })
      toast('success', `已确认 ${r.confirmed} 条事实`)
      load()
    })

  const addExperience = () =>
    guard(async () => {
      if (!activeId || !expForm.employer.trim() || !expForm.title.trim()) return
      await api.post(`/profiles/${activeId}/experiences`, {
        employer: expForm.employer.trim(),
        title: expForm.title.trim(),
        description: expForm.desc.trim() || null,
        tags: expForm.tags.split(/[,，\s]+/).filter(Boolean),
      })
      setExpForm({ employer: '', title: '', tags: '', desc: '' })
      toast('success', '经历已添加（会出现在主简历与匹配依据里）')
      load()
    })

  const addEducation = () =>
    guard(async () => {
      if (!activeId || !eduForm.school.trim()) return
      await api.post(`/profiles/${activeId}/educations`, {
        school: eduForm.school.trim(),
        degree: eduForm.degree.trim() || null,
        major: eduForm.major.trim() || null,
      })
      setEduForm({ school: '', degree: '', major: '' })
      toast('success', '学历已添加')
      load()
    })

  const addProject = () =>
    guard(async () => {
      if (!activeId || !prjForm.name.trim()) return
      await api.post(`/profiles/${activeId}/projects`, {
        name: prjForm.name.trim(),
        role: prjForm.role.trim() || null,
        description: prjForm.desc.trim() || null,
      })
      setPrjForm({ name: '', role: '', desc: '' })
      toast('success', '项目已添加（简历与匹配都会用到）')
      load()
    })

  const delChild = (kind: 'educations' | 'experiences' | 'projects', id: string, label: string) =>
    guard(async () => {
      if (!activeId) return
      await api.del(`/profiles/${activeId}/${kind}/${id}`)
      toast('info', `${label} 已删除`)
      load()
    })

  const createPreset = () =>
    guard(async () => {
      if (!activeId || !presetForm.name.trim()) return
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
      setPresetForm({ name: '', roles: '', cities: '', salaryMin: '', types: '', maxExp: '', gradYear: '' })
      toast('success', `偏好「${p.name}」已创建并启用，匹配将按它运行`)
      load()
    })

  const resetFeedback = async () => {
    setConfirmReset(false)
    if (!activeId) return
    try {
      const r = await api.del<{ deleted: number }>(`/feedback?profile_id=${activeId}`)
      toast('success', `已清空 ${r.deleted} 条历史反馈（排序影响回到零）`)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  // ---------- 无画像：建档 ----------
  if (!activeId) {
    return (
      <div>
        <h1>我的画像</h1>
        <p className="page-sub">画像是匹配和简历的原料库：技能、经历、事实证据、求职偏好。</p>
        <div className="card" style={{ maxWidth: 520, padding: 24 }}>
          <h2 style={{ marginTop: 0 }}>建立你的档案</h2>
          <p className="hint">只填姓名即可创建，其余随时补。</p>
          <label className="field">
            姓名
            <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="你的名字或昵称" />
          </label>
          <label className="field">
            一句话介绍（可选）
            <input type="text" value={newHeadline} onChange={(e) => setNewHeadline(e.target.value)} placeholder="如：2027届本科 · 设计×AI / 3年Java后端" />
          </label>
          <label className="field">
            手机（可选）
            <input type="tel" value={newPhone} onChange={(e) => setNewPhone(e.target.value)} placeholder="写入简历头部" />
          </label>
          <label className="field">
            邮箱（可选）
            <input type="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} placeholder="写入简历头部" />
          </label>
          <button className="btn primary" onClick={createProfile} disabled={!newName.trim()}>
            创建画像
          </button>
        </div>
      </div>
    )
  }

  const doneSkills = (view?.skills.length ?? 0) > 0
  const doneExp = (view?.experiences.length ?? 0) + (view?.projects.length ?? 0) > 0
  const doneEvidence = evidence.length > 0
  const donePreset = presets.length > 0

  return (
    <div>
      <h1>我的画像</h1>
      <p className="page-sub">
        {view?.profile.display_name}
        {view?.profile.headline ? ` · ${view.profile.headline}` : ''}
      </p>
      {err && <div className="error-box">{err}</div>}

      <div className="card" style={{ padding: 12, marginBottom: 16, fontSize: 14 }}>
        💡 手里有一份现成简历？<Link to="/welcome">上传文件自动建档 →</Link>
        <span className="hint">（PDF / DOCX / TXT / MD / JSON Resume，解析后逐项核对才入库）</span>
      </div>

      {profiles.length > 1 && !showCreate && (
        <p className="hint">切换画像用左侧下拉框；共 {profiles.length} 个画像。</p>
      )}
      {(profiles.length <= 1 || showCreate) && (
        <button className="btn" style={{ marginBottom: 16 }} onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? '收起新建' : '＋ 新建另一个画像（如同时准备技术岗和产品岗）'}
        </button>
      )}
      {showCreate && (
        <div className="card" style={{ maxWidth: 520, padding: 20, marginBottom: 20 }}>
          <label className="field">
            姓名
            <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          <label className="field">
            一句话介绍（可选）
            <input type="text" value={newHeadline} onChange={(e) => setNewHeadline(e.target.value)} />
          </label>
          <label className="field">
            手机（可选）
            <input type="tel" value={newPhone} onChange={(e) => setNewPhone(e.target.value)} />
          </label>
          <label className="field">
            邮箱（可选）
            <input type="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} />
          </label>
          <button className="btn primary" onClick={createProfile} disabled={!newName.trim()}>创建</button>
        </div>
      )}

      {/* 基本信息：姓名/联系方式/简介——简历 basics 的法定字段 */}
      <div className="card" style={{ padding: 20, marginBottom: 20 }}>
        <b>基本信息与联系方式</b>
        <p className="hint" style={{ margin: '4px 0 10px' }}>
          写入简历头部与求职信落款。数据只在本机，不参与匹配打分。
        </p>
        <div className="row">
          <input
            type="text"
            placeholder="姓名"
            value={contact.name}
            onChange={(e) => setContact((c) => ({ ...c, name: e.target.value }))}
            style={{ width: 160 }}
            aria-label="姓名"
          />
          <input
            type="tel"
            placeholder="手机号"
            value={contact.phone}
            onChange={(e) => setContact((c) => ({ ...c, phone: e.target.value }))}
            style={{ width: 180 }}
            aria-label="手机号"
          />
          <input
            type="email"
            placeholder="邮箱"
            value={contact.email}
            onChange={(e) => setContact((c) => ({ ...c, email: e.target.value }))}
            style={{ flex: 1, minWidth: 200 }}
            aria-label="邮箱"
          />
        </div>
        <textarea
          placeholder="个人简介（可选，2-3 句：方向 + 亮点 + 求职目标）"
          value={contact.summary}
          onChange={(e) => setContact((c) => ({ ...c, summary: e.target.value }))}
          style={{ marginTop: 8, minHeight: 56, width: '100%' }}
          aria-label="个人简介"
        />
        <div className="row" style={{ marginTop: 8 }}>
          <button
            className="btn"
            onClick={saveContact}
            disabled={
              !!activeId
              && contact.phone === (view?.profile.phone ?? '')
              && contact.email === (view?.profile.email ?? '')
              && contact.summary === (view?.profile.summary ?? '')
              && contact.name === view?.profile.display_name
            }
          >
            保存
          </button>
          {!view?.profile.phone && !view?.profile.email && (
            <span className="hint" style={{ color: 'var(--warn, #b8860b)' }}>
              ⚠ 还没有任何联系方式——导出的简历头部将是空的，投递前务必补上。
            </span>
          )}
        </div>
      </div>

      {/* 第 1 步：学历与经历（简历主体素材） */}
      <SectionHead
        n={1}
        title="学历与经历"
        done={doneExp || (view?.educations.length ?? 0) > 0}
        affects="简历的主体内容从这生成；经历标签也参与匹配打分"
      />
      <div className="card">
        {view && (view.educations.length > 0 || view.experiences.length > 0 || view.projects.length > 0) ? (
          <>
            {view.educations.map((e) => (
              <div key={e.id} className="list-row">
                <span style={{ flex: 1 }}>
                  🎓 <b>{e.school}</b> {e.degree ? `· ${e.degree}` : ''} {e.major ? `· ${e.major}` : ''}
                </span>
                <button
                  className="icon-btn"
                  aria-label={`删除学历 ${e.school}`}
                  title="删除（录错了点这里）"
                  onClick={() => delChild('educations', e.id, `学历 ${e.school}`)}
                >✕</button>
              </div>
            ))}
            {view.experiences.map((e) => (
              <div key={e.id} className="list-row">
                <span style={{ flex: 1 }}>
                  💼 <b>{e.title}</b> @ {e.employer}
                  {e.tags.length ? <span className="tag" style={{ marginLeft: 8 }}>{e.tags.join(' · ')}</span> : null}
                </span>
                <button
                  className="icon-btn"
                  aria-label={`删除经历 ${e.employer}`}
                  title="删除（录错了点这里）"
                  onClick={() => delChild('experiences', e.id, `经历 ${e.employer}`)}
                >✕</button>
              </div>
            ))}
            {view.projects.map((p) => (
              <div key={p.id} className="list-row">
                <span style={{ flex: 1 }}>
                  🛠 <b>{p.name}</b>{p.role ? ` · ${p.role}` : ''}
                </span>
                <button
                  className="icon-btn"
                  aria-label={`删除项目 ${p.name}`}
                  title="删除（录错了点这里）"
                  onClick={() => delChild('projects', p.id, `项目 ${p.name}`)}
                >✕</button>
              </div>
            ))}
          </>
        ) : (
          <EmptyState icon="📋" title="还没有学历与经历" hint="简历是空的——先补这里，再去生成简历" />
        )}
        <details style={{ marginTop: 12 }}>
          <summary className="btn">＋ 添加学历</summary>
          <div className="row" style={{ marginTop: 10 }}>
            <input type="text" placeholder="学校" value={eduForm.school} onChange={(e) => setEduForm({ ...eduForm, school: e.target.value })} />
            <select value={eduForm.degree} onChange={(e) => setEduForm({ ...eduForm, degree: e.target.value })} style={{ width: 120 }}>
              <option value="">学历</option>
              <option value="专科">专科</option>
              <option value="本科">本科</option>
              <option value="硕士">硕士</option>
              <option value="博士">博士</option>
            </select>
            <input type="text" placeholder="专业" value={eduForm.major} onChange={(e) => setEduForm({ ...eduForm, major: e.target.value })} />
            <button className="btn" onClick={addEducation} disabled={!eduForm.school.trim()}>添加</button>
          </div>
        </details>
        <details style={{ marginTop: 8 }}>
          <summary className="btn">＋ 添加工作/实习经历</summary>
          <div style={{ marginTop: 10 }}>
            <div className="row">
              <input type="text" placeholder="公司" value={expForm.employer} onChange={(e) => setExpForm({ ...expForm, employer: e.target.value })} style={{ flex: 1 }} />
              <input type="text" placeholder="职位" value={expForm.title} onChange={(e) => setExpForm({ ...expForm, title: e.target.value })} style={{ flex: 1 }} />
              <input type="text" placeholder="标签（逗号分隔，如 后端,高并发）" value={expForm.tags} onChange={(e) => setExpForm({ ...expForm, tags: e.target.value })} style={{ flex: 2 }} />
            </div>
            <textarea placeholder="做了什么（可选，写关键事实）" value={expForm.desc} onChange={(e) => setExpForm({ ...expForm, desc: e.target.value })} style={{ marginTop: 8, minHeight: 56, width: '100%' }} />
            <button className="btn" style={{ marginTop: 8 }} onClick={addExperience} disabled={!expForm.employer.trim() || !expForm.title.trim()}>添加</button>
          </div>
        </details>
        <details style={{ marginTop: 8 }}>
          <summary className="btn">＋ 添加项目</summary>
          <div style={{ marginTop: 10 }}>
            <div className="row">
              <input type="text" placeholder="项目名" value={prjForm.name} onChange={(e) => setPrjForm({ ...prjForm, name: e.target.value })} style={{ flex: 2 }} />
              <input type="text" placeholder="角色（可选，如 核心开发）" value={prjForm.role} onChange={(e) => setPrjForm({ ...prjForm, role: e.target.value })} style={{ flex: 1 }} />
            </div>
            <textarea placeholder="项目描述 / 技术栈（可选）" value={prjForm.desc} onChange={(e) => setPrjForm({ ...prjForm, desc: e.target.value })} style={{ marginTop: 8, minHeight: 56, width: '100%' }} />
            <button className="btn" style={{ marginTop: 8 }} onClick={addProject} disabled={!prjForm.name.trim()}>添加</button>
          </div>
        </details>
      </div>

      {/* 第 2 步：技能 */}
      <SectionHead n={2} title="技能" done={doneSkills} affects="匹配引擎按技能（含同义词）识别 JD；技能缺口也会提示你" />
      <div className="card">
        <div className="row">
          <input type="text" placeholder="技能名，如 Java" value={skillName} onChange={(e) => setSkillName(e.target.value)} style={{ flex: 1 }} />
          <input type="text" placeholder="别名（可选，逗号分隔），如 spring, jvm" value={skillAliases} onChange={(e) => setSkillAliases(e.target.value)} style={{ flex: 2 }} />
          <button className="btn" onClick={addSkill} disabled={!skillName.trim()}>添加</button>
        </div>
        {view?.skills.length ? (
          <p style={{ marginTop: 10 }}>
            {view.skills.map((s) => (
              <span className="tag" key={s.id} title="点击 × 删除">
                {s.name}
                {s.aliases.length ? `（${s.aliases.join('/')}）` : ''}
                <button
                  className="tag-x"
                  aria-label={`删除技能 ${s.name}`}
                  onClick={() =>
                    guard(async () => {
                      await api.del(`/profiles/${activeId}/skills/${s.id}`)
                      toast('info', `技能 ${s.name} 已删除`)
                      load()
                    })
                  }
                >×</button>
              </span>
            ))}
          </p>
        ) : (
          <EmptyState icon="🧩" title="还没有技能" hint="至少加 3-5 个核心技能，匹配分才有意义（常见同义词如 go/golang 系统会自动识别）" />
        )}
      </div>

      {/* 第 3 步：事实证据 */}
      <SectionHead n={3} title="事实证据" done={doneEvidence} affects="简历里的每条量化成果都必须能指回这里的原文——事实不可捏造的根基" />
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
          <button className="btn" onClick={addEvidence} disabled={!evText.trim()}>添加证据</button>
          <button className="btn primary" onClick={confirmAll} disabled={evidence.every((e) => e.user_confirmed)}>
            确认全部待复核（{evidence.filter((e) => !e.user_confirmed).length}）
          </button>
        </div>
        {evidence.length === 0 ? (
          <EmptyState icon="🔍" title="还没有证据" hint="之后生成简历时，写进简历的每个数字都能在这里找到出处" />
        ) : (
          <table className="data" style={{ marginTop: 12 }}>
            <thead>
              <tr><th>原文</th><th>状态</th><th /></tr>
            </thead>
            <tbody>
              {evidence.map((e) => (
                <tr key={e.id}>
                  <td>{e.original_text}</td>
                  <td>
                    {e.user_confirmed
                      ? <span className="tag green">已确认</span>
                      : <button className="btn small" onClick={() => confirmOne(e.id)}>确认</button>}
                  </td>
                  <td>
                    <button
                      className="icon-btn"
                      aria-label="删除证据"
                      title="删除（已引用它的简历会如实报缺失）"
                      onClick={() =>
                        guard(async () => {
                          await api.del(`/profiles/${activeId}/evidence/${e.id}`)
                          toast('info', '证据已删除')
                          load()
                        })
                      }
                    >✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 第 4 步：求职偏好 */}
      <SectionHead n={4} title="求职偏好" done={donePreset} affects="匹配引擎的全部依据：批次/城市/角色/薪资底线决定哪些岗位被推荐给你" />
      <div className="card">
        {presets.length === 0 && (
          <EmptyState
            icon="🎯"
            title="还没有求职偏好"
            hint="没有偏好，匹配无法运行（收件箱会提示你回来）"
          />
        )}
        {presets.map((p) => (
          <div key={p.id} className="between list-row" style={{ borderBottom: '1px solid var(--line)', padding: '8px 0' }}>
            <div>
              <b>{p.name}</b>{p.is_active && <span className="tag green" style={{ marginLeft: 8 }}>使用中</span>}
              <div style={{ fontSize: 13, color: 'var(--muted)' }}>
                {p.employment_types.length ? p.employment_types.join('/') : '不限批次'} ·{' '}
                {p.target_roles.join('、') || '角色不限'} ·{' '}
                {p.target_cities.join('、') || '城市不限'}
                {p.salary_min_k ? ` · 期望≥${p.salary_min_k}K` : ''}
                {p.graduation_year ? ` · ${p.graduation_year}届` : ''}
              </div>
            </div>
            <div className="row">
              {!p.is_active && (
                <button className="btn small" onClick={() => guard(async () => {
                  await api.post(`/presets/${p.id}/activate`)
                  toast('success', `已切换到偏好「${p.name}」`)
                  load()
                })}>启用</button>
              )}
              <button
                className="icon-btn"
                aria-label="删除偏好"
                onClick={() =>
                  guard(async () => {
                    await api.del(`/presets/${p.id}`)
                    toast('info', `偏好「${p.name}」已删除`)
                    load()
                  })
                }
              >✕</button>
            </div>
          </div>
        ))}
        <details style={{ marginTop: 12 }} open={presets.length === 0}>
          <summary className="btn primary">＋ 新建偏好</summary>
          <div style={{ marginTop: 10, maxWidth: 720 }}>
            <div className="row">
              <input type="text" placeholder="名称，如：2027秋招-后端" value={presetForm.name} onChange={(e) => setPresetForm({ ...presetForm, name: e.target.value })} style={{ flex: 1 }} />
              <select value={presetForm.types} onChange={(e) => setPresetForm({ ...presetForm, types: e.target.value })} title="默认不限批次，避免悄悄过滤">
                <option value="">不限批次</option>
                <option value="campus">只要校招</option>
                <option value="social">只要社招</option>
                <option value="internship">只要实习</option>
              </select>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <input type="text" placeholder="目标角色（逗号分隔），如 后端开发,Java工程师" value={presetForm.roles} onChange={(e) => setPresetForm({ ...presetForm, roles: e.target.value })} style={{ flex: 2 }} />
              <input type="text" placeholder="目标城市（逗号分隔）" value={presetForm.cities} onChange={(e) => setPresetForm({ ...presetForm, cities: e.target.value })} style={{ flex: 1 }} />
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <input type="number" placeholder="期望月薪下限K（可选）" value={presetForm.salaryMin} onChange={(e) => setPresetForm({ ...presetForm, salaryMin: e.target.value })} style={{ width: 170 }} />
              <input type="number" placeholder="经验要求上限年（社招岗筛选）" value={presetForm.maxExp} onChange={(e) => setPresetForm({ ...presetForm, maxExp: e.target.value })} style={{ width: 190 }} />
              <input type="number" placeholder="毕业年份（校招定向）" value={presetForm.gradYear} onChange={(e) => setPresetForm({ ...presetForm, gradYear: e.target.value })} style={{ width: 150 }} />
            </div>
            <p className="hint" style={{ marginTop: 8 }}>
              留空 = 不限制。批次选「只要校招」会硬性过滤掉社招岗——不确定就先不限。
            </p>
            <button className="btn primary" style={{ marginTop: 8 }} onClick={createPreset} disabled={!presetForm.name.trim()}>
              创建并启用
            </button>
          </div>
        </details>
      </div>

      {/* 第 5 步：个性化反馈 */}
      <SectionHead n={5} title="个性化反馈" done affects="你在岗位详情页的 👍/👎 会影响同一雇主岗位的排序" />
      <div className="card">
        <p style={{ margin: 0, fontSize: 14 }}>
          全部记录可查看、可一键重置；清空后排序影响回到零。
        </p>
        <button className="btn danger" style={{ marginTop: 8 }} onClick={() => setConfirmReset(true)}>
          清空全部反馈
        </button>
      </div>

      <ConfirmDialog
        open={confirmReset}
        title="清空全部反馈？"
        body="你在岗位上的所有 👍/👎 记录将被删除，匹配排序回到零起点。此操作不可撤销。"
        confirmText="清空"
        danger
        onConfirm={resetFeedback}
        onCancel={() => setConfirmReset(false)}
      />
    </div>
  )
}
