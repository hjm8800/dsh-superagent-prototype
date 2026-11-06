/**
 * Domain model for the market-department tracker.
 *
 * The organisation is a tree of positions; every position owns a list of
 * responsibility items. People are attached to positions. Reports are filed by
 * a person for a period (week or month) and answer, per responsibility item,
 * how far it progressed during that period.
 */

/** How far along one responsibility item is for one reporting period. */
export const PROGRESS_STATUSES = [
  'not_started',
  'in_progress',
  'done',
  'blocked',
  'not_applicable',
] as const

export type ProgressStatus = (typeof PROGRESS_STATUSES)[number]

export const PROGRESS_LABELS: Record<ProgressStatus, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  done: '已完成',
  blocked: '受阻',
  not_applicable: '不适用',
}

/** Reporting periods are either a week or a month. */
export type PeriodType = 'week' | 'month'

export const PERIOD_LABELS: Record<PeriodType, string> = {
  week: '周报',
  month: '月报',
}

/** Where a position sits in the department's chain of command. */
export type PositionLevel =
  | 'manager'
  | 'deputy_manager'
  | 'specialist'
  | 'specialist_staff'
  | 'team'

export const LEVEL_LABELS: Record<PositionLevel, string> = {
  manager: '经理',
  deputy_manager: '副经理',
  specialist: '专责',
  specialist_staff: '业务员',
  team: '班组',
}

export interface Position {
  /** Stable slug, e.g. `contract-clerk`. */
  id: string
  departmentId: string
  name: string
  role: string
  level: PositionLevel
  /** Id of the parent position, or null for the department root. */
  parentId: string | null
  /** Short note about headcount, e.g. `3人`. */
  headcountNote: string
  /** One-line work instruction from the source table. */
  instructions: string
  /** Where the specialist's and the clerk's duties meet. */
  boundary: string
  /** Free-form note carried over from the source table. */
  note: string
  /** Individual responsibility items, in order. */
  duties: Duty[]
}

export interface Duty {
  /** Stable id, unique within the position. */
  id: string
  positionId: string
  /** 1-based order within the position. */
  ordinal: number
  /** The responsibility text itself. */
  text: string
}

export interface Person {
  /** Stable slug, e.g. `huang-meilin`. */
  id: string
  departmentId: string
  name: string
  /** Position the person belongs to. */
  positionId: string
  /** Secret used in the person's own report link. */
  token: string
  /** True when the source table left the placement open. */
  pending: boolean
  /** Note about the person's placement in the source table. */
  note: string
}

/** One person's answer for one responsibility item in one period. */
export interface ReportItem {
  dutyId: string
  status: ProgressStatus
  /** Optional completion percentage, 0-100. */
  percent: number | null
  /** What was actually done, or what blocks it. */
  note: string
}

export interface Report {
  id: number
  personId: string
  /** `week` or `month`. */
  periodType: PeriodType
  /** Period key, e.g. `2026-W45` or `2026-11`. */
  periodKey: string
  /** Human-readable period label, e.g. `2026年第45周`. */
  periodLabel: string
  /** Free-text summary the person writes for the period. */
  summary: string
  /** What blocked the work, if anything. */
  blockers: string
  /** What comes next. */
  nextPlan: string
  submittedAt: string
  items: ReportItem[]
}

export interface Department {
  id: string
  code: string
  name: string
  nameEn: string
}

/** A position plus the people in it, as the views need it. */
export interface PositionView extends Position {
  people: Person[]
  children: PositionView[]
}

/** Aggregate completion for one position in one period. */
export interface PositionProgress {
  positionId: string
  positionName: string
  level: PositionLevel
  /** Number of people in the position who filed a report. */
  reported: number
  /** Number of people in the position in total. */
  total: number
  /** Number of duty items answered across all reports. */
  answered: number
  /** Number of duty items expected across all reports. */
  expected: number
  /** Mean completion percentage over answered items, 0-100. */
  percent: number
  /** Number of answered items whose status is `done`. */
  done: number
  /** Number of answered items whose status is `blocked`. */
  blocked: number
}
