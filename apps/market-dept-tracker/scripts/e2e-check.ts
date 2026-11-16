// End-to-end check against a running tracker: read a person's link, file a
// report through the form, and confirm it reaches the dashboard.
import { openDatabase, seedArtifacts, seedDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'
import { collect } from '../src/collection.ts'
import { DirectorySource } from '../src/collector.ts'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const BASE = process.argv[2] ?? 'http://127.0.0.1:3111'
const DATA = process.argv[3] ?? join(HERE, '..', 'data', 'tracker.db')
const SEED = join(HERE, '..', 'seed', 'market-department.json')
const ARTIFACTS = join(HERE, '..', 'seed', 'artifacts.json')

const db = openDatabase(DATA)
// The seed is idempotent and keeps the tokens people already have, so the
// links the running server shows stay valid.
seedDatabase(db, SEED)
seedArtifacts(db, ARTIFACTS)
const store = new Store(db)

// 1. The links page lists a working link for every person.
//    Each link appears twice in the page: once in the table, once in the
//    copy-all script, so the check compares the unique tokens.
const linksHtml = await (await fetch(`${BASE}/links`)).text()
const linkMatches = [...linksHtml.matchAll(/\/report\?token=([a-f0-9]{32})/g)].map((m) => m[1]!)
const uniqueTokens = new Set(linkMatches)
const people = store.people()
console.log(`links page: ${uniqueTokens.size} unique links for ${people.length} people`)
if (uniqueTokens.size !== people.length) throw new Error('link count does not match the roster')
for (const person of people) {
  if (!uniqueTokens.has(person.token)) throw new Error(`${person.name} has no link on the links page`)
}

// 2. A person's form loads and lists their duties.
const clerk = people.find((p) => p.name === '黄美琳')!
const clerkPosition = store.positionById(clerk.positionId)!
const formHtml = await (await fetch(`${BASE}/report?token=${clerk.token}`)).text()
if (!formHtml.includes(clerk.name)) throw new Error('the form does not name the person')
const dutyFields = [...formHtml.matchAll(/data-duty-id="([^"]+)"/g)].map((m) => m[1]!)
console.log(`form for ${clerk.name}: ${dutyFields.length} duty fields, position duties ${clerkPosition.duties.length}`)
if (dutyFields.length !== clerkPosition.duties.length) throw new Error('the form does not list every duty')
if (!formHtml.includes('本周小结') && !formHtml.includes('本期小结')) throw new Error('the summary section is missing')

// 3. Submitting the form stores the report and redirects back.
const form = new URLSearchParams()
form.set('token', clerk.token)
form.set('periodType', 'week')
form.set('periodKey', '2026-W45')
form.set('periodLabel', '2026年第45周')
form.set('summary', '本周完成合同台账归档与资金计划汇总')
form.set('blockers', '财务发票流转较慢')
form.set('nextPlan', '推进回款核对')
clerkPosition.duties.forEach((duty, index) => {
  const status = index === 0 ? 'done' : index === 1 ? 'in_progress' : 'not_started'
  form.set(`status:${duty.id}`, status)
  form.set(`percent:${duty.id}`, status === 'done' ? '100' : status === 'in_progress' ? '60' : '0')
  form.set(`note:${duty.id}`, status === 'done' ? '已归档' : '')
})
const response = await fetch(`${BASE}/report`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: form.toString(),
  redirect: 'manual',
})
console.log(`submit -> ${response.status} ${response.headers.get('location') ?? ''}`)
if (response.status !== 302) throw new Error('the submission did not redirect')

// 4. The report is stored with every answer.
const stored = store.reportFor(clerk.id, 'week', '2026-W45')
if (!stored) throw new Error('the report was not stored')
if (stored.items.length !== clerkPosition.duties.length) throw new Error('the stored report is missing answers')
console.log(`stored report: ${stored.items.length} items, summary="${stored.summary}"`)

// 5. The dashboard shows the person and the position progress.
const overviewHtml = await (await fetch(`${BASE}/?type=week&period=2026-W45`)).text()
if (!overviewHtml.includes(clerk.name)) throw new Error('the dashboard does not list the reporter')
if (!overviewHtml.includes('本周完成合同台账归档与资金计划汇总')) throw new Error('the dashboard does not show the summary')
if (!overviewHtml.includes('合同管理业务员')) throw new Error('the dashboard does not show the position')

const progress = store.progress('week', '2026-W45')
const positionProgress = progress.find((p) => p.positionId === clerkPosition.id)!
console.log(
  `progress for ${positionProgress.positionName}: reported ${positionProgress.reported}/${positionProgress.total}, ` +
    `done ${positionProgress.done}, percent ${positionProgress.percent}%`,
)

// 6. The month view and the ledger also work.
const monthHtml = await (await fetch(`${BASE}/?type=month`)).text()
if (!monthHtml.includes('月报总览')) throw new Error('the month overview is missing')
const ledgerHtml = await (await fetch(`${BASE}/ledger`)).text()
if (!ledgerHtml.includes('市场营销和合同管理专责')) throw new Error('the ledger is missing the specialist')

// 7. An invalid token is rejected rather than leaking data.
const bad = await fetch(`${BASE}/report?token=deadbeef`)
console.log(`invalid token -> ${bad.status}`)
if (bad.status !== 404) throw new Error('an invalid token was not rejected')

// 8. The collector recognizes the outcome files and credits them.
const demoFolder = join(HERE, '..', 'demo-folder')
const outcome = await collect(store, new DirectorySource('指定目录', demoFolder), {
  periodType: 'week',
  periodKey: '2026-W45',
  periodLabel: '2026年第45周',
})
console.log(
  `collected ${outcome.credited} files, ${outcome.uncredited} unmatched, ${outcome.ignored} ignored`,
)
if (outcome.credited === 0) throw new Error('the collector recognized nothing')

// 9. The collection shows what it recognized and what it did not.
const collectionHtml = await (await fetch(`${BASE}/collection?type=week&period=2026-W45`)).text()
if (!collectionHtml.includes('认出的产出')) throw new Error('the collection page is missing')
if (!collectionHtml.includes('认不出的文件')) throw new Error('the unmatched files are not shown')

// 10. The overview counts the completion from the collected files.
const filesOverview = await (await fetch(`${BASE}/?type=week&period=2026-W45&mode=files`)).text()
if (!filesOverview.includes('产出到位率')) throw new Error('the file-based overview is missing')
if (!filesOverview.includes('按人统计')) throw new Error('the per-person list is missing')
const reportsOverview = await (await fetch(`${BASE}/?type=week&period=2026-W45&mode=reports`)).text()
if (!reportsOverview.includes('填报率')) throw new Error('the report-based overview is missing')

console.log('END TO END OK')
db.close()
