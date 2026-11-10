import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { openDatabase, seedArtifacts, seedDatabase } from '../src/db.ts'
import { grantLeaderAccess } from '../src/access.ts'
import { Store } from '../src/store.ts'
import { createTrackerServer } from '../src/server.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const SEED = join(HERE, '..', 'seed', 'market-department.json')
const ARTIFACTS = join(HERE, '..', 'seed', 'artifacts.json')

function freshStore() {
  const db = openDatabase(':memory:')
  seedDatabase(db, SEED)
  seedArtifacts(db, ARTIFACTS)
  return { db, store: new Store(db) }
}

const NOW = '2026-11-05T00:00:00.000Z'

/**
 * The manager of the department.
 *
 * The source table leaves the manager and the deputy manager posts empty, so
 * the test grants the access to the specialist who runs the market work, the
 * way the department head would.
 */
function makeLeader(store: Store): { id: string; name: string; token: string } {
  const leader = store.people().find((p) => p.name === '黎嘉良')!
  grantLeaderAccess(store.db.raw, {
    id: leader.id,
    name: leader.name,
    grantedBy: 'test',
    grantedAt: NOW,
  })
  return leader
}

// ------------------------------------------------------------- the model

test('a superior gives a task to a subordinate', () => {
  const { store } = freshStore()
  const manager = store.people().find((p) => p.name === '黎嘉良')!
  const clerk = store.people().find((p) => p.name === '黄美琳')!
  const period = { periodType: 'week' as const, periodKey: '2026-W45', periodLabel: '2026年第45周' }

  store.createTask({
    id: 'task-1',
    departmentId: 'mkt',
    kind: 'assigned',
    title: '把上周的合同台账补齐',
    detail: '缺三份原件',
    personId: clerk.id,
    positionId: clerk.positionId,
    ...period,
    assignedById: manager.id,
    recurringTaskId: null,
    dutyId: null,
    artifactId: null,
    createdAt: NOW,
  })

  const tasks = store.tasks('week', '2026-W45')
  assert.equal(tasks.length, 1)
  assert.equal(tasks[0]?.kind, 'assigned')
  assert.equal(tasks[0]?.title, '把上周的合同台账补齐')
  assert.equal(tasks[0]?.personId, clerk.id)
  assert.equal(tasks[0]?.assignedById, manager.id)
})

test('a fixed task appears for every member of its post, once per period', () => {
  const { store } = freshStore()
  const warehouse = store.positions().find((p) => p.name === '仓储班')!

  store.createRecurringTask({
    id: 'rec-1',
    departmentId: 'mkt',
    positionId: warehouse.id,
    title: '每周盘点',
    detail: '账卡物一致',
    cadence: 'week',
    dutyId: null,
    artifactId: null,
    createdAt: NOW,
  })

  const created = store.materialiseRecurringTasks('mkt', 'week', '2026-W45', '2026年第45周', NOW)
  const members = store.people().filter((p) => p.positionId === warehouse.id)
  assert.equal(members.length, 5)
  assert.equal(created, 5, 'every member of the post gets the fixed task')

  // Running it again adds nothing.
  const again = store.materialiseRecurringTasks('mkt', 'week', '2026-W45', '2026年第45周', NOW)
  assert.equal(again, 0)
  assert.equal(store.tasks('week', '2026-W45').length, 5)
})

test('a monthly fixed task does not turn up in a week', () => {
  const { store } = freshStore()
  const warehouse = store.positions().find((p) => p.name === '仓储班')!
  store.createRecurringTask({
    id: 'rec-month',
    departmentId: 'mkt',
    positionId: warehouse.id,
    title: '每月物资盘点',
    detail: '',
    cadence: 'month',
    dutyId: null,
    artifactId: null,
    createdAt: NOW,
  })
  assert.equal(store.materialiseRecurringTasks('mkt', 'week', '2026-W45', '2026年第45周', NOW), 0)
  assert.equal(store.materialiseRecurringTasks('mkt', 'month', '2026-11', '2026年11月', NOW), 5)
})

test('a task moves on and keeps what the person said', () => {
  const { store } = freshStore()
  const clerk = store.people().find((p) => p.name === '黄美琳')!
  store.createTask({
    id: 'task-2',
    departmentId: 'mkt',
    kind: 'assigned',
    title: '交回款凭证',
    detail: '',
    personId: clerk.id,
    positionId: clerk.positionId,
    periodType: 'week',
    periodKey: '2026-W45',
    periodLabel: '2026年第45周',
    assignedById: null,
    recurringTaskId: null,
    dutyId: null,
    artifactId: null,
    createdAt: NOW,
  })
  store.updateTask('task-2', 'done', '已交 12 笔', NOW)
  const task = store.task('task-2')
  assert.equal(task?.status, 'done')
  assert.equal(task?.note, '已交 12 笔')
})

