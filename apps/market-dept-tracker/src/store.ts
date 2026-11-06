import type { Database } from './db.ts'
import type {
  Department,
  Duty,
  Person,
  Position,
  PositionProgress,
  PositionView,
  ProgressStatus,
  Report,
  ReportItem,
} from './types.ts'

/** A row as it comes back from SQLite, before it is mapped to the model. */
interface PositionRow {
  id: string
  department_id: string
  name: string
  role: string
  level: string
  parent_id: string | null
  headcount_note: string
  instructions: string
  boundary: string
  note: string
}

interface PersonRow {
  id: string
  department_id: string
  name: string
  position_id: string
  token: string
  pending: number
  note: string
}

/** Read and write operations over the tracker's tables. */
export class Store {
  private readonly db: Database

  constructor(db: Database) {
    this.db = db
  }
  // ------------------------------------------------------------- reading

  departments(): Department[] {
    return this.db.raw.prepare('SELECT id, code, name, name_en FROM departments ORDER BY code').all() as unknown as Department[]
  }

  positions(departmentId?: string): Position[] {
    const rows = this.db.raw
      .prepare(
        `SELECT id, department_id, name, role, level, parent_id, headcount_note,
                instructions, boundary, note
         FROM positions
         ${departmentId ? 'WHERE department_id = ?' : ''}
         ORDER BY ordinal, name`,
      )
      .all(...(departmentId ? [departmentId] : [])) as unknown as PositionRow[]
    return rows.map((r) => ({
      id: r.id,
      departmentId: r.department_id,
      name: r.name,
      role: r.role,
      level: r.level as Position['level'],
      parentId: r.parent_id,
      headcountNote: r.headcount_note,
      instructions: r.instructions,
      boundary: r.boundary,
      note: r.note,
      duties: [],
    }))
  }

  duties(positionId: string): Duty[] {
    const rows = this.db.raw
      .prepare('SELECT id, position_id, ordinal, text FROM duties WHERE position_id = ? ORDER BY ordinal')
      .all(positionId) as unknown as Array<{ id: string; position_id: string; ordinal: number; text: string }>
    return rows.map((r) => ({ id: r.id, positionId: r.position_id, ordinal: r.ordinal, text: r.text }))
  }

  people(departmentId?: string): Person[] {
    const rows = this.db.raw
      .prepare(
        `SELECT id, department_id, name, position_id, token, pending, note
         FROM people
         ${departmentId ? 'WHERE department_id = ?' : ''}
         ORDER BY name`,
      )
      .all(...(departmentId ? [departmentId] : [])) as unknown as PersonRow[]
    return rows.map((r) => ({
      id: r.id,
      departmentId: r.department_id,
      name: r.name,
      positionId: r.position_id,
      token: r.token,
      pending: r.pending === 1,
      note: r.note,
    }))
  }

  personById(id: string): Person | undefined {
    const rows = this.people()
    return rows.find((p) => p.id === id)
  }

  personByToken(token: string): Person | undefined {
    const row = this.db.raw
      .prepare('SELECT id, department_id, name, position_id, token, pending, note FROM people WHERE token = ?')
      .get(token) as unknown as PersonRow | undefined
    if (!row) return undefined
    return {
      id: row.id,
      departmentId: row.department_id,
      name: row.name,
      positionId: row.position_id,
      token: row.token,
      pending: row.pending === 1,
      note: row.note,
    }
  }

  positionById(id: string): Position | undefined {
    const all = this.positions()
    const found = all.find((p) => p.id === id)
    if (!found) return undefined
    found.duties = this.duties(id)
    return found
  }

  /** The position tree of a department, each position carrying its people. */
  tree(departmentId: string): PositionView[] {
    const positions = this.positions(departmentId)
    const people = this.people(departmentId)
    const byParent = new Map<string | null, Position[]>()
    for (const p of positions) {
      const list = byParent.get(p.parentId) ?? []
      list.push(p)
      byParent.set(p.parentId, list)
    }
    const build = (parentId: string | null): PositionView[] =>
      (byParent.get(parentId) ?? []).map((p) => ({
        ...p,
        duties: this.duties(p.id),
        people: people.filter((person) => person.positionId === p.id),
        children: build(p.id),
      }))
    return build(null)
  }

  // ------------------------------------------------------------- reports

  reportsForPeriod(periodType: string, periodKey: string): Report[] {
    const rows = this.db.raw
      .prepare(
        `SELECT id, person_id, period_type, period_key, period_label, summary, blockers, next_plan, submitted_at
         FROM reports WHERE period_type = ? AND period_key = ?`,
      )
      .all(periodType, periodKey) as unknown as Array<Record<string, string | number>>
    return rows.map((r) => {
      const id = r.id as number
      return {
        id,
        personId: r.person_id as string,
        periodType: r.period_type as Report['periodType'],
        periodKey: r.period_key as string,
        periodLabel: r.period_label as string,
        summary: (r.summary as string) ?? '',
        blockers: (r.blockers as string) ?? '',
        nextPlan: (r.next_plan as string) ?? '',
        submittedAt: r.submitted_at as string,
        items: this.reportItems(id),
      }
    })
  }

