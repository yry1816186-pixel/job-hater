import { useEffect, useState } from 'react'
import { api, qs } from '../api'
import { useProfiles } from '../App'
import { EmptyState, useToast } from '../components/ui'

interface VersionInfo {
  id: string
  version: number
  note?: string | null
  created_at?: string | null
  checked: boolean
}

interface FactcheckReport {
  passed: boolean
  bullets_checked: number
  issues: { path: string; type: string; msg: string }[]
}

/** JSON Resume 结构的可编辑形态（与后端 sections 契约一致） */
interface WorkItem {
  name: string
  position: string
  startDate: string
  endDate: string
  summary: string
  highlights: string[]
  evidence_ids?: string[]
}
interface EduItem {
  school: string
  degree: string
  major: string
  startDate: string
  endDate: string
  score: string
}
interface ProjectItem {
  name: string
  role: string
  url: string
  description: string
}
interface SkillItem {
  name: string
  level: number
  keywords: string[]
}
interface AwardItem {
  title: string
  date: string
}
interface Sections {
  basics: { name: string; label: string; summary: string }
  work: WorkItem[]
  education: EduItem[]
  projects: ProjectItem[]
  skills: SkillItem[]
  awards: AwardItem[]
  [k: string]: unknown
}

interface FullVersion {
  id: string
  resume_id: string
  version: number
  sections: Sections
  factcheck_report: FactcheckReport | null
  note?: string | null
}

const PATH_HINTS: [RegExp, string][] = [
  [/^\/?basics\/summary/, '个人概要'],
  [/^\/?work/, '工作经历'],
  [/^\/?education/, '教育'],
  [/^\/?projects/, '项目'],
  [/^\/?skills/, '技能'],
  [/^\/?awards/, '获奖/事实'],
]

function humanPath(p: string): string {
  for (const [re, label] of PATH_HINTS) if (re.test(p)) return `${label}（${p.replace(/^\//, '')}）`
  return p
}

