import { useState } from 'react'
import { api } from '../api'
import type { PasteDraft } from '../types'

/** 导入岗位：粘贴 JD（§7 通用入口，所有自动化失效时主链仍可用） */
export default function ImportPage() {
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [draft, setDraft] = useState<PasteDraft | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const parse = async () => {
    setErr('')
    setMsg('')
    setDraft(null)
    try {
      const r = await api.post<{ draft: PasteDraft }>('/import/paste', { text, url: url || null })
      setDraft(r.draft)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const save = async () => {
    if (!draft) return
    setErr('')
    try {
      const r = await api.post<{ saved: { added: number; deduped_exact: number } | null }>(
        '/import/paste',
        { text, url: url || null, save: true },
      )
      if (r.saved) {
        setMsg(
          `已入库：新增 ${r.saved.added} 条，去重跳过 ${r.saved.deduped_exact} 条。去岗位收件箱查看。`,
        )
        setDraft(null)
        setText('')
        setUrl('')
      }
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const setField = (k: keyof PasteDraft, v: string) => {
    if (!draft) return
    setDraft({ ...draft, [k]: v } as PasteDraft)
  }

  return (
    <div>
      <h1>导入岗位</h1>
      <p className="page-sub">
        在任何招聘网站看到心仪岗位：复制 JD 原文粘贴到这里。系统抽取结构化字段，
        你确认后入库——不依赖任何平台的自动化接口。
      </p>
      {err && <div className="error-box">{err}</div>}
      {msg && <div className="card" style={{ borderColor: 'var(--accent)' }}>{msg}</div>}
      <label className="field">
        JD 原文（必填，尽量完整）
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={'粘贴岗位描述全文。含「职位：/公司：/城市：」等标签时抽取最准。'}
          style={{ minHeight: 200 }}
        />
      </label>
      <label className="field">
        岗位链接（可选）
        <input type="text" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
      </label>
      <button className="btn primary" onClick={parse} disabled={!text.trim()}>
        解析为结构化草稿
      </button>

      {draft && (
        <div className="card" style={{ marginTop: 18 }}>
          <h2 style={{ marginTop: 0 }}>解析草稿（请核对后保存）</h2>
          {draft.needs_review_fields.length > 0 && (
            <div className="error-box">
              以下字段未能自动解析，请补填：{draft.needs_review_fields.join('、')}
            </div>
          )}
          <div className="row">
            <label className="field" style={{ flex: 2 }}>
              岗位标题
              <input type="text" value={draft.title ?? ''} onChange={(e) => setField('title', e.target.value)} />
            </label>
            <label className="field" style={{ flex: 2 }}>
              公司（必填）
              <input type="text" value={draft.company ?? ''} onChange={(e) => setField('company', e.target.value)} />
            </label>
          </div>
          <div className="row">
            <label className="field" style={{ flex: 1 }}>
              城市
              <input type="text" value={draft.city ?? ''} onChange={(e) => setField('city', e.target.value)} />
            </label>
            <label className="field" style={{ flex: 1 }}>
              薪资
              <input type="text" value={draft.salary ?? ''} onChange={(e) => setField('salary', e.target.value)} />
            </label>
            <label className="field" style={{ flex: 1 }}>
              经验要求
              <input type="text" value={draft.experience ?? ''} onChange={(e) => setField('experience', e.target.value)} />
            </label>
            <label className="field" style={{ flex: 1 }}>
              学历
              <input type="text" value={draft.education ?? ''} onChange={(e) => setField('education', e.target.value)} />
            </label>
          </div>
          {draft.parse_notes.length > 0 && (
            <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>解析依据：{draft.parse_notes.join('；')}</p>
          )}
          <button className="btn primary" onClick={save} disabled={!draft.title || !draft.company}>
            确认入库
          </button>
        </div>
      )}
    </div>
  )
}
