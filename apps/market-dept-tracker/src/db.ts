import { DatabaseSync } from 'node:sqlite'
import { mkdirSync, readFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { createHash, randomBytes } from 'node:crypto'
import type { Department, Duty, Person, Position } from './types.ts'

/**
 * Storage for the tracker. Everything lives in one SQLite file so the whole
 * platform can be copied between machines as a single artifact.
 */

export interface Database {
  raw: DatabaseSync
  close(): void
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS departments (
  id         TEXT PRIMARY KEY,
  code       TEXT NOT NULL UNIQUE,
  name       TEXT NOT NULL,
  name_en    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS positions (
  id              TEXT PRIMARY KEY,
  department_id   TEXT NOT NULL REFERENCES departments(id),
  name            TEXT NOT NULL,
  role            TEXT NOT NULL DEFAULT '',
  level           TEXT NOT NULL,
  parent_id       TEXT REFERENCES positions(id),
  headcount_note  TEXT NOT NULL DEFAULT '',
  instructions    TEXT NOT NULL DEFAULT '',
  boundary        TEXT NOT NULL DEFAULT '',
  note            TEXT NOT NULL DEFAULT '',
  ordinal         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS duties (
  id           TEXT PRIMARY KEY,
  position_id  TEXT NOT NULL REFERENCES positions(id),
  ordinal      INTEGER NOT NULL,
  text         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
  id             TEXT PRIMARY KEY,
  department_id  TEXT NOT NULL REFERENCES departments(id),
  name           TEXT NOT NULL,
  position_id    TEXT NOT NULL REFERENCES positions(id),
  token          TEXT NOT NULL UNIQUE,
  pending        INTEGER NOT NULL DEFAULT 0,
  note           TEXT NOT NULL DEFAULT ''
);

-- One login per post. Everyone in the post shares the same account.
CREATE TABLE IF NOT EXISTS accounts (
  id             TEXT PRIMARY KEY,
  position_id    TEXT NOT NULL REFERENCES positions(id),
  username       TEXT NOT NULL UNIQUE,
  password_hash  TEXT NOT NULL,
  password_plain TEXT NOT NULL DEFAULT '',
  token          TEXT NOT NULL UNIQUE,
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id      TEXT NOT NULL REFERENCES people(id),
  period_type    TEXT NOT NULL,
  period_key     TEXT NOT NULL,
  period_label   TEXT NOT NULL,
  summary        TEXT NOT NULL DEFAULT '',
  blockers       TEXT NOT NULL DEFAULT '',
  next_plan      TEXT NOT NULL DEFAULT '',
  submitted_at   TEXT NOT NULL,
  UNIQUE (person_id, period_type, period_key)
);

CREATE TABLE IF NOT EXISTS report_items (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  report_id   INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  duty_id     TEXT NOT NULL REFERENCES duties(id),
  status      TEXT NOT NULL,
  percent     INTEGER,
  note        TEXT NOT NULL DEFAULT ''
);

-- What a post is expected to produce, and the words that identify the output.
CREATE TABLE IF NOT EXISTS artifacts (
  id           TEXT PRIMARY KEY,
  position_id  TEXT NOT NULL REFERENCES positions(id),
  artifact     TEXT NOT NULL,
  keywords     TEXT NOT NULL,
  duty_ids     TEXT NOT NULL,
  note         TEXT NOT NULL DEFAULT ''
);

-- A file the collector found in the shared folder.
CREATE TABLE IF NOT EXISTS collected_files (
  id           TEXT PRIMARY KEY,
  period_type  TEXT NOT NULL,
  period_key   TEXT NOT NULL,
  name         TEXT NOT NULL,
  owner_name   TEXT NOT NULL DEFAULT '',
  owner_id     TEXT NOT NULL DEFAULT '',
  modified_at  TEXT NOT NULL,
  location     TEXT NOT NULL DEFAULT '',
  person_id    TEXT REFERENCES people(id),
  artifact_id  TEXT REFERENCES artifacts(id),
  matched_keyword TEXT NOT NULL DEFAULT '',
  reason       TEXT NOT NULL DEFAULT '',
  collected_at TEXT NOT NULL
);

-- A fixed task that repeats every week or every month for a whole post.
CREATE TABLE IF NOT EXISTS recurring_tasks (
  id             TEXT PRIMARY KEY,
  department_id  TEXT NOT NULL REFERENCES departments(id),
  position_id    TEXT NOT NULL REFERENCES positions(id),
  title          TEXT NOT NULL,
  detail         TEXT NOT NULL DEFAULT '',
  cadence        TEXT NOT NULL,
  duty_id        TEXT REFERENCES duties(id),
  artifact_id    TEXT REFERENCES artifacts(id),
  created_at     TEXT NOT NULL
);

-- The company leaders who may see the whole department, beyond the manager
-- and the deputy manager.
CREATE TABLE IF NOT EXISTS leader_grants (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  granted_by  TEXT NOT NULL DEFAULT '',
  granted_at  TEXT NOT NULL
);

-- One occurrence of a task, for one person and one period.
CREATE TABLE IF NOT EXISTS tasks (
  id                TEXT PRIMARY KEY,
  department_id     TEXT NOT NULL REFERENCES departments(id),
  kind              TEXT NOT NULL,
  title             TEXT NOT NULL,
  detail            TEXT NOT NULL DEFAULT '',
  person_id         TEXT NOT NULL REFERENCES people(id),
  position_id       TEXT NOT NULL REFERENCES positions(id),
  period_type       TEXT NOT NULL,
  period_key        TEXT NOT NULL,
  period_label      TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'open',
  assigned_by_id    TEXT REFERENCES people(id),
  recurring_task_id TEXT REFERENCES recurring_tasks(id),
  duty_id           TEXT REFERENCES duties(id),
  artifact_id       TEXT REFERENCES artifacts(id),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  note              TEXT NOT NULL DEFAULT '',
  UNIQUE (person_id, period_type, period_key, recurring_task_id)
);

CREATE INDEX IF NOT EXISTS idx_duties_position ON duties(position_id);
CREATE INDEX IF NOT EXISTS idx_people_position ON people(position_id);
CREATE INDEX IF NOT EXISTS idx_reports_period ON reports(period_type, period_key);
CREATE INDEX IF NOT EXISTS idx_report_items_report ON report_items(report_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_position ON artifacts(position_id);
CREATE INDEX IF NOT EXISTS idx_collected_files_period ON collected_files(period_type, period_key);
CREATE INDEX IF NOT EXISTS idx_recurring_tasks_position ON recurring_tasks(position_id);
CREATE INDEX IF NOT EXISTS idx_tasks_period ON tasks(period_type, period_key);
CREATE INDEX IF NOT EXISTS idx_tasks_person ON tasks(person_id);
CREATE INDEX IF NOT EXISTS idx_tasks_position ON tasks(position_id);
`

export function openDatabase(file: string): Database {
  if (file !== ':memory:') mkdirSync(dirname(file), { recursive: true })
  const raw = new DatabaseSync(file)
  raw.exec('PRAGMA journal_mode = WAL')
  raw.exec('PRAGMA foreign_keys = ON')
  raw.exec(SCHEMA)
  return { raw, close: () => raw.close() }
}

/** Turn a Chinese or mixed name into a URL-safe slug. */
export function slugify(input: string): string {
  const ascii = input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  if (ascii) return ascii
  // No latin characters: fall back to a stable hash of the name.
  return 'p-' + createHash('sha1').update(input).digest('hex').slice(0, 10)
}

/** A short, unguessable token used in a person's own report link. */
export function makeToken(): string {
  return randomBytes(16).toString('hex')
}

export interface SeedFile {
  departments: {
    code: string
    name: string
    name_en: string
    positions: {
      name: string
      role: string
      level: string
      parent: string | null
      headcount_note: string
      instructions: string
      boundary: string
      note?: string
      duties: string[]
      members: string[]
    }[]
    pending?: { name: string; note: string }[]
  }[]
}

/** The expected outputs, as a separate file beside the responsibility table. */
export interface ArtifactFile {
  positions: Record<
    string,
    {
      duties: number[]
      artifact: string
      keywords: string[]
      note?: string
    }[]
  >
}

function readSeedFile(path: string): SeedFile {
  return JSON.parse(readFileSync(path, 'utf8')) as SeedFile
}

/**
 * Load the responsibility table into the database.
 *
 * Positions and people keep stable ids derived from their names, so re-seeding
 * an existing database keeps the links people already have. Reports and people
 * are preserved: the seed only fills in what is missing.
 */
export function seedDatabase(db: Database, seedPath: string): { departments: number; positions: number; people: number; duties: number } {
  const seed = readSeedFile(seedPath)
  const counts = { departments: 0, positions: 0, people: 0, duties: 0 }

  const insertDept = db.raw.prepare(
    'INSERT INTO departments (id, code, name, name_en) VALUES (?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET name = excluded.name, name_en = excluded.name_en',
  )
  const insertPosition = db.raw.prepare(
    `INSERT INTO positions (id, department_id, name, role, level, parent_id, headcount_note, instructions, boundary, note, ordinal)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT (id) DO UPDATE SET
       name = excluded.name, role = excluded.role, level = excluded.level, parent_id = excluded.parent_id,
       headcount_note = excluded.headcount_note, instructions = excluded.instructions,
       boundary = excluded.boundary, note = excluded.note, ordinal = excluded.ordinal`,
  )
  const insertDuty = db.raw.prepare(
    'INSERT INTO duties (id, position_id, ordinal, text) VALUES (?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET ordinal = excluded.ordinal, text = excluded.text',
  )
  const insertPerson = db.raw.prepare(
    `INSERT INTO people (id, department_id, name, position_id, token, pending, note)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT (id) DO UPDATE SET name = excluded.name, position_id = excluded.position_id, pending = excluded.pending, note = excluded.note`,
  )
  const selectPerson = db.raw.prepare('SELECT id, token FROM people WHERE id = ?')
  const selectDuty = db.raw.prepare('SELECT id FROM duties WHERE id = ?')

  for (const dept of seed.departments) {
    const deptId = slugify(dept.code)
    insertDept.run(deptId, dept.code, dept.name, dept.name_en)
    counts.departments++

    // Position ids come from the name, but must be unique inside the
    // department, so a repeated name gets a numeric suffix.
    const usedIds = new Map<string, number>()
    const positionId = (name: string): string => {
      const base = slugify(name)
      const seen = usedIds.get(base) ?? 0
      usedIds.set(base, seen + 1)
      return seen === 0 ? base : `${base}-${seen + 1}`
    }

    const ids = new Map<string, string>()
    for (const p of dept.positions) ids.set(p.name, positionId(p.name))

    for (const [index, p] of dept.positions.entries()) {
      const id = ids.get(p.name)!
      insertPosition.run(
        id, deptId, p.name, p.role ?? p.name, p.level, p.parent ? (ids.get(p.parent) ?? null) : null,
        p.headcount_note ?? '', p.instructions ?? '', p.boundary ?? '', p.note ?? '', index,
      )
      counts.positions++

      for (const [ordinal, text] of p.duties.entries()) {
        const dutyId = `${id}#${ordinal + 1}`
        // The check runs before the write, so the count says how many duties
        // this call added rather than how many the database holds.
        const existed = selectDuty.get(dutyId)
        insertDuty.run(dutyId, id, ordinal + 1, text)
        if (!existed) counts.duties++
      }

      for (const name of p.members) {
        const personId = slugify(name)
        const existing = selectPerson.get(personId) as { id: string; token: string } | undefined
        const token = existing?.token ?? makeToken()
        insertPerson.run(personId, deptId, name, id, token, 0, '')
        counts.people++
      }
    }

    // People whose placement the source table left open still need a slot to
    // report against, so they land in a holding position under the manager.
    for (const p of dept.pending ?? []) {
      const personId = slugify(p.name)
      const existing = selectPerson.get(personId) as { id: string; token: string } | undefined
      if (existing) {
        db.raw.prepare('UPDATE people SET note = ? WHERE id = ?').run(p.note, personId)
        continue
      }
      const holderId = `${deptId}-pending`
      insertPosition.run(holderId, deptId, '待定岗人员', '待定岗', 'specialist_staff', null, '', '', '', '', 9999)
      insertPerson.run(personId, deptId, p.name, holderId, makeToken(), 1, p.note)
      counts.people++
    }
  }

  return counts
}

/**
 * Load the people who may see the whole department.
 *
 * The market-department table leaves the manager and the deputy manager posts
 * empty, and the company leaders are not in it at all, so they are named here.
 */
export function seedLeaders(db: Database, leaderPath: string): number {
  const file = JSON.parse(readFileSync(leaderPath, 'utf8')) as {
    leaders: { name: string; id?: string; granted_by?: string }[]
  }
  const insert = db.raw.prepare(
    `INSERT INTO leader_grants (id, name, granted_by, granted_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT (id) DO UPDATE SET name = excluded.name, granted_by = excluded.granted_by`,
  )
  const now = new Date().toISOString()
  let count = 0
  for (const leader of file.leaders) {
    const id = leader.id ?? slugify(leader.name)
    insert.run(id, leader.name, leader.granted_by ?? '', now)
    count++
  }
  return count
}

/**
 * Load the fixed tasks into the database.
 *
 * Each entry names the post it belongs to, what has to be done, how often it
 * repeats, and the duty it serves. The ids are stable, so re-running this does
 * not disturb the tasks already generated from them.
 */
export function seedFixedTasks(db: Database, taskPath: string): number {
  const file = JSON.parse(readFileSync(taskPath, 'utf8')) as {
    positions: Record<
      string,
      { title: string; detail: string; cadence: string; duty?: number }[]
    >
  }
  const department = db.raw.prepare('SELECT id FROM departments ORDER BY code LIMIT 1').get() as
    | { id: string }
    | undefined
  if (!department) return 0

  const selectPosition = db.raw.prepare('SELECT id FROM positions WHERE name = ? AND department_id = ?')
  const insert = db.raw.prepare(
    `INSERT INTO recurring_tasks (id, department_id, position_id, title, detail, cadence, duty_id, artifact_id, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT (id) DO UPDATE SET
       title = excluded.title, detail = excluded.detail, cadence = excluded.cadence, duty_id = excluded.duty_id`,
  )
  const now = new Date().toISOString()
  let count = 0
  for (const [positionName, entries] of Object.entries(file.positions)) {
    const position = selectPosition.get(positionName, department.id) as { id: string } | undefined
    if (!position) continue
    for (const [index, entry] of entries.entries()) {
      const id = `${position.id}--t${index + 1}`
      const dutyId = entry.duty ? `${position.id}#${entry.duty}` : null
      insert.run(
        id, department.id, position.id, entry.title, entry.detail ?? '',
        entry.cadence, dutyId, null, now,
      )
      count++
    }
  }
  return count
}
/**
 * Load the expected outputs into the database.
 *
 * Each group names the post it belongs to, the duties it answers for, and
 * the words that identify the output in a file name. The groups keep stable
 * ids, so re-running this does not disturb what the collector has matched.
 */
export function seedArtifacts(db: Database, artifactPath: string): number {
  const file = JSON.parse(readFileSync(artifactPath, 'utf8')) as ArtifactFile
  const insert = db.raw.prepare(
    `INSERT INTO artifacts (id, position_id, artifact, keywords, duty_ids, note)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT (id) DO UPDATE SET
       position_id = excluded.position_id, artifact = excluded.artifact,
       keywords = excluded.keywords, duty_ids = excluded.duty_ids, note = excluded.note`,
  )
  const selectPosition = db.raw.prepare('SELECT id FROM positions WHERE name = ? AND department_id = ?')
  const department = db.raw.prepare('SELECT id FROM departments ORDER BY code LIMIT 1').get() as
    | { id: string }
    | undefined
  if (!department) return 0

  let count = 0
  for (const [positionName, groups] of Object.entries(file.positions)) {
    const position = selectPosition.get(positionName, department.id) as { id: string } | undefined
    if (!position) continue
    for (const [index, group] of groups.entries()) {
      const id = `${position.id}--a${index + 1}`
      const dutyIds = group.duties.map((ordinal) => `${position.id}#${ordinal}`)
      insert.run(id, position.id, group.artifact, JSON.stringify(group.keywords), JSON.stringify(dutyIds), group.note ?? '')
      count++
    }
  }
  return count
}
