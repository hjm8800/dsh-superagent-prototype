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

console.log('MINI PROGRAM API OK')
db.close()
