import { useEffect, useState } from 'react'
import { api, qs } from '../api'
import { useProfiles } from '../App'

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

interface FullVersion {
  id: string
  resume_id: string
  version: number
  sections: Record<string, unknown>
  factcheck_report: FactcheckReport | null
  note?: string | null
}

/** 简历：master 版本链 + factcheck 守门 + 多格式导出 */
export default function ResumePage() {
  const { activeId } = useProfiles()
  const [resumes, setResumes] = useState<{ id: string; name: string; kind: string; status: string; current_version: number }[]>([])
  const [versions, setVersions] = useState<VersionInfo[]>([])
  const [current, setCurrent] = useState<FullVersion | null>(null)
  const [editorText, setEditorText] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

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
    const vs = await api.get<VersionInfo[]>(`/resumes/${rid}/versions`)
    setVersions(vs)
    if (vs.length) openVersion(vs[0].id)
  }

  const openVersion = async (vid: string) => {
    const v = await api.get<FullVersion>(`/resume-versions/${vid}`)
    setCurrent(v)
    setEditorText(JSON.stringify(v.sections, null, 2))
  }

  const generate = async () => {
    if (!activeId) return
    setErr('')
    try {
      const r = await api.post<{ resume_id: string; version_id: string; factcheck: FactcheckReport }>(
        '/resumes/generate',
        { profile_id: activeId },
      )
      setMsg(
        `已从画像生成主简历 v1。事实校验：${r.factcheck.passed ? '通过' : `${r.factcheck.issues.length} 个问题`}（${r.factcheck.bullets_checked} 条已查）。`,
      )
      loadResumes()
      openResume(r.resume_id)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const commitEdit = async () => {
    if (!current) return
    setErr('')
    try {
      const sections = JSON.parse(editorText)
      const r = await api.post<{ version_id: string; factcheck: FactcheckReport }>(
        `/resumes/${current.resume_id}/versions`,
        { sections, note: '编辑器修改' },
      )
      setMsg(`已保存为新版本。事实校验：${r.factcheck.passed ? '通过' : `${r.factcheck.issues.length} 个问题`}`)
      openResume(current.resume_id)
      openVersion(r.version_id)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const runFactcheck = async () => {
    if (!current) return
    const r = await api.post<FactcheckReport>(`/resume-versions/${current.id}/factcheck`, {})
    setCurrent({ ...current, factcheck_report: r })
  }

  const markFinal = async () => {
    if (!current) return
    setErr('')
    try {
      await api.post(`/resume-versions/${current.id}/final`, {})
      setMsg('已标记为终稿（通过事实校验的版本才允许）')
      loadResumes()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const fc = current?.factcheck_report

  return (
    <div>
      <h1>简历</h1>
      <p className="page-sub">
        主简历 → 版本历史 → 岗位定制版。每条经历可溯源到证据；事实校验不过闸不能定稿。
      </p>
      {err && <div className="error-box">{err}</div>}
      {msg && <div className="card" style={{ borderColor: 'var(--accent)' }}>{msg}</div>}

      <div className="row">
        <button className="btn primary" onClick={generate} disabled={!activeId}>
          从画像生成 / 更新主简历
        </button>
      </div>

      {resumes.length > 0 && (
        <h2>简历库</h2>
      )}
      {resumes.map((r) => (
        <div className="card between" key={r.id}>
          <div>
            <b>{r.name}</b>{r.status === 'final' ? <span className="tag green" style={{ marginLeft: 8 }}>终稿</span> : <span className="tag" style={{ marginLeft: 8 }}>草稿</span>}
            <div style={{ fontSize: 13, color: 'var(--muted)' }}>
              当前 v{r.current_version} · {r.kind === 'master' ? '主简历' : '岗位定制'}
            </div>
          </div>
          <button className="btn" onClick={() => openResume(r.id)}>
            打开
          </button>
        </div>
      ))}

      {versions.length > 0 && (
        <>
          <h2>版本历史（不可变）</h2>
          <div className="card row" style={{ flexWrap: 'wrap' }}>
            {versions.map((v) => (
              <button
                key={v.id}
                className={`btn small${current?.id === v.id ? ' primary' : ''}`}
                onClick={() => openVersion(v.id)}
              >
                v{v.version}
                {v.checked ? ' ✓' : ''}
              </button>
            ))}
          </div>
        </>
      )}

      {current && (
        <>
          <h2>编辑 v{current.version}（JSON Resume 结构）</h2>
          <div className="card">
            <textarea
              value={editorText}
              onChange={(e) => setEditorText(e.target.value)}
              style={{ minHeight: 320, fontFamily: 'Consolas, monospace', fontSize: 13 }}
              aria-label="简历结构编辑器"
            />
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" onClick={commitEdit}>
                保存为新版本（自动跑事实校验）
              </button>
              <button className="btn" onClick={runFactcheck}>
                重新校验
              </button>
              <button className="btn" onClick={markFinal}>
                标记终稿
              </button>
            </div>
          </div>

          {fc && (
            <div className={`card ${fc.passed ? '' : 'error-box'}`}>
              <b>
                事实校验{fc.passed ? '通过' : `未通过`}（{fc.bullets_checked} 条 bullet）
              </b>
              {fc.issues.length > 0 && (
                <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                  {fc.issues.map((i, k) => (
                    <li key={k}>
                      [{i.type}] {i.path}：{i.msg}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <h2>导出</h2>
          <div className="row">
            {(['md', 'html', 'json', 'pdf', 'docx'] as const).map((fmt) => (
              <a className="btn" key={fmt} href={`/api/resume-versions/${current.id}/export${qs({ fmt })}`} target="_blank">
                {fmt.toUpperCase()}
              </a>
            ))}
          </div>
          <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>
            PDF 需要 playwright（可选依赖）、DOCX 需要 python-docx；未安装时后端会给出
            降级指引（HTML 可直接浏览器打印为 PDF）。
          </p>
        </>
      )}
    </div>
  )
}
