import type { PeriodType } from './types.ts'

/**
 * ISO-8601 week helpers. A reporting week starts on Monday and the week key
 * is the ISO year plus the ISO week number, e.g. `2026-W45`.
 */

export function isoWeekKey(date: Date): string {
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
  // Shift to the Thursday of this week; its year decides the ISO year.
  const day = target.getUTCDay() || 7
  target.setUTCDate(target.getUTCDate() + 4 - day)
  const year = target.getUTCFullYear()
  const yearStart = new Date(Date.UTC(year, 0, 1))
  const week = Math.ceil(((target.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
  return `${year}-W${String(week).padStart(2, '0')}`
}

export function isoWeekLabel(key: string): string {
  const [year, week] = key.split('-W')
  return `${year}年第${Number(week)}周`
}

export function monthKey(date: Date): string {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`
}

export function monthLabel(key: string): string {
  const [year, month] = key.split('-')
  return `${year}年${Number(month)}月`
}

/** Build the key and label for a period type at a given date. */
export function periodFor(type: PeriodType, date: Date): { key: string; label: string } {
  return type === 'week'
    ? { key: isoWeekKey(date), label: isoWeekLabel(isoWeekKey(date)) }
    : { key: monthKey(date), label: monthLabel(monthKey(date)) }
}

/** The Monday of the week containing `date`. */
export function weekStart(date: Date): Date {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
  const day = d.getUTCDay() || 7
  d.setUTCDate(d.getUTCDate() - (day - 1))
  return d
}

/** The Sunday of the week containing `date`. */
export function weekEnd(date: Date): Date {
  const start = weekStart(date)
  const end = new Date(start)
  end.setUTCDate(end.getUTCDate() + 6)
  return end
}

/** The first and last day of the month containing `date`. */
export function monthRange(date: Date): { start: Date; end: Date } {
  const start = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1))
  const end = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0))
  return { start, end }
}

/** The date range a period covers, for display in a form. */
export function periodRange(type: PeriodType, date: Date): { start: string; end: string } {
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  if (type === 'week') return { start: iso(weekStart(date)), end: iso(weekEnd(date)) }
  const { start, end } = monthRange(date)
  return { start: iso(start), end: iso(end) }
}

/**
 * The periods a user can pick, newest first: the current one plus the
 * previous ones, so a late report can still be filed.
 */
export function recentPeriods(type: PeriodType, now: Date, count: number): { key: string; label: string }[] {
  const out: { key: string; label: string }[] = []
  const seen = new Set<string>()
  const cursor = new Date(now.getTime())
  for (let i = 0; i < count * 14 && out.length < count; i++) {
    const { key, label } = periodFor(type, cursor)
    if (!seen.has(key)) {
      seen.add(key)
      out.push({ key, label })
    }
    cursor.setUTCDate(cursor.getUTCDate() - 7)
  }
  return out
}
