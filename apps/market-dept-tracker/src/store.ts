import type { Database } from './db.ts'
import { taskStore } from './tasks.ts'
import type {
  Account,
  ArtifactGroup,
  Department,
  Duty,
  Person,
  PeriodType,
  Position,
  PositionProgress,
  PositionView,
  ProgressStatus,
  RecurringTask,
  Report,
  ReportItem,
  Task,
  TaskKind,
  TaskStatus,
  TaskView,
} from './types.ts'

/** A row of the artifacts table, before it is mapped to the model. */
interface ArtifactRow {
  id: string
  position_id: string
  artifact: string
  keywords: string
  duty_ids: string
  note: string
}

/** A row of the collected_files table, before it is mapped to the model. */
interface CollectedFileRow {
  id: string
  name: string
  owner_name: string
  modified_at: string
  location: string
  person_id: string | null
  artifact_id: string | null
  matched_keyword: string
  reason: string
}

/** A file the collector found, as the views need it. */
export interface CollectedFileRecord {
  id: string
  name: string
  ownerName: string
  modifiedAt: string
  location: string
  personId: string | null
  artifactId: string | null
  matchedKeyword: string
  reason: string
}

/** One person's completion, derived from the files they filed. */
export interface PersonProgress {
  personId: string
  personName: string
  positionId: string
  positionName: string
  level: string
  /** How many files the collector credited to this person. */
  files: number
  /** How many of their duties expect an output. */
  expected: number
  /** How many of those outputs arrived. */
  done: number
  /** Completion percentage, 0-100. */
  percent: number
}

/** Read a list stored as a JSON array in a text column. */
function parseList(value: string): string[] {
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}

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

interface AccountRow {
  id: string
  position_id: string
  username: string
  password_hash: string
  password_plain: string
  token: string
  created_at: string
}

/** Read and write operations over the tracker's tables. */
export class Store {
  /** The database, for the callers that need the raw connection. */
  readonly db: Database

  constructor(db: Database) {
    this.db = db
  }

  // The task list operations live in their own file and are mixed in below.
  declare recurringTasks: (departmentId?: string) => RecurringTask[]
  declare createRecurringTask: (input: {
    id: string
    departmentId: string
    positionId: string
    title: string
    detail: string
    cadence: PeriodType
    dutyId: string | null
    artifactId: string | null
    createdAt: string
  }) => void
  declare tasks: (periodType: string, periodKey: string, departmentId?: string) => Task[]
  declare createTask: (input: {
    id: string
    departmentId: string
    kind: TaskKind
    title: string
    detail: string
    personId: string
    positionId: string
    periodType: PeriodType
    periodKey: string
    periodLabel: string
    assignedById: string | null
    recurringTaskId: string | null
    dutyId: string | null
    artifactId: string | null
    createdAt: string
  }) => void
  declare updateTask: (id: string, status: TaskStatus, note: string, updatedAt: string) => void
  declare task: (id: string) => Task | undefined
  declare materialiseRecurringTasks: (
    departmentId: string,
    periodType: PeriodType,
    periodKey: string,
    periodLabel: string,
    now: string,
  ) => number
  declare taskViews: (periodType: string, periodKey: string, departmentId?: string) => TaskView[]
  declare taskProgress: (
    periodType: string,
    periodKey: string,
    departmentId?: string,
  ) => {
    positionId: string
    positionName: string
    level: string
    total: number
    done: number
    blocked: number
    percent: number
  }[]

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

  /** The login of every post. */
  accounts(): Account[] {
    const rows = this.db.raw
      .prepare(
        'SELECT id, position_id, username, password_hash, password_plain, token, created_at FROM accounts ORDER BY username',
      )
      .all() as unknown as AccountRow[]
    return rows.map((row) => this.accountFromRow(row))
  }

  accountByUsername(username: string): Account | undefined {
    const row = this.db.raw
      .prepare(
        'SELECT id, position_id, username, password_hash, password_plain, token, created_at FROM accounts WHERE username = ?',
      )
      .get(username) as unknown as AccountRow | undefined
    return row ? this.accountFromRow(row) : undefined
  }

  accountByToken(token: string): Account | undefined {
    const row = this.db.raw
      .prepare(
        'SELECT id, position_id, username, password_hash, password_plain, token, created_at FROM accounts WHERE token = ?',
      )
      .get(token) as unknown as AccountRow | undefined
    return row ? this.accountFromRow(row) : undefined
  }

  accountForPosition(positionId: string): Account | undefined {
    const row = this.db.raw
      .prepare(
        'SELECT id, position_id, username, password_hash, password_plain, token, created_at FROM accounts WHERE position_id = ?',
      )
      .get(positionId) as unknown as AccountRow | undefined
    return row ? this.accountFromRow(row) : undefined
  }

