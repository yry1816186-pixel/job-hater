// 与后端 Pydantic 契约对应的领域类型（只声明 UI 实际使用的字段）

export interface Profile {
  id: string
  display_name: string
  headline?: string | null
  summary?: string | null
}

export interface ProfileView {
  profile: Profile
  educations: Education[]
  experiences: ExperienceItem[]
  projects: ProjectItem[]
  skills: Skill[]
}

export interface Education {
  id: string
  school: string
  degree?: string | null
  major?: string | null
  start_date?: string | null
  end_date?: string | null
  gpa?: number | null
  gpa_note?: string | null
}

export interface ExperienceItem {
  id: string
  employer: string
  title: string
  kind: string
  start_date?: string | null
  end_date?: string | null
  is_current: boolean
  description?: string | null
  tags: string[]
  evidence_ids: string[]
}

export interface ProjectItem {
  id: string
  name: string
  role?: string | null
  description?: string | null
  tags: string[]
}

export interface Skill {
  id: string
  name: string
  category?: string | null
  level?: number | null
  years?: number | null
  aliases: string[]
}

export interface Evidence {
  id: string
  original_text: string
  normalized_fact?: string | null
  fact_type?: string | null
  user_confirmed: boolean
  confidence: number
}

export interface Preset {
  id: string
  profile_id: string
  name: string
  is_active: boolean
  employment_types: string[]
  target_roles: string[]
  target_cities: string[]
  remote_ok: boolean
  salary_min_k?: number | null
  max_experience_years_required?: number | null
  graduation_year?: number | null
  weights: Record<string, number>
  gates: Record<string, unknown>
}

export interface Job {
  id: string
  title: string
  employer_name: string
  city?: string | null
  salary_text?: string | null
  salary_min_k?: number | null
  salary_max_k?: number | null
  salary_months?: number | null
  recruitment_type: string
  experience_required_min?: number | null
  experience_required_text?: string | null
  education_required?: string | null
  description?: string | null
  canonical_url?: string | null
  status: string
  published_at?: string | null
  deadline?: string | null
  source_id: string
  extras: Record<string, unknown>
}

export interface GateOutcome {
  code: string
  passed: boolean
  detail: string
}

export interface DimensionScore {
  score: number
  reasons: string[]
  uncertainty?: string | null
}

export interface MatchOutcome {
  job_id: string
  eligible: boolean
  gate_reasons: GateOutcome[]
  relevance_score?: number | null
  rank_score?: number | null
  verdict?: string | null
  dims: Record<string, DimensionScore>
  evidence: Record<string, unknown>
  needs_review: boolean
}

export interface Application {
  id: string
  job_id: string
  profile_id: string
  status: string
  applied_at?: string | null
  apply_channel?: string | null
  notes?: string | null
  updated_at?: string | null
  /** 列表接口嵌岗位于（可辨识名字，替代裸 job_id） */
  job_title?: string | null
  employer_name?: string | null
  job_city?: string | null
}

export interface ApplicationEvent {
  id: number
  application_id: string
  kind: string
  payload_json?: string | null
  note?: string | null
  occurred_at: string
}

export interface Offer {
  id: string
  application_id: string
  base_salary_k: number
  salary_months?: number | null
  city?: string | null
  work_mode?: string | null
  deadline?: string | null
  status: string
  benefits: string[]
  custom_dimensions: Record<string, string | number>
  notes?: string | null
}

export interface OfferCompareRow extends Offer {
  job_title: string
  employer_name: string
  job_city?: string | null
  annual_base_k: number
}

export interface JobSourceInfo {
  id: string
  display_name: string
  adapter_kind: string
  enabled: boolean
  health_status: string
  health_message?: string | null
  last_success_at?: string | null
  consecutive_failures: number
}

export interface AIProviderInfo {
  id: string
  adapter_kind: string
  display_name: string
  base_url?: string | null
  model: string
  enabled: boolean
  has_api_key?: boolean
}

export interface PasteDraft {
  title?: string
  company?: string
  city?: string
  salary?: string
  experience?: string
  education?: string
  department?: string
  url?: string
  description: string
  parse_notes: string[]
  needs_review_fields: string[]
}

export type FeedbackKind =
  | 'interested' | 'not_interested' | 'too_far' | 'low_pay' | 'bad_industry'
  | 'bad_company' | 'skill_mismatch' | 'applied' | 'rejected' | 'got_interview' | 'got_offer'

export const APPLICATION_STATUS_LABELS: Record<string, string> = {
  discovered: '刚发现',
  saved: '已收藏',
  shortlisted: '入围',
  preparing: '准备材料',
  materials_ready: '材料就绪',
  ready_to_apply: '待投递',
  applied_confirmed: '已投递',
  assessment: '测评中',
  interviewing: '面试中',
  offer: 'Offer',
  rejected: '被拒',
  withdrawn: '已撤回',
  closed: '已关闭',
}

export const BOARD_COLUMNS = [
  'saved', 'shortlisted', 'preparing', 'ready_to_apply',
  'applied_confirmed', 'interviewing', 'offer', 'rejected',
] as const