test('the task views name the people and the posts', () => {
  const { store } = freshStore()
  const manager = store.people().find((p) => p.name === '黎嘉良')!
  const clerk = store.people().find((p) => p.name === '黄美琳')!
  store.createTask({
    id: 'task-3',
    departmentId: 'mkt',
    kind: 'assigned',
    title: '补齐合同台账',
    detail: '',
    personId: clerk.id,
    positionId: clerk.positionId,
    periodType: 'week',
    periodKey: '2026-W45',
    periodLabel: '2026年第45周',
    assignedById: manager.id,
    recurringTaskId: null,
    dutyId: null,
    artifactId: null,
    createdAt: NOW,
  })
  const views = store.taskViews('week', '2026-W45')
  assert.equal(views.length, 1)
  assert.equal(views[0]?.personName, '黄美琳')
  assert.equal(views[0]?.assignedByName, '黎嘉良')
  assert.equal(views[0]?.positionName, '合同管理业务员')
})

// -------------------------------------------------------------- the pages

async function withServer<T>(run: (base: string, store: Store) => Promise<T>): Promise<T> {
  const { db, store } = freshStore()
  const server = createTrackerServer({
    store,
    db,
    baseUrl: 'http://127.0.0.1:0',
    now: () => new Date('2026-11-05T00:00:00Z'),
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  try {
    return await run(`http://127.0.0.1:${port}`, store)
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    db.close()
  }
}

test('the task list page shows the tasks of a period', async () => {
  await withServer(async (base, store) => {
    makeLeader(store)
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    store.createTask({
      id: 'task-4',
      departmentId: 'mkt',
      kind: 'assigned',
      title: '整理回款凭证',
      detail: '',
      personId: clerk.id,
      positionId: clerk.positionId,
      periodType: 'week',
      periodKey: '2026-W45',
      periodLabel: '2026年第45周',
      assignedById: null,
      recurringTaskId: null,
      dutyId: null,
      artifactId: null,
      createdAt: NOW,
    })
    const leader = store.people().find((p) => p.name === '黎嘉良')!
    const html = await (
      await fetch(`${base}/tasks?type=week&period=2026-W45&token=${leader.token}`)
    ).text()
    assert.ok(html.includes('任务清单'), 'the page must be the task list')
    assert.ok(html.includes('整理回款凭证'), 'the task must be shown')
    // Nobody is named: the list is by post.
    assert.ok(html.includes('合同管理业务员'), 'the post must be shown')
    assert.ok(!html.includes('黄美琳'), 'no person may be named')
  })
})

test('no page names a person', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const manager = store.people().find((p) => p.name === '黎嘉良')!
    store.createTask({
      id: 'task-name',
      departmentId: 'mkt',
      kind: 'assigned',
      title: '整理回款凭证',
      detail: '',
      personId: clerk.id,
      positionId: clerk.positionId,
      periodType: 'week',
      periodKey: '2026-W45',
      periodLabel: '2026年第45周',
      assignedById: manager.id,
      recurringTaskId: null,
      dutyId: null,
      artifactId: null,
      createdAt: NOW,
    })

    const pages = ['/tasks?type=week&period=2026-W45', '/ledger', '/links', '/collection?type=week&period=2026-W45']
    for (const path of pages) {
      const html = await (await fetch(`http://127.0.0.1:${portFor(base)}${path}`)).text()
      assert.ok(!html.includes('黄美琳'), `${path} names a person`)
      assert.ok(!html.includes('黎嘉良'), `${path} names a person`)
    }
  })
})

/** The port of a running test server. */
function portFor(base: string): string {
  return new URL(base).port
}

test('a superior can give a task over the page', async () => {
  await withServer(async (base, store) => {
    const manager = makeLeader(store)
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const form = new URLSearchParams()
    form.set('token', manager.token)
    form.set('periodType', 'week')
    form.set('periodKey', '2026-W45')
    form.set('periodLabel', '2026年第45周')
    form.set('title', '把上周的合同台账补齐')
    form.set('positionId', clerk.positionId)

    const response = await fetch(`${base}/tasks/assign`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
      redirect: 'manual',
    })
    assert.equal(response.status, 302)
    const tasks = store.tasks('week', '2026-W45')
    assert.equal(tasks.length, 1)
    assert.equal(tasks[0]?.title, '把上周的合同台账补齐')
    assert.equal(tasks[0]?.personId, clerk.id)
    assert.equal(tasks[0]?.assignedById, manager.id)
  })
})

