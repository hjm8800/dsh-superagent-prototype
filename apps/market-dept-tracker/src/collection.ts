import type { Store } from './store.ts'
import { isOutcomeFile, matchArtifact, matchPerson, periodOf, type FileSource } from './collector.ts'
import type { CollectionResult, PeriodType } from './types.ts'

/**
 * The collection run: read the shared folder, recognize what each file is,
 * and store the result so the dashboard can be built from it.
 */

/**
 * One file the collector read, and what it was credited to.
 */
interface CollectedRecord {
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

export interface CollectOptions {
  /** Which period to collect for, or `undefined` to take it from the files. */
  periodType: PeriodType
  /** The period to collect for; the files' own dates decide it when absent. */
  periodKey?: string
  /** The label of the period, filled in when the files decide it. */
  periodLabel?: string
  /** When the collection runs; the caller fixes it in tests. */
  now?: Date
}

export interface CollectOutcome extends CollectionResult {
  /** Files that were recognized and credited. */
  credited: number
  /** Files that were read but could not be credited. */
  uncredited: number
  /** Files that were not outcome files and were ignored. */
  ignored: number
}

/**
 * Read the shared folder and store what it finds.
 *
 * A file is credited when the collector can tell whose it is and which
 * output it is. A file it cannot place is kept and listed, so nothing the
 * team filed disappears without an explanation.
 */
export async function collect(
  store: Store,
  source: FileSource,
  options: CollectOptions,
): Promise<CollectOutcome> {
  const now = options.now ?? new Date()
  const artifacts = store.artifacts()
  const people = store.people()
  const files = await source.list()

  let credited = 0
  let uncredited = 0
  let ignored = 0

  // When the caller names a period, every file counts towards it. When it
  // does not, each file counts towards the period it was last changed in, so
  // a file from last week lands in last week's numbers.
  const byPeriod = new Map<string, CollectedRecord[]>()
  const recordFor = (periodKey: string): CollectedRecord[] => {
    let list = byPeriod.get(periodKey)
    if (!list) {
      list = []
      byPeriod.set(periodKey, list)
    }
    return list
  }

  for (const file of files) {
    if (!isOutcomeFile(file.name)) {
      ignored++
      continue
    }
    const person = matchPerson(file.name, file.ownerName, people)
    const artifact = matchArtifact(file.name, artifacts, person?.positionId)
    let reason = ''
    if (!person && !artifact) reason = '认不出是谁的什么产出'
    else if (!person) reason = '认得出产出，但认不出是谁交的'
    else if (!artifact) reason = '认得出是谁交的，但文件名里没有对应的产出'

    if (person && artifact) credited++
    else uncredited++

    const own = options.periodKey
      ? { key: options.periodKey, label: options.periodLabel ?? options.periodKey }
      : periodOf(file.modifiedAt, options.periodType)

    recordFor(own.key).push({
      id: `${source.name}:${file.sourceId}`,
      name: file.name,
      ownerName: file.ownerName,
      modifiedAt: file.modifiedAt,
      location: file.location,
      personId: person?.id ?? null,
      artifactId: artifact?.artifact.id ?? null,
      matchedKeyword: artifact?.keyword ?? '',
      reason,
    })
  }

  const collectedAt = now.toISOString()
  for (const [periodKey, list] of byPeriod) {
    store.saveCollectedFiles(options.periodType, periodKey, list, collectedAt)
  }

  const records = [...byPeriod.values()].flat()
  const matched = records.filter((r) => r.personId && r.artifactId)
  const unmatched = records.filter((r) => !(r.personId && r.artifactId))

  // The period the run belongs to: the one the caller asked for, or the one
  // the files themselves fall in.
  const periods = [...byPeriod.keys()]
  const primary = options.periodKey ?? periods[0] ?? ''
  const primaryLabel = options.periodLabel ?? periodOf(now.toISOString(), options.periodType).label

  return {
    periodType: options.periodType,
    periodKey: primary,
    periodLabel: primaryLabel,
    matches: matched.map((r) => ({
      file: { sourceId: r.id, name: r.name, ownerName: r.ownerName, modifiedAt: r.modifiedAt, location: r.location },
      personId: r.personId,
      artifactId: r.artifactId,
      matchedKeyword: r.matchedKeyword,
      reason: null,
    })),
    unmatched: unmatched.map((r) => ({
      file: { sourceId: r.id, name: r.name, ownerName: r.ownerName, modifiedAt: r.modifiedAt, location: r.location },
      personId: r.personId,
      artifactId: r.artifactId,
      matchedKeyword: r.matchedKeyword,
      reason: r.reason,
    })),
    collectedAt,
    source: source.name,
    credited,
    uncredited,
    ignored,
  }
}
