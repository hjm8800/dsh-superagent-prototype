import type { Database } from './db.ts'
import type {
  PeriodType,
  RecurringTask,
  Task,
  TaskKind,
  TaskPhoto,
  TaskStatus,
  TaskView,
} from './types.ts'

/**
 * The task lists.
 *
 * Two kinds of task live here: one a superior gives a subordinate, and a fixed
 * task that repeats every week or every month for a whole post.
 */

/**
 * What the task operations need from the store.
 *
 * The store class declares these methods and mixes this object into it, so the
 * operations can call each other and the store's reading methods.
 */
interface TaskStore {
  db: Database
  recurringTasks(departmentId?: string): RecurringTask[]
  createRecurringTask(input: {
    id: string
    departmentId: string
    positionId: string
    title: string
    detail: string
    cadence: PeriodType
    dutyId: string | null
    artifactId: string | null
    createdAt: string
  }): void
  tasks(periodType: string, periodKey: string, departmentId?: string): Task[]
  createTask(input: {
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
  }): void
  updateTask(id: string, status: TaskStatus, note: string, updatedAt: string): void
  task(id: string): Task | undefined
  taskPhotos(taskId: string): TaskPhoto[]
  photoById(id: string): TaskPhoto | undefined
  createTaskPhoto(photo: TaskPhoto): void
  materialiseRecurringTasks(
    departmentId: string,
    periodType: PeriodType,
    periodKey: string,
    periodLabel: string,
    now: string,
  ): number
}

/** A row of the recurring_tasks table, before it is mapped to the model. */
interface RecurringTaskRow {
  id: string
  department_id: string
  position_id: string
  title: string
  detail: string
  cadence: string
  duty_id: string | null
  artifact_id: string | null
  created_at: string
}

/** A row of the tasks table, before it is mapped to the model. */
interface TaskRow {
  id: string
  department_id: string
  kind: string
  title: string
  detail: string
  person_id: string
  position_id: string
  period_type: string
  period_key: string
  period_label: string
  status: string
  assigned_by_id: string | null
  recurring_task_id: string | null
  duty_id: string | null
  artifact_id: string | null
  created_at: string
  updated_at: string
  note: string
}

interface TaskPhotoRow {
  id: string
  task_id: string
  person_id: string
  filename: string
  mime: string
  size: number
  created_at: string
}

