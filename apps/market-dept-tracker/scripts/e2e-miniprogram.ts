/** Check the mini program's new endpoints against a running tracker. */
import { openDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'

const BASE = process.argv[2] ?? 'http://127.0.0.1:3000'
const DATA = process.argv[3] ?? 'data/tracker.db'
const db = openDatabase(DATA)
const store = new Store(db)

const clerk = store.people().find((p) => p.name === '黄美琳')!
const leader = store.people().find((p) => p.name === '黎嘉良')!

// 1. The duties of my post come with what I answered for them.
const duties = await (await fetch(`${BASE}/api/my-duties?token=${clerk.token}&type=week`)).json()
if (!duties.ok) throw new Error('my-duties did not answer ok')
if (duties.duties.length === 0) throw new Error('my-duties returned no duties')
console.log(`my-duties: ${duties.position}, ${duties.duties.length} duties, ${duties.periodLabel}`)

// 2. Filing a report.
const period = duties.periodKey
const items = duties.duties.map((duty: { id: string }, index: number) => ({
  id: duty.id,
  status: index === 0 ? 'done' : index === 1 ? 'in_progress' : 'not_started',
  percent: index === 0 ? 100 : index === 1 ? 60 : 0,
  note: index === 0 ? '已归档' : '',
}))
const body = [
  `token=${encodeURIComponent(clerk.token)}`,
  'periodType=week',
  `periodKey=${encodeURIComponent(period)}`,
  `summary=${encodeURIComponent('小程序提交的本周小结')}`,
  'blockers=',
  'nextPlan=',
  `items=${encodeURIComponent(JSON.stringify(items))}`,
].join('&')
const saved = await fetch(`${BASE}/api/report`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body,
})
const savedData = await saved.json()
if (!savedData.ok) throw new Error('the report was not saved')
const stored = store.reportFor(clerk.id, 'week', period)
if (!stored) throw new Error('the report is not in the database')
if (stored.summary !== '小程序提交的本周小结') throw new Error('the summary was not stored')
console.log(`report saved: ${savedData.items} items, summary="${stored.summary}"`)

// 3. Reading it back shows what was answered.
const again = await (await fetch(`${BASE}/api/my-duties?token=${clerk.token}&type=week&period=${period}`)).json()
const first = again.duties[0]
if (first.status !== 'done') throw new Error(`the answer did not come back (got ${first.status})`)
if (!again.dismissed || again.dismissed.summary !== '小程序提交的本周小结') {
  throw new Error('the summary did not come back')
}
console.log(`read back: first duty is ${first.statusLabel}, summary present`)

// 4. The posts a leader may give a task to.
const assignable = await (await fetch(`${BASE}/api/assignable?token=${leader.token}`)).json()
if (assignable.ok !== true) throw new Error('the leader cannot read the assignable posts')
console.log(`assignable: ${assignable.positions.length} posts`)

const member = await fetch(`${BASE}/api/assignable?token=${clerk.token}`)
if (member.status !== 403) throw new Error('a member may not read the assignable posts')
console.log('a member is refused the assignable posts')

// 5. Giving a task to a post.
const post = assignable.positions.find((p: { headcount: number }) => p.headcount > 0)
const assignBody = [
  `token=${encodeURIComponent(leader.token)}`,
  `positionId=${encodeURIComponent(post.id)}`,
  'periodType=week',
  `periodKey=${encodeURIComponent(period)}`,
  `title=${encodeURIComponent('小程序下达的任务')}`,
  `detail=${encodeURIComponent('测试')}`,
].join('&')
const assigned = await fetch(`${BASE}/api/tasks/assign`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: assignBody,
})
const assignedData = await assigned.json()
if (!assignedData.ok) throw new Error(`the task was not given: ${assignedData.error}`)
const created = store.tasks('week', period).filter((t) => t.title === '小程序下达的任务')
if (created.length !== post.headcount) {
  throw new Error(`expected ${post.headcount} tasks, got ${created.length}`)
}
console.log(`assigned to ${assignedData.position}: ${created.length} people got it`)

// 6. The ledger, by post, for the leaders.
const ledger = await (await fetch(`${BASE}/api/ledger?token=${leader.token}`)).json()
if (!ledger.ok) throw new Error('the leader cannot read the ledger')
if (!ledger.positions.some((p: { duties: unknown[] }) => p.duties.length > 0)) {
  throw new Error('the ledger has no duties')
}
console.log(`ledger: ${ledger.positions.length} posts`)
const ledgerDenied = await fetch(`${BASE}/api/ledger?token=${clerk.token}`)
if (ledgerDenied.status !== 403) throw new Error('a member may not read the ledger')
console.log('a member is refused the ledger')

// 7. The collection, for the leaders.
const collection = await (await fetch(`${BASE}/api/collection?token=${leader.token}&type=week`)).json()
if (!collection.ok) throw new Error('the leader cannot read the collection')
if (typeof collection.counts.files !== 'number') throw new Error('the collection has no counts')
console.log(`collection: ${collection.counts.files} files, ${collection.counts.unmatched} unmatched`)
const collectionDenied = await fetch(`${BASE}/api/collection?token=${clerk.token}`)
if (collectionDenied.status !== 403) throw new Error('a member may not read the collection')
console.log('a member is refused the collection')

// 8. A fixed task the leader sets shows up for the post.
const recurringBody = [
  `token=${encodeURIComponent(leader.token)}`,
  `positionId=${encodeURIComponent(clerk.positionId)}`,
  'cadence=week',
  `title=${encodeURIComponent('小程序设的固定任务')}`,
].join('&')
const recurring = await fetch(`${BASE}/api/tasks/recurring`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: recurringBody,
})
const recurringData = await recurring.json()
if (!recurringData.ok) throw new Error(`the fixed task was not set: ${recurringData.error}`)
const mine = await (await fetch(`${BASE}/api/my-tasks?token=${clerk.token}&type=week&period=${period}`)).json()
if (!mine.tasks.some((t: { title: string }) => t.title === '小程序设的固定任务')) {
  throw new Error('the fixed task did not reach the post')
}
console.log('a fixed task set by the leader reaches the post')
const recurringDenied = await fetch(`${BASE}/api/tasks/recurring`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: recurringBody.replace(encodeURIComponent(leader.token), encodeURIComponent(clerk.token)),
})
if (recurringDenied.status !== 403) throw new Error('a member may not set a fixed task')
console.log('a member is refused setting a fixed task')

console.log('MINI PROGRAM API OK')
db.close()
