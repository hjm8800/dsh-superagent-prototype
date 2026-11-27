import { openDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'

const db = openDatabase(process.argv[2]!)
const store = new Store(db)
const periodType = (process.argv[3] ?? 'week') as 'week' | 'month'
const periodKey = process.argv[4] ?? '2026-W45'

console.log('--- files and completion per person ---')
for (const p of store.artifactProgressByPerson(periodType, periodKey)) {
  if (p.files === 0 && p.expected === 0) continue
  console.log(
    `${p.personName} (${p.positionName}): ${p.files} files, ${p.done}/${p.expected} done, ${p.percent}%`,
  )
}

console.log('\n--- completion per post ---')
for (const p of store.artifactProgress(periodType, periodKey)) {
  if (p.expected === 0) continue
  console.log(
    `${p.positionName}: ${p.reported}/${p.total} people, ${p.done}/${p.expected} outputs, ${p.percent}%`,
  )
}

console.log('\n--- the files themselves ---')
for (const f of store.collectedFiles(periodType, periodKey)) {
  const person = f.personId ? store.personById(f.personId) : undefined
  console.log(`${f.name} -> ${person?.name ?? '(认不出人)'} / ${f.artifactId ?? '(认不出产出)'} ${f.reason}`)
}

db.close()