  reportItems(reportId: number): ReportItem[] {
    const rows = this.db.raw
      .prepare('SELECT duty_id, status, percent, note FROM report_items WHERE report_id = ?')
      .all(reportId) as unknown as Array<{ duty_id: string; status: string; percent: number | null; note: string }>
    return rows.map((r) => ({
      dutyId: r.duty_id,
      status: r.status as ProgressStatus,
      percent: r.percent,
      note: r.note ?? '',
    }))
  }

  reportFor(personId: string, periodType: string, periodKey: string): Report | undefined {
    return this.reportsForPeriod(periodType, periodKey).find((r) => r.personId === personId)
  }

  /**
   * File or replace one person's report for a period.
   *
   * The report and its items are replaced as a unit, so a resubmission never
   * leaves half of the previous answers behind.
   */
  saveReport(input: {
    personId: string
    periodType: string
    periodKey: string
    periodLabel: string
    summary: string
    blockers: string
    nextPlan: string
    items: ReportItem[]
    submittedAt?: string
  }): number {
    const submittedAt = input.submittedAt ?? new Date().toISOString()
    this.db.raw.exec('BEGIN')
    try {
      this.db.raw
        .prepare(
          `INSERT INTO reports (person_id, period_type, period_key, period_label, summary, blockers, next_plan, submitted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (person_id, period_type, period_key) DO UPDATE SET
             period_label = excluded.period_label, summary = excluded.summary,
             blockers = excluded.blockers, next_plan = excluded.next_plan,
             submitted_at = excluded.submitted_at`,
        )
        .run(
          input.personId, input.periodType, input.periodKey, input.periodLabel,
          input.summary, input.blockers, input.nextPlan, submittedAt,
        )

      const row = this.db.raw
        .prepare('SELECT id FROM reports WHERE person_id = ? AND period_type = ? AND period_key = ?')
        .get(input.personId, input.periodType, input.periodKey) as unknown as { id: number }
      const reportId = row.id

      this.db.raw.prepare('DELETE FROM report_items WHERE report_id = ?').run(reportId)
      const insertItem = this.db.raw.prepare(
        'INSERT INTO report_items (report_id, duty_id, status, percent, note) VALUES (?, ?, ?, ?, ?)',
      )
      for (const item of input.items) {
        insertItem.run(reportId, item.dutyId, item.status, item.percent, item.note)
      }
      this.db.raw.exec('COMMIT')
      return reportId
    } catch (error) {
      this.db.raw.exec('ROLLBACK')
      throw error
    }
  }

  // ---------------------------------------------------------- aggregation

  /**
   * Completion per position for one period.
   *
   * A position's expected item count is the number of people in it times the
   * number of duties in it, so a position with unreported people shows up as
   * incomplete rather than silently missing.
   */
  progress(periodType: string, periodKey: string, departmentId?: string): PositionProgress[] {
    const positions = this.positions(departmentId)
    const people = this.people(departmentId)
    const reports = this.reportsForPeriod(periodType, periodKey)
    const reportByPerson = new Map(reports.map((r) => [r.personId, r]))

    return positions.map((position) => {
      const duties = this.duties(position.id)
      const members = people.filter((p) => p.positionId === position.id)
      let answered = 0
      let expected = 0
      let done = 0
      let blocked = 0
      let percentSum = 0

      for (const member of members) {
        expected += duties.length
        const report = reportByPerson.get(member.id)
        if (!report) continue
        for (const item of report.items) {
          if (!duties.some((d) => d.id === item.dutyId)) continue
          answered++
          if (item.status === 'done') done++
          if (item.status === 'blocked') blocked++
          if (item.status === 'not_applicable') continue
          percentSum += item.percent ?? statusWeight(item.status)
        }
      }

      const reported = members.filter((m) => reportByPerson.has(m.id)).length
      return {
        positionId: position.id,
        positionName: position.name,
        level: position.level,
        reported,
        total: members.length,
        answered,
        expected,
        percent: answered === 0 ? 0 : Math.round((percentSum / answered) * 10) / 10,
        done,
        blocked,
      }
    })
  }
}

/** Default completion weight for a status when the reporter gave no percentage. */
export function statusWeight(status: ProgressStatus): number {
  switch (status) {
    case 'done':
      return 100
    case 'in_progress':
      return 50
    case 'not_started':
      return 0
    case 'blocked':
      return 30
    case 'not_applicable':
      return 100
  }
}
