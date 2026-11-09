import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { openDatabase, seedDatabase, slugify } from '../src/db.ts'
import { Store, statusWeight } from '../src/store.ts'
import { createTrackerServer } from '../src/server.ts'
import { isoWeekKey, monthKey, periodFor, recentPeriods } from '../src/period.ts'
import { PROGRESS_STATUSES } from '../src/types.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const SEED = join(HERE, '..', 'seed', 'market-department.json')

function freshStore() {
  const db = openDatabase(':memory:')
  seedDatabase(db, SEED)
  return { db, store: new Store(db) }
}

// ---------------------------------------------------------------- periods

test('ISO week key matches the ISO week number', () => {
  // 2026-01-01 is a Thursday, so it belongs to ISO week 1 of 2026.
  assert.equal(isoWeekKey(new Date('2026-01-01T00:00:00Z')), '2026-W01')
  // 2026-11-05 is a Thursday of ISO week 45.
  assert.equal(isoWeekKey(new Date('2026-11-05T00:00:00Z')), '2026-W45')
})

test('month key and label are stable', () => {
  assert.equal(monthKey(new Date('2026-11-05T00:00:00Z')), '2026-11')
  assert.equal(periodFor('month', new Date('2026-11-05T00:00:00Z')).label, '2026年11月')
})

test('recent periods are unique and newest first', () => {
  const periods = recentPeriods('week', new Date('2026-11-05T00:00:00Z'), 6)
  assert.equal(periods.length, 6)
  assert.equal(new Set(periods.map((p) => p.key)).size, 6)
  assert.equal(periods[0]?.key, '2026-W45')
})

// ------------------------------------------------------------------- seed

test('seeding loads the market department org', () => {
  const { store } = freshStore()
  const departments = store.departments()
  assert.equal(departments.length, 1)
  assert.equal(departments[0]?.name, '市场部')

  const positions = store.positions()
  assert.ok(positions.length >= 17, `expected at least 17 positions, got ${positions.length}`)

  const manager = positions.find((p) => p.name === '经理')
  const specialist = positions.find((p) => p.name === '市场营销和合同管理专责')
  assert.ok(manager, '经理 position is missing')
  assert.ok(specialist, '市场营销和合同管理专责 position is missing')
  assert.equal(specialist.parentId, manager.id, 'specialist must sit under the manager')
  assert.equal(specialist.level, 'specialist')
})

test('every position has its duties split into individual items', () => {
  const { store } = freshStore()
  const positions = store.positions()
  let total = 0
  for (const position of positions) {
    const duties = store.duties(position.id)
    total += duties.length
    for (const duty of duties) {
      assert.ok(duty.text.length > 0, `${position.name} has an empty duty`)
      assert.ok(duty.ordinal > 0)
    }
  }
  assert.ok(total > 100, `expected a substantial duty list, got ${total}`)
})

test('the manager and deputy duties are split into separate items', () => {
  const { store } = freshStore()
  const manager = store.positions().find((p) => p.name === '经理')!
  const managerDuties = store.duties(manager.id).map((d) => d.text)
  assert.deepEqual(managerDuties, ['经营指标', '重大客户', '制度审批', '风控审计', '队伍建设'])

  const deputy = store.positions().find((p) => p.name === '副经理')!
  const deputyDuties = store.duties(deputy.id).map((d) => d.text)
  assert.deepEqual(deputyDuties, ['业务执行', '班组管理', '流程推进', '交付保障', '时效管控'])
})

test('the specialist duty list is not one merged blob', () => {
  const { store } = freshStore()
  const specialist = store.positions().find((p) => p.name === '市场营销和合同管理专责')!
  const duties = store.duties(specialist.id)
  assert.ok(duties.length >= 10, `expected many duties, got ${duties.length}`)
  assert.ok(duties.every((d) => d.text.length < 300), 'a duty item swallowed too much text')
})

test('people are attached to their positions', () => {
  const { store } = freshStore()
  const people = store.people()
  // The source table names 25 people across the posts and the two side
  // notes. Three posts are left open there ("莫楠or新人", "新、男"),
  // so they have no member.
  assert.equal(people.length, 25, `expected 25 named people, got ${people.length}`)
  const clerk = people.find((p) => p.name === '黄美琳')
  assert.ok(clerk, '黄美琳 is missing from the roster')
  const position = store.positionById(clerk.positionId)
  assert.equal(position?.name, '合同管理业务员')
  assert.equal(clerk.pending, false)

  const warehouse = people.filter((p) => p.name === '潘清河' || p.name === '莫桂凤')
  assert.equal(warehouse.length, 2)
  assert.equal(store.positionById(warehouse[0]!.positionId)?.name, '仓储班')
})

