import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { DemoSeedResult, ParseResumeResult, ResumeDraft } from '../types'
import { useProfiles } from '../App'
import { useToast } from '../components/ui'

/**
 * 新手向导：新用户从零到第一次匹配的三步。
 * 1. 上传简历（pdf/docx/txt/md/json）或粘贴文本，或手动填写；
 * 2. 核对解析草稿——自动解析可能不准，逐项确认/修改后才落库（无免检特权）；
 * 3. 定求职方向（目标角色/城市/薪资底线），跳过则建通用偏好保证匹配可运行。
 */

const EMPTY_DRAFT: ResumeDraft = {
  basics: { name: '', label: '', summary: '', email: '', phone: '' },
  work: [], education: [], projects: [], skills: [], awards: [],
}

const DEMO_RESUME_HINT = `支持 PDF / DOCX / TXT / MD / JSON Resume。扫描件（图片型 PDF）无法读取——可把内容粘贴成文本。`

export default function OnboardingPage() {
  const navigate = useNavigate()
  const { refresh } = useProfiles()
  const { toast } = useToast()

  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)

  // 步骤 1：输入
  const fileRef = useRef<HTMLInputElement>(null)
  const [pasteMode, setPasteMode] = useState(false)
  const [pasteText, setPasteText] = useState('')
  const [aiAvailable, setAiAvailable] = useState(false)
  const [aiDisclosure, setAiDisclosure] = useState('')
  const [aiAsking, setAiAsking] = useState(false)

  // 步骤 2：核对草稿
  const [draft, setDraft] = useState<ResumeDraft>(EMPTY_DRAFT)
  const [warnings, setWarnings] = useState<string[]>([])
  const [skillsText, setSkillsText] = useState('')

  // 步骤 3：求职方向
  const [dir, setDir] = useState({ roles: '', cities: '', salaryMin: '', types: '', gradYear: '' })
  const [seedDemo, setSeedDemo] = useState(true)

  // ---------- 步骤 1：解析 ----------

  const applyResult = (r: ParseResumeResult) => {
    setDraft(r.draft)
    setWarnings(r.warnings)
    setSkillsText(r.draft.skills.map((s) => s.name).join('、'))
    setAiAvailable(!!r.ai_available)
    setStep(2)
  }

  const parseFile = async (file: File) => {
    setErr('')
    setBusy(true)
    try {
      applyResult(await api.upload<ParseResumeResult>('/onboarding/parse-resume', file))
      toast('success', '解析完成——请逐项核对（自动解析可能不准）')
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const parsePaste = async () => {
    setErr('')
    setBusy(true)
    try {
      applyResult(await api.post<ParseResumeResult>('/onboarding/parse-resume-text', { text: pasteText }))
      toast('success', '解析完成——请逐项核对（自动解析可能不准）')
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  /** AI 精解析（opt-in）：第一次不带 ack → 428 + 披露 → 用户确认后带 ack 重试 */
  const aiParse = async () => {
    setErr('')
    setBusy(true)
    try {
      const text = pasteMode ? pasteText : draftToText(draft)
      const r = await api.post<{ executed: boolean; draft?: ResumeDraft; reason?: string }>(
        '/onboarding/ai-parse', { text, ack_egress: false },
      )
      if (!r.executed) {
        toast('info', '当前是本地模式（未启用 AI Provider），已保留启发式解析结果')
        return
      }
      if (r.draft) applyResult({ draft: r.draft, warnings: [], source_kind: 'ai' })
      toast('success', 'AI 精解析完成——仍请核对后保存')
    } catch (e) {
      const status = (e as { status?: number }).status
      if (status === 428) {
        try {
          const detail = JSON.parse((e as Error).message) as { disclosure?: string }
          setAiDisclosure(detail.disclosure ?? '将发送简历全文到你启用的 AI Provider。')
        } catch {
          setAiDisclosure('将发送简历全文到你启用的 AI Provider。')
        }
        setAiAsking(true)
        return
      }
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const aiParseAcked = async () => {
    setAiAsking(false)
    setBusy(true)
    try {
      const text = pasteMode ? pasteText : draftToText(draft)
      const r = await api.post<{ executed: boolean; draft?: ResumeDraft; reason?: string }>(
        '/onboarding/ai-parse', { text, ack_egress: true },
      )
      if (r.executed && r.draft) {
        applyResult({ draft: r.draft, warnings: [], source_kind: 'ai' })
        toast('success', 'AI 精解析完成——仍请核对后保存')
      } else {
        toast('info', 'AI 未执行（本地模式），已保留启发式结果')
      }
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // 手动建档：只需要姓名，其余在画像页随时补
  const [manualName, setManualName] = useState('')
  const [manualHeadline, setManualHeadline] = useState('')
  const createManual = async () => {
    setErr('')
    setBusy(true)
    try {
      const p = await api.post<{ id: string }>('/profiles', {
        display_name: manualName.trim(),
        headline: manualHeadline.trim() || null,
      })
      localStorage.setItem('jobhater-active-profile', p.id)
      await refresh()
      setDraft(EMPTY_DRAFT)
      setStep(3)
      toast('success', '画像已创建——方向设置完成后可回画像页补学历/技能')
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // ---------- 步骤 2：核对与保存 ----------

  const patchBasics = (k: keyof ResumeDraft['basics'], v: string) =>
    setDraft((d) => ({ ...d, basics: { ...d.basics, [k]: v } }))
  const patchEdu = (i: number, k: keyof ResumeDraft['education'][number], v: string) =>
    setDraft((d) => ({ ...d, education: d.education.map((e, j) => (j === i ? { ...e, [k]: v } : e)) }))
  const patchWork = (i: number, k: keyof ResumeDraft['work'][number], v: string) =>
    setDraft((d) => ({ ...d, work: d.work.map((w, j) => (j === i ? { ...w, [k]: v } : w)) }))
  const patchProj = (i: number, k: keyof ResumeDraft['projects'][number], v: string) =>
    setDraft((d) => ({ ...d, projects: d.projects.map((p, j) => (j === i ? { ...p, [k]: v } : p)) }))

  const saveDraft = async () => {
    if (!draft.basics.name.trim()) {
      setErr('姓名必填——这是画像的唯一必填项')
      return
    }
    setErr('')
    setBusy(true)
    try {
      const skills = skillsText.split(/[,，、;；\s]+/).filter(Boolean)
      const b = draft.basics
      const profile = await api.post<{ id: string; display_name: string }>('/profiles', {
        display_name: b.name.trim(),
        headline: b.label.trim() || null,
        phone: b.phone.trim() || null,
        email: b.email.trim() || null,
      })
      if (b.summary.trim()) {
        await api.patch(`/profiles/${profile.id}`, { summary: b.summary.trim() })
      }
      const counts = await api.post<Record<string, number>>(
        `/profiles/${profile.id}/import/json-resume`,
        { ...draft, skills: skills.map((s) => ({ name: s })) },
      )
      localStorage.setItem('jobhater-active-profile', profile.id)
      await refresh()
      toast('success', `画像已建立：技能 ${counts.skills ?? 0}、经历 ${counts.experiences ?? 0}、学历 ${counts.educations ?? 0}、项目 ${counts.projects ?? 0}`)
      setStep(3)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // ---------- 步骤 3：方向 ----------

  const finish = async (skip: boolean) => {
    setErr('')
    setBusy(true)
    try {
      const profileId = localStorage.getItem('jobhater-active-profile')
      if (profileId) {
        if (skip) {
          const p = await api.post<{ id: string }>(`/profiles/${profileId}/presets`, {
            name: '通用偏好',
            employment_types: [], target_roles: [], target_cities: [],
          })
          await api.post(`/presets/${p.id}/activate`)
          toast('info', '已建通用偏好（全部不限）——随时可到画像页细化')
        } else {
          const p = await api.post<{ id: string }>(`/profiles/${profileId}/presets`, {
            name: '我的方向',
            target_roles: dir.roles.split(/[,，、\s]+/).filter(Boolean),
            target_cities: dir.cities.split(/[,，、\s]+/).filter(Boolean),
            salary_min_k: dir.salaryMin ? Number(dir.salaryMin) : null,
            employment_types: dir.types ? [dir.types] : [],
            graduation_year: dir.gradYear ? Number(dir.gradYear) : null,
          })
          await api.post(`/presets/${p.id}/activate`)
          toast('success', '求职方向已保存并启用——匹配将按它运行')
        }
      }
      if (seedDemo) {
        const r = await api.post<DemoSeedResult>('/demo/seed')
        toast('info', `已导入 ${r.added} 条示例岗位（重复 ${r.deduped}）`)
      }
      await api.put('/settings/onboarding_done', { value: true })
      navigate('/')
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // ---------- 渲染 ----------

  return (
    <div className="wizard">
      <h1 style={{ marginBottom: 4 }}>欢迎来到 Job Hater</h1>
      <p className="page-sub" style={{ marginTop: 0 }}>
        本地求职全流程管理，数据只在这台电脑上。三步开始：简历建档 → 核对信息 → 定方向。
      </p>

      <ol className="wizard-steps">
        {['上传/输入简历', '核对信息', '定求职方向'].map((label, i) => (
          <li key={label} className={step === i + 1 ? 'now' : step > i + 1 ? 'done' : ''}>
            <span className="dot">{step > i + 1 ? '✓' : i + 1}</span> {label}
          </li>
        ))}
      </ol>

      {err && <div className="error-box">{err}</div>}

      {step === 1 && (
        <div className="card" style={{ padding: 24 }}>
          {!pasteMode ? (
            <>
              <div
                className={`dropzone${dragOver ? ' over' : ''}`}
                role="button"
                tabIndex={0}
                aria-label="上传简历文件"
                onClick={() => fileRef.current?.click()}
                onKeyDown={(e) => e.key === 'Enter' && fileRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragOver(false)
                  const f = e.dataTransfer.files?.[0]
                  if (f) parseFile(f)
                }}
              >
                <div className="dropzone-icon" aria-hidden>📄</div>
                <b>{busy ? '解析中…' : '把简历文件拖到这里，或点击选择'}</b>
                <p className="hint">{DEMO_RESUME_HINT}</p>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".pdf,.docx,.txt,.md,.markdown,.json"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) parseFile(f)
                  e.target.value = ''
                }}
              />
              <p className="hint" style={{ textAlign: 'center', margin: '10px 0' }}>
                <a onClick={() => setPasteMode(true)} style={{ cursor: 'pointer' }}>
                  没有文件？粘贴简历文本 →
                </a>
              </p>
            </>
          ) : (
            <>
              <label className="field">
                简历文本（把简历全文复制进来，格式不限）
                <textarea
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                  style={{ minHeight: 180 }}
                  placeholder={'姓名\n邮箱/电话\n教育背景\n……'}
                />
              </label>
              <div className="row">
                <button className="btn primary" onClick={parsePaste} disabled={!pasteText.trim() || busy}>
                  {busy ? '解析中…' : '解析简历文本'}
                </button>
                <a onClick={() => setPasteMode(false)} style={{ cursor: 'pointer', alignSelf: 'center' }}>
                  ← 改用文件上传
                </a>
              </div>
            </>
          )}

          <details style={{ marginTop: 18 }}>
            <summary className="btn">手动填写（不解析文件）</summary>
            <div style={{ maxWidth: 460, marginTop: 10 }}>
              <label className="field">
                姓名（唯一必填）
                <input type="text" value={manualName} onChange={(e) => setManualName(e.target.value)} />
              </label>
              <label className="field">
                一句话介绍（可选）
                <input type="text" value={manualHeadline} onChange={(e) => setManualHeadline(e.target.value)} placeholder="如：2027届本科 · 后端开发" />
              </label>
              <button className="btn primary" onClick={createManual} disabled={!manualName.trim() || busy}>
                创建画像，直接去定方向 →
              </button>
            </div>
          </details>
        </div>
      )}

      {step === 2 && (
        <div className="card" style={{ padding: 22 }}>
          {warnings.length > 0 && (
            <div className="warn-box">
              <b>解析器提示（{warnings.length}）：</b>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </div>
          )}
          <p className="hint" style={{ marginTop: 10 }}>
            自动解析可能不准确——<b>逐项核对、改完再保存</b>。识别不到的留空即可，之后随时在「我的画像」补充。
          </p>

          <h3 style={{ marginBottom: 6 }}>基本信息</h3>
          <div className="row">
            <label className="field">姓名（必填）
              <input type="text" value={draft.basics.name} onChange={(e) => patchBasics('name', e.target.value)} />
            </label>
            <label className="field">邮箱
              <input type="email" value={draft.basics.email} onChange={(e) => patchBasics('email', e.target.value)} />
            </label>
            <label className="field" style={{ width: 160 }}>手机
              <input type="tel" value={draft.basics.phone} onChange={(e) => patchBasics('phone', e.target.value)} />
            </label>
          </div>
          <div className="row">
            <label className="field" style={{ flex: 1 }}>求职意向 / 一句话头衔
              <input type="text" value={draft.basics.label} onChange={(e) => patchBasics('label', e.target.value)} placeholder="如：后端开发工程师" />
            </label>
          </div>
          <label className="field">个人简介（可选）
            <textarea value={draft.basics.summary} onChange={(e) => patchBasics('summary', e.target.value)} style={{ minHeight: 54 }} />
          </label>

          <h3>教育背景（{draft.education.length}）</h3>
          {draft.education.map((e, i) => (
            <div className="draft-row" key={i}>
              <input type="text" value={e.institution} placeholder="学校" onChange={(ev) => patchEdu(i, 'institution', ev.target.value)} style={{ flex: 2 }} />
              <select value={e.studyType} onChange={(ev) => patchEdu(i, 'studyType', ev.target.value)} style={{ width: 96 }}>
                <option value="">学历</option>
                <option value="专科">专科</option>
                <option value="本科">本科</option>
                <option value="硕士">硕士</option>
                <option value="博士">博士</option>
              </select>
              <input type="text" value={e.area} placeholder="专业" onChange={(ev) => patchEdu(i, 'area', ev.target.value)} style={{ flex: 1.5 }} />
              <input type="text" value={e.startDate} placeholder="2023-09" onChange={(ev) => patchEdu(i, 'startDate', ev.target.value)} style={{ width: 92 }} />
              <input type="text" value={e.endDate} placeholder="2027-06" onChange={(ev) => patchEdu(i, 'endDate', ev.target.value)} style={{ width: 92 }} />
              <button className="icon-btn" aria-label="删除这条学历" onClick={() => setDraft((d) => ({ ...d, education: d.education.filter((_, j) => j !== i) }))}>✕</button>
            </div>
          ))}
          <button className="btn small" onClick={() => setDraft((d) => ({ ...d, education: [...d.education, { institution: '', studyType: '', area: '', startDate: '', endDate: '' }] }))}>
            ＋ 添加学历
          </button>

          <h3>工作 / 实习经历（{draft.work.length}）</h3>
          {draft.work.map((w, i) => (
            <div className="draft-block" key={i}>
              <div className="row">
                <input type="text" value={w.name} placeholder="公司" onChange={(ev) => patchWork(i, 'name', ev.target.value)} style={{ flex: 1.5 }} />
                <input type="text" value={w.position} placeholder="职位" onChange={(ev) => patchWork(i, 'position', ev.target.value)} style={{ flex: 1.5 }} />
                <input type="text" value={w.startDate} placeholder="2025-06" onChange={(ev) => patchWork(i, 'startDate', ev.target.value)} style={{ width: 88 }} />
                <input type="text" value={w.endDate} placeholder="2025-09（在任留空）" onChange={(ev) => patchWork(i, 'endDate', ev.target.value)} style={{ width: 130 }} />
                <button className="icon-btn" aria-label="删除这段经历" onClick={() => setDraft((d) => ({ ...d, work: d.work.filter((_, j) => j !== i) }))}>✕</button>
              </div>
              <textarea value={w.summary} placeholder="做了什么（关键事实与数字）" onChange={(ev) => patchWork(i, 'summary', ev.target.value)} style={{ minHeight: 52, marginTop: 6 }} />
            </div>
          ))}
          <button className="btn small" onClick={() => setDraft((d) => ({ ...d, work: [...d.work, { name: '', position: '', startDate: '', endDate: '', summary: '' }] }))}>
            ＋ 添加经历
          </button>

          <h3>项目经历（{draft.projects.length}）</h3>
          {draft.projects.map((p, i) => (
            <div className="draft-block" key={i}>
              <div className="row">
                <input type="text" value={p.name} placeholder="项目名" onChange={(ev) => patchProj(i, 'name', ev.target.value)} style={{ flex: 2 }} />
                <input type="text" value={p.role} placeholder="角色（可选）" onChange={(ev) => patchProj(i, 'role', ev.target.value)} style={{ flex: 1 }} />
                <button className="icon-btn" aria-label="删除这个项目" onClick={() => setDraft((d) => ({ ...d, projects: d.projects.filter((_, j) => j !== i) }))}>✕</button>
              </div>
              <textarea value={p.description} placeholder="项目描述 / 技术栈" onChange={(ev) => patchProj(i, 'description', ev.target.value)} style={{ minHeight: 52, marginTop: 6 }} />
            </div>
          ))}
          <button className="btn small" onClick={() => setDraft((d) => ({ ...d, projects: [...d.projects, { name: '', role: '', description: '' }] }))}>
            ＋ 添加项目
          </button>

          <h3>技能</h3>
          <label className="field">
            用逗号/顿号分隔（匹配引擎按技能识别 JD，先写最有把握的 5-10 个）
            <textarea value={skillsText} onChange={(e) => setSkillsText(e.target.value)} style={{ minHeight: 54 }} placeholder="Java、Golang、MySQL、Redis、Spring Boot" />
          </label>

          {aiAvailable && (
            <p className="hint">
              <a onClick={aiParse} style={{ cursor: 'pointer' }}>✨ 用 AI 再精解析一次（可选，需确认数据出境）</a>
            </p>
          )}

          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary" onClick={saveDraft} disabled={busy || !draft.basics.name.trim()}>
              {busy ? '保存中…' : '确认无误，建立画像 →'}
            </button>
            <button className="btn" onClick={() => setStep(1)} disabled={busy}>← 重新解析</button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="card" style={{ padding: 24, maxWidth: 640 }}>
          <h2 style={{ marginTop: 0 }}>你的求职方向</h2>
          <p className="hint">匹配引擎用这些偏好过滤和排序岗位。留空 = 不限；随时可在「我的画像」修改。</p>
          <label className="field">目标角色（逗号分隔）
            <input type="text" value={dir.roles} onChange={(e) => setDir({ ...dir, roles: e.target.value })} placeholder="后端开发、Java工程师" />
          </label>
          <div className="row">
            <label className="field" style={{ flex: 1 }}>目标城市（逗号分隔）
              <input type="text" value={dir.cities} onChange={(e) => setDir({ ...dir, cities: e.target.value })} placeholder="杭州、上海" />
            </label>
            <label className="field" style={{ width: 150 }}>批次
              <select value={dir.types} onChange={(e) => setDir({ ...dir, types: e.target.value })}>
                <option value="">不限</option>
                <option value="campus">只要校招</option>
                <option value="social">只要社招</option>
                <option value="internship">只要实习</option>
              </select>
            </label>
          </div>
          <div className="row">
            <label className="field" style={{ width: 180 }}>期望月薪下限（K，可选）
              <input type="number" value={dir.salaryMin} onChange={(e) => setDir({ ...dir, salaryMin: e.target.value })} placeholder="20" />
            </label>
            <label className="field" style={{ width: 180 }}>毕业年份（校招可选）
              <input type="number" value={dir.gradYear} onChange={(e) => setDir({ ...dir, gradYear: e.target.value })} placeholder="2027" />
            </label>
          </div>
          <label className="check-row">
            <input type="checkbox" checked={seedDemo} onChange={(e) => setSeedDemo(e.target.checked)} />
            顺便导入 8 条示例岗位（虚构雇主，可一键清除）——先看看匹配分和 ATS 报告长什么样
          </label>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary" onClick={() => finish(false)} disabled={busy}>
              {busy ? '保存中…' : '保存方向，开始使用 →'}
            </button>
            <button className="btn" onClick={() => finish(true)} disabled={busy}>跳过（建通用偏好）</button>
          </div>
        </div>
      )}

      {aiAsking && (
        <div className="cmdk-overlay" onClick={() => setAiAsking(false)}>
          <div className="card confirm-card" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="AI 数据出境确认">
            <h3 style={{ marginTop: 0 }}>AI 精解析需要发送数据</h3>
            <p style={{ fontSize: 14 }}>{aiDisclosure}</p>
            <p className="hint">发送对象是你自己启用的 AI Provider；不启用则始终使用本地启发式解析。</p>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setAiAsking(false)}>取消</button>
              <button className="btn primary" onClick={aiParseAcked}>已了解，继续</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** AI 精解析的输入文本：优先原始粘贴文本；文件路径下用草稿回拼（AI 只做再精化）。 */
function draftToText(draft: ResumeDraft): string {
  const b = draft.basics
  const lines = [
    b.name, [b.phone, b.email].filter(Boolean).join(' | '), b.label,
    '教育背景',
    ...draft.education.map((e) => `${e.startDate} - ${e.endDate} ${e.institution} ${e.area} ${e.studyType}`),
    '实习经历',
    ...draft.work.flatMap((w) => [
      `${w.startDate} - ${w.endDate} ${w.name} ${w.position}`, ...(w.summary ? w.summary.split('\n') : []),
    ]),
    '项目经历',
    ...draft.projects.flatMap((p) => [`${p.name}${p.role ? `（${p.role}）` : ''}`, ...(p.description ? p.description.split('\n') : [])]),
    '专业技能',
    draft.skills.map((s) => s.name).join('、'),
  ]
  return lines.filter((x) => x && x.trim()).join('\n')
}
