// 共享 UI 基建：Toast 反馈系统、确认对话框、全量中文标签映射。
// 目标（真实用户反馈「搞不懂逻辑和交互」）：系统术语永不裸露给用户。
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'

// ---------- 标签映射（系统值 → 用户语言） ----------

/** 匹配 Gate 的英文 code → 中文说明 */
export const GATE_LABELS: Record<string, string> = {
  recruitment_type: '招聘批次',
  experience_over_max: '经验年限',
  education: '学历要求',
  city: '工作城市',
  salary_floor: '薪资底线',
  excluded_employer: '排除企业',
  excluded_keyword: '排除关键词',
  headhunter: '猎头岗位',
  outsourcing: '外包岗位',
  expired: '岗位已过期',
  graduation_year: '毕业届数',
}

/** 投递事件 kind → 中文 */
export const EVENT_KIND_LABELS: Record<string, string> = {
  created: '建立跟踪',
  status_change: '状态变更',
  interview_scheduled: '面试已排期',
  offer_received: '收到Offer',
}

export const OFFER_STATUS_LABELS: Record<string, string> = {
  considering: '考虑中',
  accepted: '已接受',
  declined: '已婉拒',
  expired: '已过期',
}

export const AI_TASK_LABELS: Record<string, string> = {
  job_deep_review: '岗位深度分析',
  resume_rewrite: '简历改写建议',
  cover_letter: '求职信起草',
  interview_mock: '模拟面试',
  interview_review: '面试复盘',
  fact_extraction: '文档事实提取',
}

/** 岗位状态 → 中文 */
export const JOB_STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  expired: '已过期',
  archived: '已归档',
  rejected: '已排除',
}

export const RECRUIT_LABELS: Record<string, string> = {
  campus: '校招',
  social: '社招',
  internship: '实习',
  unknown: '批次未知',
}

/** 面试结果 outcome → 中文 */
export const INTERVIEW_OUTCOME_LABELS: Record<string, string> = {
  pass: '通过',
  fail: '未通过',
  pending: '待定',
  unknown: '未知',
}

// ---------- Toast ----------

interface ToastItem {
  id: number
  kind: 'success' | 'error' | 'info'
  text: string
}

const ToastCtx = createContext<{
  toast: (kind: ToastItem['kind'], text: string) => void
}>({ toast: () => {} })

export const useToast = () => useContext(ToastCtx)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const toast = useCallback((kind: ToastItem['kind'], text: string) => {
    const id = nextId.current++
    setItems((prev) => [...prev.slice(-3), { id, kind, text }])
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id))
    }, kind === 'error' ? 6000 : 3200)
  }, [])

  return (
    <ToastCtx.Provider value={{ toast }}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} role="status">
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

// ---------- 确认对话框（危险操作二次确认） ----------

export function ConfirmDialog({
  open,
  title,
  body,
  confirmText = '确认',
  danger,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  body: string
  confirmText?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal confirm" onClick={(e) => e.stopPropagation()} role="alertdialog" aria-label={title}>
        <h3>{title}</h3>
        <p>{body}</p>
        <div className="modal-actions">
          <button className="btn" onClick={onCancel}>取消</button>
          <button className={`btn ${danger ? 'danger' : 'primary'}`} onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------- 模态框（替代 window.prompt） ----------

export function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

// ---------- 空状态（统一「下一步指引」形态） ----------

export function EmptyState({
  icon = '📭',
  title,
  hint,
  action,
}: {
  icon?: string
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon" aria-hidden>{icon}</div>
      <h3>{title}</h3>
      {hint && <p>{hint}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  )
}

// ---------- 步骤条（引导向导） ----------

export function StepBadge({ n, done }: { n: number; done?: boolean }) {
  return <span className={`step-badge${done ? ' done' : ''}`}>{done ? '✓' : n}</span>
}