test('the seed file is well formed', () => {
  const seed = JSON.parse(readFileSync(SEED, 'utf8')) as {
    departments: {
      name: string
      positions: { name: string; parent: string | null; duties: string[]; members: string[] }[]
    }[]
  }
  const department = seed.departments[0]!
  assert.equal(department.name, '市场部')
  assert.equal(department.positions.length, 17)

  const names = new Set(department.positions.map((p) => p.name))
  for (const position of department.positions) {
    if (position.parent) {
      assert.ok(names.has(position.parent), `${position.name} points at unknown parent ${position.parent}`)
    }
    for (const duty of position.duties) {
      assert.ok(duty.trim().length > 0, `${position.name} has an empty duty`)
      assert.ok(!duty.endsWith('。'), `${position.name} has a duty with a trailing full stop`)
    }
  }

  // No person sits in two posts.
  const people = department.positions.flatMap((p) => p.members)
  assert.equal(new Set(people).size, people.length, 'a person is listed in two posts')
})

test('posts the source table leaves open have no member', () => {
  const { store } = freshStore()
  for (const name of ['技经业务员', '物资管理业务员', '结算班']) {
    const position = store.positions().find((p) => p.name === name)!
    assert.ok(position, `${name} is missing`)
    const members = store.people().filter((p) => p.positionId === position.id)
    assert.equal(members.length, 0, `${name} should have no member yet`)
    assert.ok(position.note.length > 0, `${name} should say why it is open`)
  }
})

test('the two named roles inside the photovoltaic group are filled', () => {
  const { store } = freshStore()
  const team = store.positions().find((p) => p.name === '光伏+用户标团')!
  const market = store.positions().find((p) => p.name === '市场拓展业务员')!
  const service = store.positions().find((p) => p.name === '客户服务业务员')!
  assert.equal(market.parentId, team.id)
  assert.equal(service.parentId, team.id)
  assert.equal(store.people().filter((p) => p.positionId === market.id).length, 1)
  assert.equal(store.people().filter((p) => p.positionId === service.id).length, 2)
  assert.equal(store.duties(market.id).length, 6)
  assert.equal(store.duties(service.id).length, 6)
})

test('seeding twice keeps the same tokens so report links survive', () => {
  const db = openDatabase(':memory:')
  seedDatabase(db, SEED)
  const before = new Store(db).people().map((p) => `${p.id}:${p.token}`)
  seedDatabase(db, SEED)
  const after = new Store(db).people().map((p) => `${p.id}:${p.token}`)
  assert.deepEqual(after, before)
})

// ---------------------------------------------------------------- reports

test('a report is saved, replaced, and reflected in progress', () => {
  const { store } = freshStore()
  const person = store.people().find((p) => p.name === '黄美琳')!
  const position = store.positionById(person.positionId)!
  const duties = position.duties

  const items = duties.map((duty, index) => ({
    dutyId: duty.id,
    status: index === 0 ? ('done' as const) : ('not_started' as const),
    percent: index === 0 ? 100 : 0,
    note: index === 0 ? '已归档' : '',
  }))

  store.saveReport({
    personId: person.id,
    periodType: 'week',
    periodKey: '2026-W45',
    periodLabel: '2026年第45周',
    summary: '本周完成合同归档',
    blockers: '',
    nextPlan: '继续催收',
    items,
  })

  const report = store.reportFor(person.id, 'week', '2026-W45')
  assert.ok(report, 'the report was not stored')
  assert.equal(report.items.length, duties.length)
  assert.equal(report.summary, '本周完成合同归档')

  const progress = store.progress('week', '2026-W45')
  const positionProgress = progress.find((p) => p.positionId === position.id)!
  assert.equal(positionProgress.reported, 1)
  assert.equal(positionProgress.total, 1)
  assert.equal(positionProgress.done, 1)
  assert.equal(positionProgress.expected, duties.length)
  assert.equal(positionProgress.answered, duties.length)

  // Filing again replaces the previous answers instead of adding to them.
  store.saveReport({
    personId: person.id,
    periodType: 'week',
    periodKey: '2026-W45',
    periodLabel: '2026年第45周',
    summary: '修订',
    blockers: '',
    nextPlan: '',
    items: duties.map((duty) => ({
      dutyId: duty.id,
      status: 'blocked' as const,
      percent: 30,
      note: '',
    })),
  })
  const updated = store.reportFor(person.id, 'week', '2026-W45')
  assert.equal(updated?.items.length, duties.length)
  assert.equal(updated?.summary, '修订')
  const after = store.progress('week', '2026-W45').find((p) => p.positionId === position.id)!
  assert.equal(after.blocked, duties.length)
  assert.equal(after.done, 0)
})

