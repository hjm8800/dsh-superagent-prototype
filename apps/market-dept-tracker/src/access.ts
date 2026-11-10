import type { DatabaseSync } from 'node:sqlite'
import type { AccessRole, LeaderGrant, Person, Position } from './types.ts'

/**
 * Who may see what.
 *
 * A person in a post sees only their own work: their own tasks and their own
 * report. The manager, the deputy manager and the company leaders the
 * department head names see the whole department.
 */

/** The posts that always see the whole department. */
const LEADER_LEVELS = new Set(['manager', 'deputy_manager'])

/** The people who may see the whole department beyond the two leaders. */
export function leaderGrants(db: DatabaseSync): LeaderGrant[] {
  const rows = db
    .prepare('SELECT id, name, granted_by, granted_at FROM leader_grants ORDER BY granted_at, id')
    .all() as unknown as { id: string; name: string; granted_by: string; granted_at: string }[]
  return rows.map((r) => ({
    id: r.id,
    name: r.name,
    grantedBy: r.granted_by,
    grantedAt: r.granted_at,
  }))
}

/** Give a person the right to see the whole department. */
export function grantLeaderAccess(
  db: DatabaseSync,
  grant: { id: string; name: string; grantedBy: string; grantedAt: string },
): void {
  db
    .prepare(
      `INSERT INTO leader_grants (id, name, granted_by, granted_at)
       VALUES (?, ?, ?, ?)
       ON CONFLICT (id) DO UPDATE SET name = excluded.name, granted_by = excluded.granted_by`,
    )
    .run(grant.id, grant.name, grant.grantedBy, grant.grantedAt)
}

/** Take that right away. */
export function revokeLeaderAccess(db: DatabaseSync, id: string): void {
  db.prepare('DELETE FROM leader_grants WHERE id = ?').run(id)
}

/**
 * What a person may see.
 *
 * A manager or a deputy manager sees everything. So does anyone the company
 * leaders name. Everyone else sees only their own work.
 */
export function accessOf(
  person: Person | undefined,
  position: Position | undefined,
  leaders: LeaderGrant[],
): AccessRole {
  if (!person) return 'member'
  if (position && LEADER_LEVELS.has(position.level)) return 'leader'
  if (leaders.some((leader) => leader.id === person.id)) return 'leader'
  return 'member'
}
