/** Check the task flow against a running tracker. */
import { openDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'

const BASE = process.argv[2] ?? 'http://127.0.0.1:3000'
const DATA = process.argv[3] ?? 'data/tracker.db'

const db = openDatabase(DATA)
const store = new Store(db)

// 1. The manager's link shows the form to give a task out.
const manager = store.people().find((p) => p.name === '黎嘉良')!
const clerk = store.people().find((p) => p.name === '黄美琳')!
const list = await (await fetch(`${BASE}/tasks?type=week&token=${manager.token}`)).text()
if (!list.includes('下达任务')) throw new Error('the manager does not see the assign form')
if (!list.includes(clerk.name)) throw new Error('the subordinate is not offered')
console.log('the manager sees the assign form with their people')

// 2. Giving a task over the page.
const form = new URLSearchParams()
form.set('token', manager.token)
form.set('periodType', 'week')
form.set('periodKey', '2026-W46')
form.set('periodLabel', '2026年第46周')
form.set('title', '把上周的合同台账补齐')
form.set('detail', '还缺三份原件')
form.set('personId', clerk.id)
const response = await fetch(`${BASE}/tasks/assign`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: form.toString(),
  redirect: 'manual',
})
console.log('assign ->', response.status)
if (response.status !== 302) throw new Error('the task was not given')

const tasks = store.tasks('week', '2026-W46')
const assigned = tasks.find((t) => t.title === '把上周的合同台账补齐')
if (!assigned) throw new Error('the task was not stored')
if (assigned.assignedById !== manager.id) throw new Error('the assigner was not recorded')
console.log('the task is stored with its assigner')

// 3. Somebody outside the chain of command cannot give a task.
const outsider = store.people().find((p) => p.name === '黄美琳')!
const other = store.people().find((p) => p.name === '潘清河')!
const denied = new URLSearchParams()
denied.set('token', outsider.token)
denied.set('periodType', 'week')
denied.set('periodKey', '2026-W46')
denied.set('title', '越级下达')
denied.set('personId', other.id)
const deniedResponse = await fetch(`${BASE}/tasks/assign`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: denied.toString(),
  redirect: 'manual',
})
console.log('cross-branch assign ->', deniedResponse.status)
if (deniedResponse.status !== 403) throw new Error('a task was given outside the chain of command')

// 4. The person works through their own tasks.
const mine = await (await fetch(`${BASE}/my-tasks?token=${clerk.token}&type=week&period=2026-W46`)).text()
if (!mine.includes('我的任务')) throw new Error('the personal task list is missing')
if (!mine.includes('把上周的合同台账补齐')) throw new Error('the task is not on the list')
console.log('the person sees their own task')

const statusForm = new URLSearchParams()
statusForm.set('token', assigned.id)
statusForm.set('status', 'in_progress')
statusForm.set('note', '已补两份，还差一份')
const statusResponse = await fetch(`${BASE}/my-tasks/status`, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body: statusForm.toString(),
  redirect: 'manual',
})
if (statusResponse.status !== 302) throw new Error('the status was not saved')
if (store.task(assigned.id)?.status !== 'in_progress') throw new Error('the status was not stored')
console.log('the person moves the task on')

// 5. A fixed task reaches everyone of its post.
const warehouse = store.positions().find((p) => p.name === '仓储班')!
const members = store.people().filter((p) => p.positionId === warehouse.id)
const forWarehouse = store
  .tasks('week', '2026-W46')
  .filter((t) => t.positionId === warehouse.id && t.kind === 'recurring')
console.log(`the fixed task of 仓储班 reached ${forWarehouse.length} of ${members.length} people`)
if (forWarehouse.length !== members.length) throw new Error('the fixed task did not reach everyone')

console.log('TASK FLOW OK')
db.close()