test('a member may not give a task out', async () => {
  await withServer(async (base, store) => {
    // 黎嘉良 is a 专责; without the leader grant they may not assign.
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const other = store.people().find((p) => p.name === '潘清河')!
    const form = new URLSearchParams()
    form.set('token', clerk.token)
    form.set('periodType', 'week')
    form.set('periodKey', '2026-W45')
    form.set('title', '越级下达')
    form.set('positionId', other.positionId)

    const response = await fetch(`${base}/tasks/assign`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
      redirect: 'manual',
    })
    assert.equal(response.status, 403)
    assert.equal(store.tasks('week', '2026-W45').length, 0)
  })
})

test('a member may not see the department pages', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    for (const path of ['/', '/tasks', '/ledger', '/links', '/collection']) {
      const response = await fetch(`${base}${path}${path.includes('?') ? '&' : '?'}token=${clerk.token}`)
      assert.equal(response.status, 403, `${path} must be closed to a member`)
    }
  })
})

test('a member sees only their own tasks', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const other = store.people().find((p) => p.name === '潘清河')!
    store.createTask({
      id: 'task-own',
      departmentId: 'mkt',
      kind: 'assigned',
      title: '我自己的任务',
      detail: '',
      personId: clerk.id,
      positionId: clerk.positionId,
      periodType: 'week',
      periodKey: '2026-W45',
      periodLabel: '2026年第45周',
      assignedById: null,
      recurringTaskId: null,
      dutyId: null,
      artifactId: null,
      createdAt: NOW,
    })
    store.createTask({
      id: 'task-other',
      departmentId: 'mkt',
      kind: 'assigned',
      title: '别人的任务',
      detail: '',
      personId: other.id,
      positionId: other.positionId,
      periodType: 'week',
      periodKey: '2026-W45',
      periodLabel: '2026年第45周',
      assignedById: null,
      recurringTaskId: null,
      dutyId: null,
      artifactId: null,
      createdAt: NOW,
    })

    const mine = await (await fetch(`${base}/my-tasks?token=${clerk.token}&type=week&period=2026-W45`)).text()
    assert.ok(mine.includes('我自己的任务'))
    assert.ok(!mine.includes('别人的任务'), 'a member must not see another person task')
  })
})

test('a leader sees the whole department', async () => {
  await withServer(async (base, store) => {
    const manager = makeLeader(store)
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    store.createTask({
      id: 'task-dept',
      departmentId: 'mkt',
      kind: 'assigned',
      title: '全部门都能看到',
      detail: '',
      personId: clerk.id,
      positionId: clerk.positionId,
      periodType: 'week',
      periodKey: '2026-W45',
      periodLabel: '2026年第45周',
      assignedById: null,
      recurringTaskId: null,
      dutyId: null,
      artifactId: null,
      createdAt: NOW,
    })
    const html = await (
      await fetch(`${base}/tasks?type=week&period=2026-W45&token=${manager.token}`)
    ).text()
    assert.ok(html.includes('全部门都能看到'), 'a leader sees the whole department')
  })
})

test('somebody outside the chain of command cannot give a task', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const warehouse = store.people().find((p) => p.name === '潘清河')!
    const form = new URLSearchParams()
    form.set('token', clerk.token)
    form.set('periodType', 'week')
    form.set('periodKey', '2026-W45')
    form.set('title', '越级下达')
    form.set('personId', warehouse.id)

    const response = await fetch(`${base}/tasks/assign`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
      redirect: 'manual',
    })
    assert.equal(response.status, 403)
    assert.equal(store.tasks('week', '2026-W45').length, 0)
  })
})