test('a position with unreported people shows as incomplete', () => {
  const { store } = freshStore()
  const progress = store.progress('week', '2026-W45')
  const warehouse = progress.find((p) => p.positionName === '仓储班')!
  assert.ok(warehouse, '仓储班 is missing from progress')
  assert.equal(warehouse.reported, 0)
  assert.equal(warehouse.total, 5)
  assert.equal(warehouse.percent, 0)
  assert.ok(warehouse.expected > 0, 'expected item count must include unreported people')
})

test('the report tree nests positions under their parent', () => {
  const { store } = freshStore()
  const department = store.departments()[0]!
  const tree = store.tree(department.id)
  assert.ok(tree.length >= 2, 'the department root should list the manager and the deputy manager')
  const manager = tree.find((n) => n.name === '经理')!
  const specialist = manager.children.find((n) => n.name === '市场营销和合同管理专责')
  assert.ok(specialist, 'the specialist must be a child of the manager')
  assert.ok(specialist.children.some((n) => n.name === '合同管理业务员'))
})

test('status weights map progress onto a percentage', () => {
  assert.equal(statusWeight('done'), 100)
  assert.equal(statusWeight('in_progress'), 50)
  assert.equal(statusWeight('not_started'), 0)
  for (const status of PROGRESS_STATUSES) {
    assert.equal(typeof statusWeight(status), 'number')
  }
})

test('slugs are stable and url safe', () => {
  assert.equal(slugify('合同管理业务员'), 'p-' + slugify('合同管理业务员').slice(2))
  assert.match(slugify('Manager'), /^[a-z0-9-]+$/)
  assert.equal(slugify('Manager'), 'manager')
})

test('a report cannot be filed for a period the form never offered', async () => {
  const db = openDatabase(':memory:')
  seedDatabase(db, SEED)
  const store = new Store(db)
  const server = createTrackerServer({
    store,
    baseUrl: 'http://127.0.0.1:0',
    now: () => new Date('2026-11-05T00:00:00Z'),
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0

  const clerk = store.people().find((p) => p.name === '黄美琳')!
  const form = new URLSearchParams()
  form.set('token', clerk.token)
  form.set('periodType', 'week')
  form.set('periodKey', '1999-W01')
  form.set('periodLabel', '1999年第1周')
  form.set('summary', 'should not be stored')

  const response = await fetch(`http://127.0.0.1:${port}/report`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
    redirect: 'manual',
  })
  assert.equal(response.status, 400, 'an unknown period must be rejected')
  assert.equal(store.reportFor(clerk.id, 'week', '1999-W01'), undefined)

  // The period the form does offer is accepted.
  form.set('periodKey', '2026-W45')
  const ok = await fetch(`http://127.0.0.1:${port}/report`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: form.toString(),
    redirect: 'manual',
  })
  assert.equal(ok.status, 302)
  assert.ok(store.reportFor(clerk.id, 'week', '2026-W45'))

  await new Promise<void>((resolve) => server.close(() => resolve()))
  db.close()
})

test('a report shows the confirmation after it is submitted', async () => {
  const db = openDatabase(':memory:')
  seedDatabase(db, SEED)
  const store = new Store(db)
  const server = createTrackerServer({
    store,
    baseUrl: 'http://127.0.0.1:0',
    now: () => new Date('2026-11-05T00:00:00Z'),
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0

  const clerk = store.people().find((p) => p.name === '黄美琳')!
  const before = await (await fetch(`http://127.0.0.1:${port}/report?token=${clerk.token}`)).text()
  assert.ok(!before.includes('已提交周报'), 'the confirmation must not show before submitting')

  const after = await (
    await fetch(`http://127.0.0.1:${port}/report?token=${clerk.token}&saved=1`)
  ).text()
  assert.ok(after.includes('已提交周报'), 'the confirmation must show after submitting')

  await new Promise<void>((resolve) => server.close(() => resolve()))
  db.close()
})

test('the overview offers both ways of counting the completion', async () => {
  const db = openDatabase(':memory:')
  seedDatabase(db, SEED)
  const store = new Store(db)
  const server = createTrackerServer({
    store,
    baseUrl: 'http://127.0.0.1:0',
    now: () => new Date('2026-11-05T00:00:00Z'),
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0

  const reports = await (await fetch(`http://127.0.0.1:${port}/`)).text()
  assert.ok(reports.includes('按成果文件统计'), 'the overview must offer the file-based count')
  assert.ok(reports.includes('填报率'), 'the overview must keep the reporting rate')

  const files = await (await fetch(`http://127.0.0.1:${port}/?mode=files`)).text()
  assert.ok(files.includes('产出到位率'), 'the file-based count must lead with the output rate')
  assert.ok(files.includes('按人统计'), 'the file-based count must list the people')

  await new Promise<void>((resolve) => server.close(() => resolve()))
  db.close()
})