  createAccount(account: Account): void {
    this.db.raw
      .prepare(
        `INSERT INTO accounts (id, position_id, username, password_hash, password_plain, token, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        account.id,
        account.positionId,
        account.username,
        account.passwordHash,
        account.passwordPlain,
        account.token,
        account.createdAt,
      )
  }

  setAccountPassword(id: string, passwordHash: string, passwordPlain: string): void {
    this.db.raw.prepare('UPDATE accounts SET password_hash = ?, password_plain = ? WHERE id = ?').run(
      passwordHash,
      passwordPlain,
      id,
    )
  }

  private accountFromRow(row: AccountRow): Account {
    return {
      id: row.id,
      positionId: row.position_id,
      username: row.username,
      passwordHash: row.password_hash,
      passwordPlain: row.password_plain,
      token: row.token,
      createdAt: row.created_at,
    }
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
   * The expected outputs of every post that declares one.
   */
  artifacts(): ArtifactGroup[] {
    const rows = this.db.raw
      .prepare('SELECT id, position_id, artifact, keywords, duty_ids, note FROM artifacts ORDER BY position_id, id')
      .all() as unknown as ArtifactRow[]
    return rows.map((r) => ({
      id: r.id,
      positionId: r.position_id,
      artifact: r.artifact,
      keywords: parseList(r.keywords),
      dutyIds: parseList(r.duty_ids),
      note: r.note,
    }))
  }

  /**
   * Store what the collector found.
   *
   * Files are keyed by their id in the source, so re-running the collector
   * over the same folder updates a file rather than adding a second one.
   */
  saveCollectedFiles(
    periodType: string,
    periodKey: string,
    files: {
      id: string
      name: string
      ownerName: string
      modifiedAt: string
      location: string
      personId: string | null
      artifactId: string | null
      matchedKeyword: string
      reason: string
    }[],
    collectedAt: string,
  ): void {
    const insert = this.db.raw.prepare(
      `INSERT INTO collected_files (id, period_type, period_key, name, owner_name, modified_at, location, person_id, artifact_id, matched_keyword, reason, collected_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (id) DO UPDATE SET
         period_type = excluded.period_type, period_key = excluded.period_key, name = excluded.name,
         owner_name = excluded.owner_name, modified_at = excluded.modified_at, location = excluded.location,
         person_id = excluded.person_id, artifact_id = excluded.artifact_id,
         matched_keyword = excluded.matched_keyword, reason = excluded.reason, collected_at = excluded.collected_at`,
    )
    this.db.raw.exec('BEGIN')
    try {
      for (const file of files) {
        insert.run(
          file.id, periodType, periodKey, file.name, file.ownerName, file.modifiedAt, file.location,
          file.personId, file.artifactId, file.matchedKeyword, file.reason, collectedAt,
        )
      }
      this.db.raw.exec('COMMIT')
    } catch (error) {
      this.db.raw.exec('ROLLBACK')
      throw error
    }
  }

  /** The files the collector found for one period. */
  collectedFiles(periodType: string, periodKey: string): CollectedFileRecord[] {
    const rows = this.db.raw
      .prepare(
        `SELECT id, name, owner_name, modified_at, location, person_id, artifact_id, matched_keyword, reason
         FROM collected_files WHERE period_type = ? AND period_key = ? ORDER BY name`,
      )
      .all(periodType, periodKey) as unknown as CollectedFileRow[]
    return rows.map((r) => ({
      id: r.id,
      name: r.name,
      ownerName: r.owner_name,
      modifiedAt: r.modified_at,
      location: r.location,
      personId: r.person_id,
      artifactId: r.artifact_id,
      matchedKeyword: r.matched_keyword,
      reason: r.reason,
    }))
  }

  /**
   * Completion per position, derived from the files the collector found.
   *
   * A duty counts as done when the person filed a file for the output that
   * duty expects. A position's expected count is its people times its duties,
   * so a post where nobody filed anything shows as incomplete rather than
   * quietly missing.
   */
  artifactProgress(
    periodType: string,
    periodKey: string,
    departmentId?: string,
  ): PositionProgress[] {
    const positions = this.positions(departmentId)
    const people = this.people(departmentId)
    const artifacts = this.artifacts()
    const files = this.collectedFiles(periodType, periodKey)
    const artifactsByDuty = new Map<string, ArtifactGroup>()
    for (const artifact of artifacts) {
      for (const dutyId of artifact.dutyIds) artifactsByDuty.set(dutyId, artifact)
    }
    const filesByPersonAndArtifact = new Set<string>()
    for (const file of files) {
      if (file.personId && file.artifactId) filesByPersonAndArtifact.add(`${file.personId}|${file.artifactId}`)
    }

    return positions.map((position) => {
      const duties = this.duties(position.id)
      const members = people.filter((p) => p.positionId === position.id)
      let answered = 0
      let expected = 0
      let done = 0
      for (const member of members) {
        for (const duty of duties) {
          const artifact = artifactsByDuty.get(duty.id)
          if (!artifact) continue
          expected++
          if (filesByPersonAndArtifact.has(`${member.id}|${artifact.id}`)) {
            answered++
            done++
          }
        }
      }
      return {
        positionId: position.id,
        positionName: position.name,
        level: position.level,
        reported: members.filter((m) => files.some((f) => f.personId === m.id)).length,
        total: members.length,
        answered,
        expected,
        percent: expected === 0 ? 0 : Math.round((done / expected) * 1000) / 10,
        done,
        blocked: 0,
      }
    })
  }

  /**
   * Completion per person, derived from the files the collector found.
   */
  artifactProgressByPerson(
    periodType: string,
    periodKey: string,
    departmentId?: string,
  ): PersonProgress[] {
    const people = this.people(departmentId)
    const artifacts = this.artifacts()
    const files = this.collectedFiles(periodType, periodKey)
    const artifactsByDuty = new Map<string, ArtifactGroup>()
    for (const artifact of artifacts) {
      for (const dutyId of artifact.dutyIds) artifactsByDuty.set(dutyId, artifact)
    }
    const filesByPersonAndArtifact = new Set<string>()
    const filesByPerson = new Map<string, number>()
    for (const file of files) {
      if (!file.personId) continue
      filesByPerson.set(file.personId, (filesByPerson.get(file.personId) ?? 0) + 1)
      if (file.artifactId) filesByPersonAndArtifact.add(`${file.personId}|${file.artifactId}`)
    }

    return people.map((person) => {
      const position = this.positionById(person.positionId)
      const duties = position?.duties ?? []
      const expected = duties.filter((duty) => artifactsByDuty.has(duty.id)).length
      let done = 0
      for (const duty of duties) {
        const artifact = artifactsByDuty.get(duty.id)
        if (artifact && filesByPersonAndArtifact.has(`${person.id}|${artifact.id}`)) done++
      }
      return {
        personId: person.id,
        personName: person.name,
        positionId: person.positionId,
        positionName: position?.name ?? '',
        level: position?.level ?? 'specialist_staff',
        files: filesByPerson.get(person.id) ?? 0,
        expected,
        done,
        percent: expected === 0 ? 0 : Math.round((done / expected) * 1000) / 10,
      }
    })
  }

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

// The task list lives beside the rest of the store; mixing it in keeps the
// task operations in their own file.
Object.assign(Store.prototype, taskStore)

/**
 * The tasks of a period with the names resolved, for the views.
 */
Store.prototype.taskViews = function taskViews(
  this: Store,
  periodType: string,
  periodKey: string,
  departmentId?: string,
): TaskView[] {
  const tasks = this.tasks(periodType, periodKey, departmentId)
  const people = this.people(departmentId)
  const positions = this.positions(departmentId)
  const duties = new Map<string, string>()
  for (const position of positions) {
    for (const duty of this.duties(position.id)) duties.set(duty.id, duty.text)
  }
  const artifacts = new Map(this.artifacts().map((a) => [a.id, a.artifact]))
  const nameOf = (id: string | null): string =>
    id ? (people.find((p) => p.id === id)?.name ?? '') : ''

  return tasks.map((task) => ({
    ...task,
    personName: nameOf(task.personId),
    positionName: positions.find((p) => p.id === task.positionId)?.name ?? '',
    assignedByName: nameOf(task.assignedById),
    dutyText: task.dutyId ? (duties.get(task.dutyId) ?? '') : '',
    artifactName: task.artifactId ? (artifacts.get(task.artifactId) ?? '') : '',
  }))
}

/**
 * How the tasks of a period stand, for the dashboard.
 */
Store.prototype.taskProgress = function taskProgress(
  this: Store,
  periodType: string,
  periodKey: string,
  departmentId?: string,
): {
  positionId: string
  positionName: string
  level: string
  total: number
  done: number
  blocked: number
  percent: number
}[] {
  const tasks = this.tasks(periodType, periodKey, departmentId)
  const positions = this.positions(departmentId)
  return positions.map((position) => {
    const own = tasks.filter((t) => t.positionId === position.id)
    const done = own.filter((t) => t.status === 'done').length
    const blocked = own.filter((t) => t.status === 'blocked').length
    return {
      positionId: position.id,
      positionName: position.name,
      level: position.level,
      total: own.length,
      done,
      blocked,
      percent: own.length === 0 ? 0 : Math.round((done / own.length) * 1000) / 10,
    }
  })
}