/** 简历工坊：结构化编辑（不再让你面对 JSON）+ 实时预览 + 事实校验守门 */
export default function ResumePage() {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [resumes, setResumes] = useState<{ id: string; name: string; kind: string; status: string; current_version: number }[]>([])
  const [versions, setVersions] = useState<VersionInfo[]>([])
  const [current, setCurrent] = useState<FullVersion | null>(null)
  const [sections, setSections] = useState<Sections | null>(null)
  const [jsonMode, setJsonMode] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [generating, setGenerating] = useState(false)
  const [err, setErr] = useState('')

  const loadResumes = () => {
    if (!activeId) return
    api
      .get<{ id: string; name: string; kind: string; status: string; current_version: number }[]>(
        `/resumes${qs({ profile_id: activeId })}`,
      )
      .then(setResumes)
      .catch(() => setResumes([]))
  }
  useEffect(loadResumes, [activeId])

  const openResume = async (rid: string) => {
    setErr('')
    try {
      const vs = await api.get<VersionInfo[]>(`/resumes/${rid}/versions`)
      setVersions(vs)
      if (vs.length) openVersion(vs[0].id)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const openVersion = async (vid: string) => {
    try {
      const v = await api.get<FullVersion>(`/resume-versions/${vid}`)
      setCurrent(v)
      setSections(v.sections)
      setJsonText(JSON.stringify(v.sections, null, 2))
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const generate = async () => {
    if (!activeId) return
    setGenerating(true)
    setErr('')
    try {
      const r = await api.post<{ resume_id: string; version_id: string; factcheck: FactcheckReport }>(
        '/resumes/generate',
        { profile_id: activeId },
      )
      toast(
        'success',
        `主简历已生成。事实校验：${r.factcheck.passed ? '通过' : `${r.factcheck.issues.length} 个问题`}（${r.factcheck.bullets_checked} 条已查）`,
      )
      loadResumes()
      openResume(r.resume_id)
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  const commit = async (next: Sections, note: string) => {
    if (!current) return
    try {
      const r = await api.post<{ version_id: string; factcheck: FactcheckReport }>(
        `/resumes/${current.resume_id}/versions`,
        { sections: next, note },
      )
      toast('success', `已保存为新版本。事实校验：${r.factcheck.passed ? '通过 ✓' : `${r.factcheck.issues.length} 个问题`}`)
      openResume(current.resume_id)
      openVersion(r.version_id)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const commitJson = () => {
    try {
      const parsed = JSON.parse(jsonText) as Sections
      commit(parsed, '高级 JSON 编辑')
    } catch (e) {
      toast('error', `JSON 格式有误：${(e as Error).message}`)
    }
  }

  const runFactcheck = async () => {
    if (!current) return
    try {
      const r = await api.post<FactcheckReport>(`/resume-versions/${current.id}/factcheck`, {})
      setCurrent({ ...current, factcheck_report: r })
      toast(r.passed ? 'success' : 'error', r.passed ? '事实校验通过' : `${r.issues.length} 个问题需要处理`)
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const markFinal = async () => {
    if (!current) return
    try {
      await api.post(`/resume-versions/${current.id}/final`, {})
      toast('success', '已标记终稿（只有通过事实校验的版本可以）')
      loadResumes()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  // 结构化编辑的小工具
  const setBasics = (k: 'name' | 'label' | 'summary', v: string) =>
    setSections((s) => (s ? { ...s, basics: { ...s.basics, [k]: v } } : s))
  const updateWork = (i: number, patch: Partial<WorkItem>) =>
    setSections((s) => (s ? { ...s, work: s.work.map((w, k) => (k === i ? { ...w, ...patch } : w)) } : s))
  const updateHighlight = (wi: number, hi: number, v: string) =>
    setSections((s) =>
      s ? { ...s, work: s.work.map((w, k) => (k === wi ? { ...w, highlights: w.highlights.map((h, j) => (j === hi ? v : h)) } : w)) } : s,
    )
  const addHighlight = (wi: number) =>
    setSections((s) =>
      s ? { ...s, work: s.work.map((w, k) => (k === wi ? { ...w, highlights: [...w.highlights, ''] } : w)) } : s,
    )
  const delHighlight = (wi: number, hi: number) =>
    setSections((s) =>
      s ? { ...s, work: s.work.map((w, k) => (k === wi ? { ...w, highlights: w.highlights.filter((_, j) => j !== hi) } : w)) } : s,
    )
  const updateEdu = (i: number, patch: Partial<EduItem>) =>
    setSections((s) => (s ? { ...s, education: s.education.map((w, k) => (k === i ? { ...w, ...patch } : w)) } : s))
  const updateProject = (i: number, patch: Partial<ProjectItem>) =>
    setSections((s) => (s ? { ...s, projects: s.projects.map((w, k) => (k === i ? { ...w, ...patch } : w)) } : s))

  const fc = current?.factcheck_report
  const dirty = current && sections && JSON.stringify(sections) !== JSON.stringify(current.sections)

  return (
    <div>
      <h1>简历</h1>
      <p className="page-sub">
        从画像生成主简历 → 表单化编辑 → 事实校验通过才能定稿。每条量化成果都要能指回你画像里的证据。
      </p>
      {err && <div className="error-box">{err}</div>}

      <div className="row">
        <button className="btn primary" onClick={generate} disabled={!activeId || generating}>
          {generating ? '生成中…' : resumes.length ? '重新从画像生成' : '从画像生成主简历'}
        </button>
        {resumes.length === 0 && !generating && (
          <span className="hint">画像里先有学历/经历/技能，生成才有内容</span>
        )}
      </div>

      {resumes.length === 0 ? (
        <EmptyState
          icon="📄"
          title="还没有简历"
          hint="点上面的按钮，系统会用你画像里的学历、经历、技能生成第一版主简历。"
        />
      ) : (
        <>
          <h2>简历库</h2>
          {resumes.map((r) => (
            <div className="card between" key={r.id}>
              <div>
                <b>{r.name}</b>
                {r.status === 'final'
                  ? <span className="tag green" style={{ marginLeft: 8 }}>终稿</span>
                  : <span className="tag" style={{ marginLeft: 8 }}>草稿</span>}
                <div style={{ fontSize: 13, color: 'var(--muted)' }}>
                  当前 v{r.current_version} · {r.kind === 'master' ? '主简历' : '岗位定制'}
                </div>
              </div>
              <button className="btn" onClick={() => openResume(r.id)}>打开</button>
            </div>
          ))}
        </>
      )}

      {versions.length > 0 && (
        <>
          <h2>版本历史（不可变，修改=新版本）</h2>
          <div className="card row" style={{ flexWrap: 'wrap' }}>
            {versions.map((v) => (
              <button
                key={v.id}
                className={`btn small${current?.id === v.id ? ' primary' : ''}`}
                onClick={() => openVersion(v.id)}
                title={v.note ?? ''}
              >
                v{v.version}
                {v.checked ? ' ✓' : ''}
              </button>
            ))}
          </div>
        </>
      )}

      {current && sections && (
        <>
          <div className="between">
            <h2>编辑 v{current.version}{dirty ? '（有未保存修改）' : ''}</h2>
            <label className="hint">
              <input type="checkbox" checked={jsonMode} onChange={(e) => setJsonMode(e.target.checked)} />
              高级模式（JSON）
            </label>
          </div>

          <div className="resume-editor-grid">
            {/* 编辑区 */}
            <div className="card">
              {jsonMode ? (
                <>
                  <p className="hint">直接改 JSON Resume 结构（进阶用户）。保存会校验格式。</p>
                  <textarea
                    value={jsonText}
                    onChange={(e) => setJsonText(e.target.value)}
                    style={{ minHeight: 320, fontFamily: 'Consolas, monospace', fontSize: 13, width: '100%' }}
                    aria-label="简历 JSON 编辑器"
                  />
                  <button className="btn primary" style={{ marginTop: 10 }} onClick={commitJson}>
                    保存 JSON 为新版本
                  </button>
                </>
              ) : (
                <>
                  <h3>基本信息</h3>
                  <label className="field">姓名
                    <input value={sections.basics?.name ?? ''} onChange={(e) => setBasics('name', e.target.value)} />
                  </label>
                  <label className="field">一句话标签
                    <input value={sections.basics?.label ?? ''} onChange={(e) => setBasics('label', e.target.value)} placeholder="如：2027届本科 · 后端开发" />
                  </label>
                  <label className="field">个人概要
                    <textarea style={{ minHeight: 60 }} value={sections.basics?.summary ?? ''} onChange={(e) => setBasics('summary', e.target.value)} />
                  </label>

                  <h3>工作 / 实习经历</h3>
                  {sections.work?.length === 0 && <p className="hint">（画像里还没有经历，去画像页补充）</p>}
                  {sections.work?.map((w, i) => (
                    <div className="subcard" key={i}>
                      <div className="row">
                        <input placeholder="公司" value={w.name} onChange={(e) => updateWork(i, { name: e.target.value })} style={{ flex: 1 }} />
                        <input placeholder="职位" value={w.position} onChange={(e) => updateWork(i, { position: e.target.value })} style={{ flex: 1 }} />
                      </div>
                      <div className="row" style={{ marginTop: 6 }}>
                        <input placeholder="开始时间" value={w.startDate} onChange={(e) => updateWork(i, { startDate: e.target.value })} style={{ width: 110 }} />
                        <input placeholder="结束时间" value={w.endDate} onChange={(e) => updateWork(i, { endDate: e.target.value })} style={{ width: 110 }} />
                      </div>
                      <label className="field" style={{ marginTop: 6 }}>概述
                        <textarea style={{ minHeight: 44 }} value={w.summary} onChange={(e) => updateWork(i, { summary: e.target.value })} />
                      </label>
                      <b style={{ fontSize: 13 }}>成果要点（每条都要有事实依据）</b>
                      {w.highlights.map((h, hi) => (
                        <div className="row" key={hi} style={{ marginTop: 4 }}>
                          <input
                            value={h}
                            onChange={(e) => updateHighlight(i, hi, e.target.value)}
                            placeholder="如：主导XX模块重构，接口延迟降低40%"
                            style={{ flex: 1 }}
                          />
                          <button className="icon-btn" aria-label="删除此条" onClick={() => delHighlight(i, hi)}>✕</button>
                        </div>
                      ))}
                      <button className="btn small" style={{ marginTop: 6 }} onClick={() => addHighlight(i)}>＋ 要点</button>
                    </div>
                  ))}

                  <h3>教育</h3>
                  {sections.education?.map((e, i) => (
                    <div className="subcard" key={i}>
                      <div className="row">
                        <input placeholder="学校" value={e.school} onChange={(ev) => updateEdu(i, { school: ev.target.value })} style={{ flex: 2 }} />
                        <input placeholder="学历" value={e.degree} onChange={(ev) => updateEdu(i, { degree: ev.target.value })} style={{ width: 90 }} />
                        <input placeholder="专业" value={e.major} onChange={(ev) => updateEdu(i, { major: ev.target.value })} style={{ flex: 1 }} />
                      </div>
                    </div>
                  ))}

                  <h3>项目</h3>
                  {sections.projects?.map((p, i) => (
                    <div className="subcard" key={i}>
                      <div className="row">
                        <input placeholder="项目名" value={p.name} onChange={(e) => updateProject(i, { name: e.target.value })} style={{ flex: 1 }} />
                        <input placeholder="角色" value={p.role} onChange={(e) => updateProject(i, { role: e.target.value })} style={{ width: 130 }} />
                      </div>
                      <textarea
                        placeholder="描述" style={{ marginTop: 6, minHeight: 44, width: '100%' }}
                        value={p.description} onChange={(e) => updateProject(i, { description: e.target.value })}
                      />
                    </div>
                  ))}

                  <h3>技能</h3>
                  <p className="tags">
                    {sections.skills?.map((s, i) => (
                      <span className="tag" key={i}>{s.name}{s.keywords?.length ? `（${s.keywords.join('/')}）` : ''}</span>
                    ))}
                  </p>
                  <p className="hint">技能与别名在「我的画像」维护，这里只读。</p>

                  <div className="row" style={{ marginTop: 14 }}>
                    <button className="btn primary" onClick={() => commit(sections, '表单编辑')} disabled={!dirty}>
                      {dirty ? '保存为新版本（自动跑事实校验）' : '无修改'}
                    </button>
                    <button className="btn" onClick={runFactcheck}>重新校验</button>
                    <button className="btn" onClick={markFinal}>标记终稿</button>
                  </div>
                </>
              )}
            </div>

            {/* 实时预览 */}
            <div className="resume-preview card" aria-label="简历预览">
              <h3 style={{ marginTop: 0 }}>{sections.basics?.name || '（姓名）'}</h3>
              <p style={{ color: 'var(--muted)', margin: '2px 0 8px' }}>{sections.basics?.label}</p>
              {sections.basics?.summary && <p style={{ fontSize: 13.5 }}>{sections.basics.summary}</p>}
              {sections.work?.length > 0 && (
                <>
                  <h4>经历</h4>
                  {sections.work.map((w, i) => (
                    <div key={i} style={{ marginBottom: 10 }}>
                      <b>{w.position}</b> · {w.name}
                      <span style={{ color: 'var(--muted)', fontSize: 12 }}> {w.startDate}{w.endDate ? `–${w.endDate}` : ''}</span>
                      {w.summary && <p style={{ fontSize: 13, margin: '2px 0' }}>{w.summary}</p>}
                      <ul style={{ margin: '2px 0 0', paddingLeft: 18, fontSize: 13 }}>
                        {w.highlights.filter(Boolean).map((h, j) => <li key={j}>{h}</li>)}
                      </ul>
                    </div>
                  ))}
                </>
              )}
              {sections.education?.length > 0 && (
                <>
                  <h4>教育</h4>
                  {sections.education.map((e, i) => (
                    <p key={i} style={{ fontSize: 13, margin: '2px 0' }}>
                      <b>{e.school}</b> · {e.degree} {e.major}
                    </p>
                  ))}
                </>
              )}
              {sections.projects?.length > 0 && (
                <>
                  <h4>项目</h4>
                  {sections.projects.map((p, i) => (
                    <p key={i} style={{ fontSize: 13, margin: '2px 0' }}>
                      <b>{p.name}</b>{p.role ? ` · ${p.role}` : ''}{p.description ? `：${p.description}` : ''}
                    </p>
                  ))}
                </>
              )}
              {sections.skills?.length > 0 && (
                <>
                  <h4>技能</h4>
                  <p style={{ fontSize: 13 }}>{sections.skills.map((s) => s.name).join(' · ')}</p>
                </>
              )}
            </div>
          </div>

          {fc && (
            <div className={`card ${fc.passed ? '' : 'error-box'}`} style={{ marginTop: 14 }}>
              <b>
                事实校验{fc.passed ? '通过' : '未通过'}（{fc.bullets_checked} 条成果要点）
              </b>
              {fc.issues.length > 0 && (
                <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                  {fc.issues.map((i, k) => (
                    <li key={k}>[{i.type}] {humanPath(i.path)}：{i.msg}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <h2>导出</h2>
          <div className="row">
            {(['md', 'html', 'json', 'pdf', 'docx'] as const).map((fmt) => (
              <a className="btn" key={fmt} href={`/api/resume-versions/${current.id}/export${qs({ fmt })}`} target="_blank" rel="noreferrer">
                {fmt.toUpperCase()}
              </a>
            ))}
          </div>
          <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>
            PDF 需要 playwright（可选依赖）、DOCX 需要 python-docx；未安装时后端给出降级指引（HTML 可直接浏览器打印为 PDF）。
          </p>
        </>
      )}
    </div>
  )
}