test('a person works through their own tasks', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    store.createTask({
      id: 'task-5',
      departmentId: 'mkt',
      kind: 'assigned',
      title: '交回款凭证',
      detail: '',
      personId: clerk.id,
      positionId: clerk.positionId,
      periodType: 'week',
      periodKey: '2026-W45',
      periodLabel: '2026年第45周',
      assignedById: null,
      recurringTaskId: null,
      dutyId: null,
      artifactId: null,
      createdAt: NOW,
    })

    const html = await (await fetch(`${base}/my-tasks?token=${clerk.token}&type=week&period=2026-W45`)).text()
    assert.ok(html.includes('我的任务'))
    assert.ok(html.includes('交回款凭证'))

    const form = new URLSearchParams()
    form.set('token', 'task-5')
    form.set('status', 'done')
    form.set('note', '已交 12 笔')
    const response = await fetch(`${base}/my-tasks/status`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
      redirect: 'manual',
    })
    assert.equal(response.status, 302)
    assert.equal(store.task('task-5')?.status, 'done')
  })
})

test('the seed file for the posts stays well formed', () => {
  const seed = JSON.parse(readFileSync(SEED, 'utf8')) as { departments: unknown[] }
  assert.ok(seed.departments.length > 0)
})

// ------------------------------------------------------------- the mini program

/** The JSON answer of a call, as the mini program reads it. */
async function json<T = Record<string, unknown>>(response: Response): Promise<T> {
  return (await response.json()) as T
}

test('the api says who the link belongs to', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const data = await json<{
      ok: boolean
      person: { id: string }
      position: { name: string }
      role: string
    }>(await fetch(`${base}/api/me?token=${clerk.token}`))
    assert.equal(data.ok, true)
    assert.equal(data.person.id, clerk.id)
    assert.equal(data.position.name, '合同管理业务员')
    assert.equal(data.role, 'member')
  })
})

test('the api hands a person their own tasks', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const other = store.people().find((p) => p.name === '潘清河')!
    for (const [id, person, title] of [
      ['api-task-own', clerk, '我自己的任务'],
      ['api-task-other', other, '别人的任务'],
    ] as const) {
      store.createTask({
        id,
        departmentId: 'mkt',
        kind: 'assigned',
        title,
        detail: '',
        personId: person.id,
        positionId: person.positionId,
        periodType: 'week',
        periodKey: '2026-W45',
        periodLabel: '2026年第45周',
        assignedById: null,
        recurringTaskId: null,
        dutyId: null,
        artifactId: null,
        createdAt: NOW,
      })
    }
    const data = await json<Record<string, any>>(await fetch(`${base}/api/my-tasks?token=${clerk.token}&type=week&period=2026-W45`))
    assert.equal(data.ok, true)
    const titles = data.tasks.map((t: { title: string }) => t.title)
    assert.ok(titles.includes('我自己的任务'))
    assert.ok(!titles.includes('别人的任务'), 'the api must not hand over another person task')
  })
})

test('the api moves a task on, but only its owner may', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const other = store.people().find((p) => p.name === '潘清河')!
    store.createTask({
      id: 'api-task-move',
      departmentId: 'mkt',
      kind: 'assigned',
      title: '交回款凭证',
      detail: '',
      personId: clerk.id,
      positionId: clerk.positionId,
      periodType: 'week',
      periodKey: '2026-W45',
      periodLabel: '2026年第45周',
      assignedById: null,
      recurringTaskId: null,
      dutyId: null,
      artifactId: null,
      createdAt: NOW,
    })

    const post = (token: string) =>
      fetch(`${base}/api/my-tasks/status`, {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ token, id: 'api-task-move', status: 'done', note: '已交' }).toString(),
      })

    const denied = await post(other.token)
    assert.equal(denied.status, 403, 'another person may not move it')

    const ok = await post(clerk.token)
    assert.equal(ok.status, 200)
    const data = await json<{ ok: boolean; task: { status: string } }>(ok)
    assert.equal(data.ok, true)
    assert.equal(data.task.status, 'done')
    assert.equal(store.task('api-task-move')?.status, 'done')
  })
})

test('the api keeps the department overview for the leaders', async () => {
  await withServer(async (base, store) => {
    const clerk = store.people().find((p) => p.name === '黄美琳')!
    const denied = await fetch(`${base}/api/overview?token=${clerk.token}`)
    assert.equal(denied.status, 403, 'a member may not read the overview')

    const leader = makeLeader(store)
    const ok = await fetch(`${base}/api/overview?token=${leader.token}`)
    assert.equal(ok.status, 200)
    const data = await json<{ ok: boolean; peopleCount: number; positions: unknown[] }>(ok)
    assert.equal(data.ok, true)
    assert.equal(data.peopleCount, 25)
    assert.ok(Array.isArray(data.positions))
    assert.ok(data.positions.length > 0)
  })
})