/** The task list operations, mixed into the store. */
export const taskStore = {
  /** The fixed tasks that repeat for a post. */
  recurringTasks(this: TaskStore, departmentId?: string): RecurringTask[] {
    const rows = this.db.raw
      .prepare(
        `SELECT id, department_id, position_id, title, detail, cadence, duty_id, artifact_id, created_at
         FROM recurring_tasks ${departmentId ? 'WHERE department_id = ?' : ''} ORDER BY position_id, id`,
      )
      .all(...(departmentId ? [departmentId] : [])) as unknown as RecurringTaskRow[]
    return rows.map((r) => ({
      id: r.id,
      departmentId: r.department_id,
      positionId: r.position_id,
      title: r.title,
      detail: r.detail,
      cadence: r.cadence as PeriodType,
      dutyId: r.duty_id,
      artifactId: r.artifact_id,
      createdAt: r.created_at,
    }))
  },

  /** Set up a fixed task for a post. */
  createRecurringTask(
    this: TaskStore,
    input: {
      id: string
      departmentId: string
      positionId: string
      title: string
      detail: string
      cadence: PeriodType
      dutyId: string | null
      artifactId: string | null
      createdAt: string
    },
  ): void {
    this.db.raw
      .prepare(
        `INSERT INTO recurring_tasks (id, department_id, position_id, title, detail, cadence, duty_id, artifact_id, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (id) DO UPDATE SET
           title = excluded.title, detail = excluded.detail, cadence = excluded.cadence,
           duty_id = excluded.duty_id, artifact_id = excluded.artifact_id`,
      )
      .run(
        input.id, input.departmentId, input.positionId, input.title, input.detail,
        input.cadence, input.dutyId, input.artifactId, input.createdAt,
      )
  },

  /** The tasks of a period. */
  tasks(this: TaskStore, periodType: string, periodKey: string, departmentId?: string): Task[] {
    const rows = this.db.raw
      .prepare(
        `SELECT id, department_id, kind, title, detail, person_id, position_id, period_type, period_key,
                period_label, status, assigned_by_id, recurring_task_id, duty_id, artifact_id,
                created_at, updated_at, note
         FROM tasks
         WHERE period_type = ? AND period_key = ? ${departmentId ? 'AND department_id = ?' : ''}
         ORDER BY position_id, person_id, id`,
      )
      .all(...(departmentId ? [periodType, periodKey, departmentId] : [periodType, periodKey])) as unknown as TaskRow[]
    return rows.map((r) => ({
      id: r.id,
      departmentId: r.department_id,
      kind: r.kind as TaskKind,
      title: r.title,
      detail: r.detail,
      personId: r.person_id,
      positionId: r.position_id,
      periodType: r.period_type as PeriodType,
      periodKey: r.period_key,
      periodLabel: r.period_label,
      status: r.status as TaskStatus,
      assignedById: r.assigned_by_id,
      recurringTaskId: r.recurring_task_id,
      dutyId: r.duty_id,
      artifactId: r.artifact_id,
      createdAt: r.created_at,
      updatedAt: r.updated_at,
      note: r.note,
    }))
  },

  /**
   * Give a task to a person.
   *
   * The task lands in the period it was asked for, and with the person it is
   * for.
   */
  createTask(
    this: TaskStore,
    input: {
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
    },
  ): void {
    this.db.raw
      .prepare(
        `INSERT INTO tasks (id, department_id, kind, title, detail, person_id, position_id, period_type, period_key,
                            period_label, status, assigned_by_id, recurring_task_id, duty_id, artifact_id,
                            created_at, updated_at, note)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, '')
         ON CONFLICT (id) DO UPDATE SET
           title = excluded.title, detail = excluded.detail, period_type = excluded.period_type,
           period_key = excluded.period_key, period_label = excluded.period_label,
           assigned_by_id = excluded.assigned_by_id, updated_at = excluded.updated_at`,
      )
      .run(
        input.id, input.departmentId, input.kind, input.title, input.detail, input.personId, input.positionId,
        input.periodType, input.periodKey, input.periodLabel, input.assignedById, input.recurringTaskId,
        input.dutyId, input.artifactId, input.createdAt, input.createdAt,
      )
  },

  /** Move a task on, and let the person say how it went. */
  updateTask(
    this: TaskStore,
    id: string,
    status: TaskStatus,
    note: string,
    updatedAt: string,
  ): void {
    this.db.raw
      .prepare('UPDATE tasks SET status = ?, note = ?, updated_at = ? WHERE id = ?')
      .run(status, note, updatedAt, id)
  },

  /** One task, for the person who owns it. */
  task(this: TaskStore, id: string): Task | undefined {
    const row = this.db.raw
      .prepare(
        `SELECT id, department_id, kind, title, detail, person_id, position_id, period_type, period_key,
                period_label, status, assigned_by_id, recurring_task_id, duty_id, artifact_id,
                created_at, updated_at, note
         FROM tasks WHERE id = ?`,
      )
      .get(id) as unknown as TaskRow | undefined
    if (!row) return undefined
    return {
      id: row.id,
      departmentId: row.department_id,
      kind: row.kind as TaskKind,
      title: row.title,
      detail: row.detail,
      personId: row.person_id,
      positionId: row.position_id,
      periodType: row.period_type as PeriodType,
      periodKey: row.period_key,
      periodLabel: row.period_label,
      status: row.status as TaskStatus,
      assignedById: row.assigned_by_id,
      recurringTaskId: row.recurring_task_id,
      dutyId: row.duty_id,
      artifactId: row.artifact_id,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      note: row.note,
    }
  },

  /** The photos attached to a task. */
  taskPhotos(this: TaskStore, taskId: string): TaskPhoto[] {
    const rows = this.db.raw
      .prepare(
        'SELECT id, task_id, person_id, filename, mime, size, created_at FROM task_photos WHERE task_id = ? ORDER BY created_at, id',
      )
      .all(taskId) as unknown as TaskPhotoRow[]
    return rows.map((row) => ({
      id: row.id,
      taskId: row.task_id,
      personId: row.person_id,
      filename: row.filename,
      mime: row.mime,
      size: row.size,
      createdAt: row.created_at,
    }))
  },

  createTaskPhoto(this: TaskStore, photo: TaskPhoto): void {
    this.db.raw
      .prepare(
        'INSERT INTO task_photos (id, task_id, person_id, filename, mime, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
      )
      .run(photo.id, photo.taskId, photo.personId, photo.filename, photo.mime, photo.size, photo.createdAt)
  },

  /** One photo, by id, for serving the file. */
  photoById(this: TaskStore, id: string): TaskPhoto | undefined {
    const row = this.db.raw
      .prepare('SELECT id, task_id, person_id, filename, mime, size, created_at FROM task_photos WHERE id = ?')
      .get(id) as unknown as TaskPhotoRow | undefined
    if (!row) return undefined
    return {
      id: row.id,
      taskId: row.task_id,
      personId: row.person_id,
      filename: row.filename,
      mime: row.mime,
      size: row.size,
      createdAt: row.created_at,
    }
  },

  /**
   * Make sure the fixed tasks of a period exist.
   *
   * Every member of a post gets every fixed task of that post, once. Running
   * this again adds nothing, so it is safe to call whenever a period is opened.
   */
  materialiseRecurringTasks(
    this: TaskStore,
    departmentId: string,
    periodType: PeriodType,
    periodKey: string,
    periodLabel: string,
    now: string,
  ): number {
    const recurring = this.recurringTasks(departmentId).filter((task) => task.cadence === periodType)
    if (recurring.length === 0) return 0
    const people = this.db.raw
      .prepare('SELECT id, position_id FROM people WHERE department_id = ?')
      .all(departmentId) as unknown as { id: string; position_id: string }[]
    let created = 0
    for (const fixed of recurring) {
      const members = people.filter((p) => p.position_id === fixed.positionId)
      for (const member of members) {
        const id = `${fixed.id}::${member.id}::${periodKey}`
        const existing = this.db.raw.prepare('SELECT id FROM tasks WHERE id = ?').get(id)
        if (existing) continue
        this.createTask({
          id,
          departmentId,
          kind: 'recurring',
          title: fixed.title,
          detail: fixed.detail,
          personId: member.id,
          positionId: fixed.positionId,
          periodType,
          periodKey,
          periodLabel,
          assignedById: null,
          recurringTaskId: fixed.id,
          dutyId: fixed.dutyId,
          artifactId: fixed.artifactId,
          createdAt: now,
        })
        created++
      }
    }
    return created
  },
}
