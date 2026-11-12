import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { Store } from './store.ts'
import { PROGRESS_STATUSES, type ProgressStatus, type ReportItem } from './types.ts'
import { LEVEL_LABELS, PROGRESS_LABELS, TASK_KIND_LABELS, TASK_STATUS_LABELS } from './types.ts'
import { periodFor, periodRange, recentPeriods } from './period.ts'
import {
  renderOverview,
  renderLedger,
  renderReportForm,
  renderLinks,
  renderPersonReport,
  renderCollection,
  renderWelcome,
  type OverviewItem,
} from './views.ts'
import {
  renderTaskList,
  renderMyTasks,
  assignablePosts,
  type TaskPosition,
  type TaskPositionNode,
} from './task-views.ts'
import type { PeriodType, TaskStatus, Person, Position, PositionView, AccessRole, Account } from './types.ts'
import { accessOf, leaderGrants } from './access.ts'
import { verifyPassword } from './accounts.ts'
import { escapeHtml, layout } from './html.ts'
import type { Department } from './types.ts'
import type { Database } from './db.ts'

/** HTTP layer: routes requests to the store and renders pages. */

export interface ServerOptions {
  store: Store
  /** The database, for the access grants kept beside the data. */
  db: Database
  /** Public base URL, used to build the links handed to employees. */
  baseUrl: string
  /** Fixed "now", so tests are deterministic. */
  now?: () => Date
}

const HERE = dirname(fileURLToPath(import.meta.url))

export function createTrackerServer(options: ServerOptions) {
  const { store, db } = options
  const now = options.now ?? (() => new Date())

  /** The department this tracker covers. The seed always creates exactly one. */
  const department = (): Department => {
    const first = store.departments()[0]
    if (!first) throw new Error('数据库中没有部门数据，请先执行 seed。')
    return first
  }

  const parseType = (value: string | null): PeriodType => (value === 'month' ? 'month' : 'week')

  // ------------------------------------------------------------ helpers

  const sendHtml = (res: ServerResponse, html: string, status = 200) => {
    const body = Buffer.from(html, 'utf8')
    res.writeHead(status, {
      'content-type': 'text/html; charset=utf-8',
      'content-length': body.byteLength,
      'cache-control': 'no-store',
    })
    res.end(body)
  }

  const sendText = (res: ServerResponse, text: string, status = 200) => {
    const body = Buffer.from(text, 'utf8')
    res.writeHead(status, {
      'content-type': 'text/plain; charset=utf-8',
      'content-length': body.byteLength,
    })
    res.end(body)
  }

  /** An answer for the mini program and any other JSON caller. */
  const sendJson = (res: ServerResponse, value: unknown, status = 200) => {
    const body = Buffer.from(JSON.stringify(value), 'utf8')
    res.writeHead(status, {
      'content-type': 'application/json; charset=utf-8',
      'content-length': body.byteLength,
      'cache-control': 'no-store',
      // The mini program and the web page are served from different origins.
      'access-control-allow-origin': '*',
      'access-control-allow-headers': 'content-type',
      'access-control-allow-methods': 'GET,POST,OPTIONS',
    })
    res.end(body)
  }

  const redirect = (res: ServerResponse, location: string) => {
    res.writeHead(302, { location })
    res.end()
  }

  const readBody = async (req: IncomingMessage): Promise<URLSearchParams> => {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    return new URLSearchParams(Buffer.concat(chunks).toString('utf8'))
  }

  /** The cookie that remembers whose link this browser opened. */
  const SESSION_COOKIE = 'mdt_token'

  const cookieOf = (req: IncomingMessage, name: string): string => {
    for (const part of (req.headers.cookie ?? '').split(';')) {
      const index = part.indexOf('=')
      if (index < 0) continue
      if (part.slice(0, index).trim() !== name) continue
      return decodeURIComponent(part.slice(index + 1).trim())
    }
    return ''
  }

  /**
   * Remember the person for this browser.
   *
   * The person's link carries their token; opening it once is enough, so the
   * pages do not have to repeat the token in every address.
   */
  const remember = (res: ServerResponse, token: string): void => {
    res.setHeader(
      'set-cookie',
      `${SESSION_COOKIE}=${encodeURIComponent(token)}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax`,
    )
  }

  const ACCOUNT_COOKIE = 'mdt_account'

  /** The post account this browser is logged in with, if any. */
  const accountOf = (req: IncomingMessage): Account | undefined => {
    const token = cookieOf(req, ACCOUNT_COOKIE)
    return token ? store.accountByToken(token) : undefined
  }

  /** Log in with a post's account; the whole post shares it. */
  const login = async (req: IncomingMessage, res: ServerResponse) => {
    const form = await readBody(req)
    const username = (form.get('username') ?? '').toString().trim()
    const password = (form.get('password') ?? '').toString()
    const account = username ? store.accountByUsername(username) : undefined
    if (!account || !verifyPassword(password, account.passwordHash)) {
      sendHtml(
        res,
        renderWelcome({ departmentName: department().name, error: '账户或密码不对，请向部门负责人确认。' }),
        401,
      )
      return
    }
    const position = store.positionById(account.positionId)
    const person = position ? store.people().find((p) => p.positionId === position.id) : undefined
    const cookies = [
      `${ACCOUNT_COOKIE}=${encodeURIComponent(account.token)}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax`,
    ]
    if (person) {
      cookies.push(
        `${SESSION_COOKIE}=${encodeURIComponent(person.token)}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax`,
      )
    }
    res.setHeader('set-cookie', cookies)
    const role = person && position ? accessOf(person, position, leaderGrants(db.raw)) : 'member'
    redirect(res, role === 'leader' ? '/' : '/report')
  }

  const logout = (_req: IncomingMessage, res: ServerResponse) => {
    res.setHeader('set-cookie', [
      `${ACCOUNT_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`,
      `${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`,
    ])
    redirect(res, '/')
  }

  // ------------------------------------------------------------- routes

  const overview = (req: IncomingMessage, res: ServerResponse, url: URL) => {
    const dept = department()
    const periodType = parseType(url.searchParams.get('type'))
    const today = now()
    const current = periodFor(periodType, today)
    const requested = url.searchParams.get('period')
    const periods = recentPeriods(periodType, today, 8)
    const periodKey = periods.some((p) => p.key === requested) ? (requested as string) : current.key
    const periodLabel = periods.find((p) => p.key === periodKey)?.label ?? current.label
    // The completion comes from the outcome files when the team is filing
    // them, and from the reports people fill in otherwise.
    const fromFiles = url.searchParams.get('mode') === 'files'

    const people = store.people(dept.id)
    const reports = store.reportsForPeriod(periodType, periodKey)
    const files = store.collectedFiles(periodType, periodKey)
    const positions = fromFiles
      ? store.artifactProgress(periodType, periodKey, dept.id)
      : store.progress(periodType, periodKey, dept.id)

    const items: OverviewItem[] = reports
      .map((report) => {
        const person = people.find((p) => p.id === report.personId)
        const position = store.positionById(person?.positionId ?? '')
        const answered = report.items.filter((i) => i.status !== 'not_applicable')
        const percent = answered.length
          ? Math.round(
              (answered.reduce((sum, i) => sum + (i.percent ?? 0), 0) / answered.length) * 10,
            ) / 10
          : 0
        const status = answered.length
          ? percent >= 100
            ? '全部完成'
            : percent >= 60
              ? '大部分完成'
              : percent > 0
                ? '部分完成'
                : '尚未开始'
          : '未填报'
        return {
          positionName: position?.name ?? '',
          level: position?.level ?? 'specialist_staff',
          status,
          summary: report.summary,
          blockers: report.blockers,
          percent,
          submittedAt: report.submittedAt,
        }
      })
      .sort((a, b) => a.positionName.localeCompare(b.positionName, 'zh'))

    const filePeople = store.artifactProgressByPerson(periodType, periodKey, dept.id)
    // The people who filed at least one file, for the reporting rate.
    const filed = new Set(files.map((f) => f.personId).filter(Boolean))
    const unmatchedCount = files.filter((f) => !(f.personId && f.artifactId)).length

    const taskList = store.tasks(periodType, periodKey, dept.id)
    const taskStats = {
      total: taskList.length,
      done: taskList.filter((t) => t.status === 'done').length,
      blocked: taskList.filter((t) => t.status === 'blocked').length,
      assigned: taskList.filter((t) => t.kind === 'assigned').length,
      recurring: taskList.filter((t) => t.kind === 'recurring').length,
    }

    sendHtml(
      res,
      renderOverview({
        departmentName: dept.name,
        periodType,
        periodKey,
        periodLabel,
        periods,
        positions,
        peopleCount: people.length,
        reportedCount: fromFiles ? filed.size : reports.length,
        tree: store.tree(dept.id),
        items,
        mode: fromFiles ? 'files' : 'reports',
        filePeople,
        unmatchedCount,
        tasks: taskStats,
      }),
    )
  }

  const ledger = (_req: IncomingMessage, res: ServerResponse) => {
    const dept = department()
    sendHtml(
      res,
      renderLedger({ departmentName: dept.name, tree: store.tree(dept.id) }),
    )
  }

  const collection = (_req: IncomingMessage, res: ServerResponse, url: URL) => {
    const dept = department()
    const periodType = parseType(url.searchParams.get('type'))
    const today = now()
    const current = periodFor(periodType, today)
    const periods = recentPeriods(periodType, today, 8)
    const requested = url.searchParams.get('period')
    const periodKey = periods.some((p) => p.key === requested) ? (requested as string) : current.key
    const periodLabel = periods.find((p) => p.key === periodKey)?.label ?? current.label

    const artifacts = store.artifacts()
    const positions = store.positions(dept.id)
    const people = store.people(dept.id)
    const files = store.collectedFiles(periodType, periodKey)

    const artifactViews = artifacts.map((artifact) => {
      const position = positions.find((p) => p.id === artifact.positionId)
      return {
        id: artifact.id,
        positionName: position?.name ?? '',
        artifact: artifact.artifact,
        keywords: artifact.keywords,
        dutyIds: artifact.dutyIds,
        note: artifact.note,
      }
    })

    const artifactById = new Map(artifacts.map((a) => [a.id, a]))
    const personById = new Map(people.map((p) => [p.id, p]))
    const positionName = (positionId: string): string =>
      positions.find((p) => p.id === positionId)?.name ?? ''

    // The file is credited to the post, not to a person; nobody is named.
    const fileViews = files.map((file) => {
      const person = file.personId ? personById.get(file.personId) : undefined
      const artifact = file.artifactId ? artifactById.get(file.artifactId) : undefined
      return {
        name: file.name,
        positionName: person ? positionName(person.positionId) : '',
        artifactName: artifact?.artifact ?? '',
        matchedKeyword: file.matchedKeyword,
        reason: file.reason,
        modifiedAt: file.modifiedAt,
      }
    })

    sendHtml(
      res,
      renderCollection({
        departmentName: dept.name,
        periodType,
        periodKey,
        periodLabel,
        source: '指定目录',
        collectedAt: files.length > 0 ? files[0]?.modifiedAt ?? '' : '',
        artifacts: artifactViews,
        files: fileViews.filter((f) => f.positionName && f.artifactName),
        unmatched: fileViews.filter((f) => !(f.positionName && f.artifactName)),
        people: store.artifactProgressByPerson(periodType, periodKey, dept.id),
      }),
    )
  }

  const links = (_req: IncomingMessage, res: ServerResponse) => {
    const dept = department()
    const positions = store.positions(dept.id)
    const people = store.people(dept.id)
    const accounts = new Map(store.accounts().map((account) => [account.positionId, account]))
    // The logins are listed by post; nobody is named.
    const posts = positions.map((position) => {
      const account = accounts.get(position.id)
      return {
        name: position.name,
        level: position.level,
        headcount: people.filter((person) => person.positionId === position.id).length,
        username: account?.username ?? '（待生成）',
        password: account?.passwordPlain ?? '',
      }
    })
    sendHtml(res, renderLinks({ departmentName: dept.name, posts }))
  }

  const reportForm = (req: IncomingMessage, res: ServerResponse, url: URL) => {
    const token = url.searchParams.get('token') ?? ''
    if (!token) {
      sendHtml(res, renderWelcome({ departmentName: department().name }))
      return
    }
    const person = store.personByToken(token)
    if (!person) {
      sendHtml(
        res,
        layout({
          title: '链接无效',
          active: 'report',
          body: `<div class="page-head"><div><h1>链接无效</h1><p class="muted">这个填报链接无法识别，请向部门负责人索取最新链接。</p></div></div>
<div class="card"><a class="btn" href="/">返回总览</a></div>`,
        }),
        404,
      )
      return
    }

    const position = store.positionById(person.positionId)
    const periodType = parseType(url.searchParams.get('type'))
    const today = now()
    const current = periodFor(periodType, today)
    const periods = recentPeriods(periodType, today, 8)
    const requested = url.searchParams.get('period')
    const periodKey = periods.some((p) => p.key === requested) ? (requested as string) : current.key
    const periodLabel = periods.find((p) => p.key === periodKey)?.label ?? current.label
    const range = periodRange(periodType, today)

    sendHtml(
      res,
      renderReportForm({
        positionName: position?.name ?? '',
        periodType,
        periodKey,
        periodLabel,
        rangeStart: range.start,
        rangeEnd: range.end,
        periods,
        duties: position?.duties ?? [],
        existing: store.reportFor(person.id, periodType, periodKey),
        token: person.token,
        tokenField: 'token',
        saved: url.searchParams.get('saved') === '1',
      }),
    )
  }

  const submitReport = async (req: IncomingMessage, res: ServerResponse) => {
    const form = await readBody(req)
    const token = form.get('token') ?? ''
    const person = store.personByToken(token)
    if (!person) {
      sendText(res, '链接无效', 404)
      return
    }
    const periodType = parseType(form.get('periodType'))
    // Only accept a period the person could have picked on the form.
    const today = now()
    const periods = recentPeriods(periodType, today, 8)
    const requestedKey = form.get('periodKey') ?? ''
    const period = periods.find((p) => p.key === requestedKey)
    if (!period) {
      sendText(res, '填报周期无效', 400)
      return
    }
    const periodKey = period.key
    const periodLabel = period.label
    const position = store.positionById(person.positionId)
    const duties = position?.duties ?? []

    const items: ReportItem[] = []
    for (const duty of duties) {
      const status = form.get(`status:${duty.id}`) as ProgressStatus | null
      const rawPercent = form.get(`percent:${duty.id}`)
      const percentRaw = rawPercent === null ? '' : String(rawPercent).trim()
      const percent = percentRaw === '' ? null : Math.max(0, Math.min(100, Number(percentRaw)))
      items.push({
        dutyId: duty.id,
        status: status && PROGRESS_STATUSES.includes(status) ? status : 'not_started',
        percent: percent !== null && Number.isFinite(percent) ? percent : null,
        note: (form.get(`note:${duty.id}`) ?? '').toString().slice(0, 2000),
      })
    }

    store.saveReport({
      personId: person.id,
      periodType,
      periodKey,
      periodLabel,
      summary: (form.get('summary') ?? '').toString().slice(0, 5000),
      blockers: (form.get('blockers') ?? '').toString().slice(0, 5000),
      nextPlan: (form.get('nextPlan') ?? '').toString().slice(0, 5000),
      items,
    })

    redirect(res, `/report?token=${encodeURIComponent(token)}&type=${periodType}&period=${encodeURIComponent(periodKey)}&saved=1`)
  }

  const personReport = (_req: IncomingMessage, res: ServerResponse, url: URL) => {
    const id = url.searchParams.get('person') ?? ''
    const person = store.personById(id)
    if (!person) {
      sendText(res, '未找到该人员', 404)
      return
    }
    const periodType = parseType(url.searchParams.get('type'))
    const today = now()
    const current = periodFor(periodType, today)
    const periodKey = url.searchParams.get('period') ?? current.key
    const report = store.reportFor(person.id, periodType, periodKey)
    if (!report) {
      sendText(res, '该人员在此周期还没有提交填报', 404)
      return
    }
    const position = store.positionById(person.positionId)
    const duties = new Map((position?.duties ?? []).map((d) => [d.id, d.text]))
    sendHtml(
      res,
      renderPersonReport({
        positionName: position?.name ?? '',
        report,
        duties,
      }),
    )
  }

  const styleSheet = async (_req: IncomingMessage, res: ServerResponse) => {
    const css = await readFile(join(HERE, 'style.css'), 'utf8').catch(() => '')
    const body = Buffer.from(css, 'utf8')
    res.writeHead(200, {
      'content-type': 'text/css; charset=utf-8',
      'content-length': body.byteLength,
      'cache-control': 'no-store',
    })
    res.end(body)
  }

  // -------------------------------------------------------------- tasks

  /** The posts of the department, as the task list needs them. */
  const taskPositions = (departmentId: string): TaskPositionNode[] => {
    const positions = store.positions(departmentId)
    const people = store.people(departmentId)
    const toNode = (parentId: string | null): TaskPositionNode[] =>
      positions
        .filter((p) => p.parentId === parentId)
        .map((p) => ({
          id: p.id,
          name: p.name,
          level: p.level,
          headcount: p.headcountNote,
          people: people
            .filter((person) => person.positionId === p.id)
            .map((person) => ({ id: person.id, name: person.name })),
          children: toNode(p.id),
        }))
    return toNode(null)
  }

  /** Every post of the department, with the posts under each one. */
  const allTaskPositions = (departmentId: string): TaskPositionNode[] => {
    const tree = taskPositions(departmentId)
    const flat: TaskPositionNode[] = []
    const walk = (nodes: TaskPositionNode[]): void => {
      for (const node of nodes) {
        flat.push(node)
        walk(node.children)
      }
    }
    walk(tree)
    return flat
  }

  /** The periods a task list can be looked at. */
  const taskPeriods = (periodType: PeriodType) => recentPeriods(periodType, now(), 8)

  /**
   * The task list of a period.
   *
   * The fixed tasks of the period are made first, so a post's list is there
   * even before anyone touches it.
   */
  const taskList = (req: IncomingMessage, res: ServerResponse, url: URL) => {
    const dept = department()
    const periodType = parseType(url.searchParams.get('type'))
    const periods = taskPeriods(periodType)
    const current = periods[0] ?? { key: '', label: '' }
    const requested = url.searchParams.get('period')
    const periodKey = periods.some((p) => p.key === requested) ? (requested as string) : current.key
    const periodLabel = periods.find((p) => p.key === periodKey)?.label ?? current.label

    store.materialiseRecurringTasks(dept.id, periodType, periodKey, periodLabel, now().toISOString())

    const tree = taskPositions(dept.id)
    const positionId = url.searchParams.get('position') ?? ''
    const viewer = store.personByToken(url.searchParams.get('token') ?? '')
    const viewerPosition = viewer ? store.positionById(viewer.positionId) : undefined
    // A superior gives tasks out: the posts directly under their own.
    const assignable = viewerPosition ? assignablePosts(tree, viewerPosition.id) : []
    // Anyone with people directly under them gives tasks out.
    const viewerCanAssign = viewerPosition ? assignable.length > 0 : false

    const wanted = positionId
      ? store.positionById(positionId)
      : undefined
    const tasks = store
      .taskViews(periodType, periodKey, dept.id)
      .filter((task) => (wanted ? task.positionId === wanted.id : true))

    const recurring = wanted
      ? store
          .recurringTasks(dept.id)
          .filter((task) => task.positionId === wanted.id)
          .map((task) => {
            const duty = task.dutyId ? store.duties(wanted.id).find((d) => d.id === task.dutyId) : undefined
            const artifact = task.artifactId
              ? store.artifacts().find((a) => a.id === task.artifactId)
              : undefined
            return {
              id: task.id,
              title: task.title,
              detail: task.detail,
              cadence: task.cadence,
              dutyText: duty?.text ?? '',
              artifactName: artifact?.artifact ?? '',
            }
          })
      : []

    sendHtml(
      res,
      renderTaskList({
        departmentName: dept.name,
        periodType,
        periodKey,
        periodLabel,
        periods,
        position: wanted
          ? {
              id: wanted.id,
              name: wanted.name,
              level: wanted.level,
              headcount: wanted.headcountNote,
              people: store
                .people(dept.id)
                .filter((p) => p.positionId === wanted.id)
                .map((p) => ({ id: p.id, name: p.name })),
            }
          : undefined,
        positions: allTaskPositions(dept.id).map((p) => ({
          id: p.id,
          name: p.name,
          level: p.level,
          headcount: p.headcount,
          people: p.people,
        })),
        tasks,
        recurring,
        viewerPositionName: viewerPosition?.name,
        viewerToken: viewer?.token,
        viewerCanAssign,
        assignablePositions: assignable,
      }),
    )
  }

  /** Give a task to a person. */
  const assignTask = async (req: IncomingMessage, res: ServerResponse) => {
    const form = await readBody(req)
    const viewer = store.personByToken(form.get('token') ?? '')
    if (!viewer) {
      sendText(res, '链接无效', 404)
      return
    }
    const dept = department()
    const viewerPosition = store.positionById(viewer.positionId)
    const tree = taskPositions(dept.id)
    const assignable = viewerPosition ? assignablePosts(tree, viewerPosition.id) : []
    // The task is given to a post, not to a person; everyone holding it gets it.
    const positionId = form.get('positionId') ?? ''
    const post = assignable.find((p) => p.id === positionId)
    if (!post) {
      sendText(res, '不能给这个岗位下达任务', 403)
      return
    }
    if (post.people.length === 0) {
      sendText(res, '这个岗位暂时没有人', 400)
      return
    }

    const periodType = parseType(form.get('periodType'))
    const periods = taskPeriods(periodType)
    const periodKey = form.get('periodKey') ?? ''
    const period = periods.find((p) => p.key === periodKey)
    if (!period) {
      sendText(res, '周期无效', 400)
      return
    }

    const title = (form.get('title') ?? '').toString().trim().slice(0, 200)
    if (!title) {
      sendText(res, '任务名称不能为空', 400)
      return
    }

    // Everyone holding the post gets the task.
    const stamp = Date.now().toString(36)
    for (const member of post.people) {
      store.createTask({
        id: `task-${member.id}-${stamp}`,
        departmentId: dept.id,
        kind: 'assigned',
        title,
        detail: (form.get('detail') ?? '').toString().slice(0, 1000),
        personId: member.id,
        positionId: post.id,
        periodType,
        periodKey: period.key,
        periodLabel: period.label,
        assignedById: viewer.id,
        recurringTaskId: null,
        dutyId: null,
        artifactId: null,
        createdAt: now().toISOString(),
      })
    }

    const target = accessOf(viewer, viewerPosition, leaderGrants(db.raw)) === 'leader'
      ? `/tasks?type=${periodType}&period=${encodeURIComponent(period.key)}&token=${encodeURIComponent(viewer.token)}`
      : `/my-tasks?type=${periodType}&period=${encodeURIComponent(period.key)}&token=${encodeURIComponent(viewer.token)}`
    redirect(res, target)
  }

  /** Set up a fixed task for a post. */
  const createRecurring = async (req: IncomingMessage, res: ServerResponse) => {
    const form = await readBody(req)
    const viewer = store.personByToken(form.get('token') ?? '')
    if (!viewer) {
      sendText(res, '链接无效', 404)
      return
    }
    const dept = department()
    const tree = taskPositions(dept.id)
    const viewerPosition = store.positionById(viewer.positionId)
    const assignable = viewerPosition ? assignablePosts(tree, viewerPosition.id) : []
    const positionId = form.get('positionId') ?? ''
    if (!assignable.some((post) => post.id === positionId)) {
      sendText(res, '不能给这个岗位设固定任务', 403)
      return
    }
    const title = (form.get('title') ?? '').toString().trim().slice(0, 200)
    if (!title) {
      sendText(res, '任务名称不能为空', 400)
      return
    }
    const cadence = parseType(form.get('cadence'))
    store.createRecurringTask({
      id: `rec-${positionId}-${Date.now().toString(36)}`,
      departmentId: dept.id,
      positionId,
      title,
      detail: (form.get('detail') ?? '').toString().slice(0, 1000),
      cadence,
      dutyId: null,
      artifactId: null,
      createdAt: now().toISOString(),
    })
    const target = accessOf(viewer, viewerPosition, leaderGrants(db.raw)) === 'leader'
      ? `/tasks?type=${cadence}&position=${encodeURIComponent(positionId)}&token=${encodeURIComponent(viewer.token)}`
      : `/my-tasks?type=${cadence}&token=${encodeURIComponent(viewer.token)}`
    redirect(res, target)
  }

  /** A person's own task list. */
  const myTasks = (req: IncomingMessage, res: ServerResponse, url: URL) => {
    const token = url.searchParams.get('token') ?? ''
    if (!token) {
      sendHtml(res, renderWelcome({ departmentName: department().name }))
      return
    }
    const person = store.personByToken(token)
    if (!person) {
      sendText(res, '链接无效', 404)
      return
    }
    const dept = department()
    const periodType = parseType(url.searchParams.get('type'))
    const periods = taskPeriods(periodType)
    const current = periods[0] ?? { key: '', label: '' }
    const requested = url.searchParams.get('period')
    const periodKey = periods.some((p) => p.key === requested) ? (requested as string) : current.key
    const periodLabel = periods.find((p) => p.key === periodKey)?.label ?? current.label
    store.materialiseRecurringTasks(dept.id, periodType, periodKey, periodLabel, now().toISOString())
    const position = store.positionById(person.positionId)
    const assignable = position ? assignablePosts(taskPositions(dept.id), position.id) : []
    // A post account sees the tasks of everyone in the post.
    const account = accountOf(req)
    const postPeople =
      account && account.positionId === person.positionId
        ? store.people(dept.id).filter((p) => p.positionId === person.positionId)
        : [person]
    sendHtml(
      res,
      renderMyTasks({
        positionName: position?.name ?? '',
        periodType,
        periodKey,
        periodLabel,
        periods,
        tasks: store
          .taskViews(periodType, periodKey, dept.id)
          .filter((t) => postPeople.some((p) => p.id === t.personId)),
        assignablePositions: assignable,
        viewerToken: person.token,
      }),
    )
  }

  /** Move a task on. */
  const updateMyTask = async (req: IncomingMessage, res: ServerResponse) => {
    const form = await readBody(req)
    const task = store.task(form.get('token') ?? '')
    if (!task) {
      sendText(res, '任务不存在', 404)
      return
    }
    const status = (form.get('status') ?? 'open') as TaskStatus
    const allowed: TaskStatus[] = ['open', 'in_progress', 'done', 'blocked', 'cancelled']
    store.updateTask(
      task.id,
      allowed.includes(status) ? status : 'open',
      (form.get('note') ?? '').toString().slice(0, 2000),
      now().toISOString(),
    )
    redirect(res, `/my-tasks?type=${task.periodType}&period=${encodeURIComponent(task.periodKey)}`)
  }

  // ------------------------------------------------------------- access

  /**
   * Who the caller is and what they may see.
   *
   * A person in a post sees only their own work. The manager, the deputy
   * manager and the company leaders named by the department head see the whole
   * department.
   */
  const viewerOf = (
    token: string | null,
  ): { person?: Person; position?: Position; role: AccessRole; account?: Account } => {
    const person = token ? store.personByToken(token) : undefined
    if (person) {
      const position = store.positionById(person.positionId)
      return {
        person,
        position,
        role: accessOf(person, position, leaderGrants(db.raw)),
      }
    }
    // A post account: the account's post and the first person in it.
    const account = token ? store.accountByToken(token) : undefined
    if (account) {
      const position = store.positionById(account.positionId)
      const postPerson = store.people().find((p) => p.positionId === account.positionId)
      return {
        person: postPerson,
        position,
        role: postPerson && position ? accessOf(postPerson, position, leaderGrants(db.raw)) : 'member',
        account,
      }
    }
    return { role: 'member' }
  }

  /** The page a member without the wider access gets. */
  const memberOnly = (role: AccessRole, res: ServerResponse): boolean => {
    if (role === 'leader') return false
    sendHtml(
      res,
      layout({
        title: '没有权限',
        active: '',
        body: `<div class="page-head"><div><h1>没有权限查看这一页</h1>
<p class="muted">这一页是部门整体情况，只有经理、副经理和公司领导可以查看。你可以打开自己的任务清单和填报。</p>
</div></div>
<div class="card">
  <a class="btn btn-primary" href="/my-tasks">我的任务</a>
  <a class="btn" href="/report">我的填报</a>
</div>`,
      }),
      403,
    )
    return true
  }

  // ----------------------------------------------------------------- api

  /**
   * The JSON the mini program reads.
   *
   * The pages the web shows are rendered on the server; the mini program asks
   * for the same information as data, so both read one source of truth.
   */
  const api = async (
    req: IncomingMessage,
    res: ServerResponse,
    url: URL,
    route: string,
    method: string,
  ): Promise<void> => {
    if (method === 'OPTIONS') {
      sendJson(res, { ok: true })
      return
    }
    const token = url.searchParams.get('token') ?? ''
    const dept = department()
    const periods = (type: PeriodType) => recentPeriods(type, now(), 8)
    const pickPeriod = (type: PeriodType): { key: string; label: string } => {
      const list = periods(type)
      const asked = url.searchParams.get('period')
      return list.find((p) => p.key === asked) ?? list[0] ?? { key: '', label: '' }
    }

    // Who the caller is, and what they may see.
    // The mini program logs in with a post account and gets its session token.
    if (route === '/api/login' && method === 'POST') {
      const form = await readBody(req)
      const username = (form.get('username') ?? '').toString().trim()
      const password = (form.get('password') ?? '').toString()
      const account = username ? store.accountByUsername(username) : undefined
      if (!account || !verifyPassword(password, account.passwordHash)) {
        sendJson(res, { ok: false, error: '账户或密码不对' }, 401)
        return
      }
      const position = store.positionById(account.positionId)
      const person = position ? store.people().find((p) => p.positionId === position.id) : undefined
      const role = person && position ? accessOf(person, position, leaderGrants(db.raw)) : 'member'
      sendJson(res, {
        ok: true,
        token: account.token,
        person: person ? { id: person.id, name: person.name } : null,
        position: position ? { id: position.id, name: position.name, level: position.level } : null,
        role,
      })
      return
    }

    if (route === '/api/me' && method === 'GET') {
      const { person, position, role } = viewerOf(token)
      if (!person) {
        sendJson(res, { ok: false, error: '链接无效' }, 404)
        return
      }
      sendJson(res, {
        ok: true,
        person: { id: person.id, name: person.name },
        position: { id: position?.id ?? '', name: position?.name ?? '', level: position?.level ?? '' },
        role,
      })
      return
    }

    if (route === '/api/periods' && method === 'GET') {
      const type = parseType(url.searchParams.get('type'))
      sendJson(res, { ok: true, type, periods: periods(type) })
      return
    }

    // A person's own task list; a post account sees the whole post's tasks.
    if (route === '/api/my-tasks' && method === 'GET') {
      const { person, position, account } = viewerOf(token)
      if (!person) {
        sendJson(res, { ok: false, error: '链接无效' }, 404)
        return
      }
      const type = parseType(url.searchParams.get('type'))
      const period = pickPeriod(type)
      store.materialiseRecurringTasks(dept.id, type, period.key, period.label, now().toISOString())
      const postPeople =
        account && position
          ? store.people().filter((p) => p.positionId === position.id)
          : [person]
      const tasks = store
        .taskViews(type, period.key, dept.id)
        .filter((t) => postPeople.some((p) => p.id === t.personId))
      sendJson(res, {
        ok: true,
        position: position?.name ?? '',
        periodType: type,
        periodKey: period.key,
        periodLabel: period.label,
        tasks: tasks.map((t) => ({
          id: t.id,
          title: t.title,
          detail: t.detail || t.dutyText || t.artifactName,
          kind: t.kind,
          kindLabel: TASK_KIND_LABELS[t.kind],
          status: t.status,
          statusLabel: TASK_STATUS_LABELS[t.status],
          note: t.note,
        })),
      })
      return
    }

    // A person moves their own task on.
    if (route === '/api/my-tasks/status' && method === 'POST') {
      const form = await readBody(req)
      // A POST carries who is asking in its body; a GET carries it in the query.
      const caller = form.get('token') ?? token
      const task = store.task(form.get('id') ?? '')
      if (!task) {
        sendJson(res, { ok: false, error: '任务不存在' }, 404)
        return
      }
      const owner = store.personByToken(caller)
      if (!owner || owner.id !== task.personId) {
        sendJson(res, { ok: false, error: '这不是你的任务' }, 403)
        return
      }
      const status = (form.get('status') ?? 'open') as TaskStatus
      const allowed: TaskStatus[] = ['open', 'in_progress', 'done', 'blocked', 'cancelled']
      store.updateTask(
        task.id,
        allowed.includes(status) ? status : 'open',
        (form.get('note') ?? '').toString().slice(0, 2000),
        now().toISOString(),
      )
      const updated = store.task(task.id)
      sendJson(res, {
        ok: true,
        task: updated
          ? { id: updated.id, status: updated.status, statusLabel: TASK_STATUS_LABELS[updated.status], note: updated.note }
          : null,
      })
      return
    }

    // The department as a whole, for the leaders.
    if (route === '/api/overview' && method === 'GET') {
      const { role } = viewerOf(token)
      if (role !== 'leader') {
        sendJson(res, { ok: false, error: '只有领导可以查看部门整体情况' }, 403)
        return
      }
      const type = parseType(url.searchParams.get('type'))
      const period = pickPeriod(type)
      const people = store.people(dept.id)
      const files = store.collectedFiles(type, period.key)
      const reports = store.reportsForPeriod(type, period.key)
      const positions = store.artifactProgress(type, period.key, dept.id)
      const tasks = store.tasks(type, period.key, dept.id)
      sendJson(res, {
        ok: true,
        periodType: type,
        periodKey: period.key,
        periodLabel: period.label,
        peopleCount: people.length,
        reportedCount: new Set(files.map((f) => f.personId).filter(Boolean)).size,
        reportCount: reports.length,
        unmatchedFiles: files.filter((f) => !(f.personId && f.artifactId)).length,
        tasks: {
          total: tasks.length,
          done: tasks.filter((t) => t.status === 'done').length,
          blocked: tasks.filter((t) => t.status === 'blocked').length,
        },
        positions: positions
          .filter((p) => p.total > 0)
          .map((p) => ({
            name: p.positionName,
            level: p.level,
            levelLabel: LEVEL_LABELS[p.level],
            reported: p.reported,
            total: p.total,
            done: p.done,
            percent: p.percent,
          })),
      })
      return
    }

    // The department ledger, by post and without names, for the leaders.
    if (route === '/api/ledger' && method === 'GET') {
      const { role } = viewerOf(token)
      if (role !== 'leader') {
        sendJson(res, { ok: false, error: '只有领导可以查看岗位职责台账' }, 403)
        return
      }
      const positions: {
        id: string
        name: string
        level: string
        levelLabel: string
        depth: number
        peopleCount: number
        headcountNote: string
        instructions: string
        boundary: string
        note: string
        duties: { ordinal: number; text: string }[]
      }[] = []
      const walk = (nodes: PositionView[], depth: number): void => {
        for (const node of nodes) {
          positions.push({
            id: node.id,
            name: node.name,
            level: node.level,
            levelLabel: LEVEL_LABELS[node.level],
            depth,
            peopleCount: node.people.length,
            headcountNote: node.headcountNote,
            instructions: node.instructions,
            boundary: node.boundary,
            note: node.note,
            duties: node.duties.map((duty) => ({ ordinal: duty.ordinal, text: duty.text })),
          })
          walk(node.children, depth + 1)
        }
      }
      walk(store.tree(dept.id), 0)
      sendJson(res, { ok: true, departmentName: dept.name, positions })
      return
    }

    // The outcome files the collector placed, and the ones it could not, for the
    // leaders.
    if (route === '/api/collection' && method === 'GET') {
      const { role } = viewerOf(token)
      if (role !== 'leader') {
        sendJson(res, { ok: false, error: '只有领导可以查看成果采集' }, 403)
        return
      }
      const type = parseType(url.searchParams.get('type'))
      const period = pickPeriod(type)
      const positions = store.positions(dept.id)
      const people = store.people(dept.id)
      const artifacts = store.artifacts()
      const files = store.collectedFiles(type, period.key)
      const artifactById = new Map(artifacts.map((a) => [a.id, a]))
      const personById = new Map(people.map((p) => [p.id, p]))
      const positionName = (positionId: string): string =>
        positions.find((p) => p.id === positionId)?.name ?? ''
      const views = files.map((file) => {
        const person = file.personId ? personById.get(file.personId) : undefined
        const artifact = file.artifactId ? artifactById.get(file.artifactId) : undefined
        return {
          id: file.id,
          name: file.name,
          positionName: person ? positionName(person.positionId) : '',
          artifactName: artifact?.artifact ?? '',
          matchedKeyword: file.matchedKeyword,
          reason: file.reason,
          modifiedAt: file.modifiedAt,
          recognized: Boolean(person && artifact),
        }
      })
      const progress = store.artifactProgress(type, period.key, dept.id)
      sendJson(res, {
        ok: true,
        periodType: type,
        periodKey: period.key,
        periodLabel: period.label,
        collectedAt: files[0]?.modifiedAt ?? '',
        counts: {
          files: files.length,
          recognized: views.filter((v) => v.recognized).length,
          unmatched: views.filter((v) => !v.recognized).length,
        },
        positions: progress
          .filter((p) => p.total > 0)
          .map((p) => ({
            name: p.positionName,
            level: p.level,
            levelLabel: LEVEL_LABELS[p.level],
            reported: p.reported,
            total: p.total,
            done: p.done,
            percent: p.percent,
          })),
        files: views.filter((v) => v.recognized).slice(0, 30),
        unmatched: views.filter((v) => !v.recognized).slice(0, 30),
      })
      return
    }

    // The duties of my post, and what I answered for each of them.
    if (route === '/api/my-duties' && method === 'GET') {
      const person = store.personByToken(token)
      if (!person) {
        sendJson(res, { ok: false, error: '链接无效' }, 404)
        return
      }
      const type = parseType(url.searchParams.get('type'))
      const period = pickPeriod(type)
      const position = store.positionById(person.positionId)
      const report = store.reportFor(person.id, type, period.key)
      const answered = new Map((report?.items ?? []).map((item) => [item.dutyId, item]))
      sendJson(res, {
        ok: true,
        position: position?.name ?? '',
        instructions: position?.instructions ?? '',
        boundary: position?.boundary ?? '',
        periodType: type,
        periodKey: period.key,
        periodLabel: period.label,
        dismissed: report
          ? { summary: report.summary, blockers: report.blockers, nextPlan: report.nextPlan, submittedAt: report.submittedAt }
          : null,
        duties: (position?.duties ?? []).map((duty) => {
          const item = answered.get(duty.id)
          return {
            id: duty.id,
            ordinal: duty.ordinal,
            text: duty.text,
            status: item?.status ?? 'not_started',
            statusLabel: PROGRESS_LABELS[item?.status ?? 'not_started'],
            percent: item?.percent ?? null,
            note: item?.note ?? '',
          }
        }),
      })
      return
    }

    // A person files their weekly or monthly report.
    if (route === '/api/report' && method === 'POST') {
      const form = await readBody(req)
      const caller = form.get('token') ?? token
      const person = store.personByToken(caller)
      if (!person) {
        sendJson(res, { ok: false, error: '链接无效' }, 404)
        return
      }
      const type = parseType(form.get('periodType'))
      const period = periods(type).find((p) => p.key === (form.get('periodKey') ?? ''))
      if (!period) {
        sendJson(res, { ok: false, error: '周期无效' }, 400)
        return
      }
      const position = store.positionById(person.positionId)
      const duties = position?.duties ?? []
      let payload: unknown
      try {
        payload = JSON.parse(form.get('items') ?? '[]')
      } catch {
        sendJson(res, { ok: false, error: '填报内容格式不对' }, 400)
        return
      }
      const answered = new Map<string, { status?: string; percent?: number | null; note?: string }>()
      if (Array.isArray(payload)) {
        for (const entry of payload) {
          const row = entry as { id?: string; status?: string; percent?: number | null; note?: string }
          if (row && typeof row.id === 'string') answered.set(row.id, row)
        }
      }
      const items: ReportItem[] = duties.map((duty) => {
        const row = answered.get(duty.id)
        const status = (row?.status ?? 'not_started') as ProgressStatus
        const percent = row?.percent === null || row?.percent === undefined ? null : Number(row.percent)
        return {
          dutyId: duty.id,
          status: PROGRESS_STATUSES.includes(status) ? status : 'not_started',
          percent: percent !== null && Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null,
          note: String(row?.note ?? '').slice(0, 2000),
        }
      })
      store.saveReport({
        personId: person.id,
        periodType: type,
        periodKey: period.key,
        periodLabel: period.label,
        summary: String(form.get('summary') ?? '').slice(0, 5000),
        blockers: String(form.get('blockers') ?? '').slice(0, 5000),
        nextPlan: String(form.get('nextPlan') ?? '').slice(0, 5000),
        items,
      })
      sendJson(res, { ok: true, periodKey: period.key, periodLabel: period.label, items: items.length })
      return
    }

    // The posts a person may give a task to: the ones directly under their own.
    if (route === '/api/assignable' && method === 'GET') {
      const { person, position } = viewerOf(token)
      if (!person || !position) {
        sendJson(res, { ok: false, error: '链接无效' }, 404)
        return
      }
      const assignable = assignablePosts(taskPositions(dept.id), position.id)
      if (assignable.length === 0) {
        sendJson(res, { ok: false, error: '没有可以下达任务的岗位' }, 403)
        return
      }
      sendJson(res, {
        ok: true,
        positions: assignable.map((post) => ({
          id: post.id,
          name: post.name,
          headcount: post.people.length,
        })),
      })
      return
    }

    // A leader gives a task to a post.
    if (route === '/api/tasks/assign' && method === 'POST') {
      const form = await readBody(req)
      const caller = form.get('token') ?? token
      const viewer = store.personByToken(caller)
      const viewerPosition = viewer ? store.positionById(viewer.positionId) : undefined
      if (!viewer || !viewerPosition) {
        sendJson(res, { ok: false, error: '链接无效' }, 404)
        return
      }
      const post = assignablePosts(taskPositions(dept.id), viewerPosition.id).find(
        (p) => p.id === (form.get('positionId') ?? ''),
      )
      if (!post) {
        sendJson(res, { ok: false, error: '不能给这个岗位下达任务' }, 403)
        return
      }
      if (post.people.length === 0) {
        sendJson(res, { ok: false, error: '这个岗位暂时没有人' }, 400)
        return
      }
      const title = String(form.get('title') ?? '').trim().slice(0, 200)
      if (!title) {
        sendJson(res, { ok: false, error: '任务名称不能为空' }, 400)
        return
      }
      const type = parseType(form.get('periodType'))
      const period = periods(type).find((p) => p.key === (form.get('periodKey') ?? ''))
      if (!period) {
        sendJson(res, { ok: false, error: '周期无效' }, 400)
        return
      }
      const stamp = Date.now().toString(36)
      for (const member of post.people) {
        store.createTask({
          id: `task-${member.id}-${stamp}`,
          departmentId: dept.id,
          kind: 'assigned',
          title,
          detail: String(form.get('detail') ?? '').slice(0, 1000),
          personId: member.id,
          positionId: post.id,
          periodType: type,
          periodKey: period.key,
          periodLabel: period.label,
          assignedById: viewer.id,
          recurringTaskId: null,
          dutyId: null,
          artifactId: null,
          createdAt: now().toISOString(),
        })
      }
      sendJson(res, { ok: true, position: post.name, people: post.people.length })
      return
    }

    // A leader sets up a fixed task for a post.
    if (route === '/api/tasks/recurring' && method === 'POST') {
      const form = await readBody(req)
      const caller = form.get('token') ?? token
      const viewer = store.personByToken(caller)
      if (!viewer) {
        sendJson(res, { ok: false, error: '链接无效' }, 404)
        return
      }
      const viewerPosition = store.positionById(viewer.positionId)
      const assignable = viewerPosition ? assignablePosts(taskPositions(dept.id), viewerPosition.id) : []
      const post = assignable.find((p) => p.id === (form.get('positionId') ?? ''))
      if (!post) {
        sendJson(res, { ok: false, error: '不能给这个岗位设固定任务' }, 403)
        return
      }
      const title = String(form.get('title') ?? '').trim().slice(0, 200)
      if (!title) {
        sendJson(res, { ok: false, error: '任务名称不能为空' }, 400)
        return
      }
      const cadence = parseType(form.get('cadence'))
      store.createRecurringTask({
        id: `rec-${post.id}-${Date.now().toString(36)}`,
        departmentId: dept.id,
        positionId: post.id,
        title,
        detail: String(form.get('detail') ?? '').slice(0, 1000),
        cadence,
        dutyId: null,
        artifactId: null,
        createdAt: now().toISOString(),
      })
      sendJson(res, { ok: true, position: post.name, cadence, people: post.people.length })
      return
    }

    sendJson(res, { ok: false, error: '没有这个接口' }, 404)
  }

  const server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', options.baseUrl)
    const route = url.pathname
    const method = req.method ?? 'GET'

    try {
      // A link may be pasted whole; keep only the token out of it.
      const raw = url.searchParams.get('token') ?? ''
      const supplied = /([a-f0-9]{32})/i.exec(raw)?.[1] ?? raw.trim()
      if (supplied && supplied !== raw) url.searchParams.set('token', supplied)

      // The mini program and any other JSON caller read the same data. They
      // carry the token themselves, so the browser cookie does not apply.
      if (route.startsWith('/api/')) return void api(req, res, url, route, method)

      // The post accounts: log in, log out.
      if (route === '/login' && method === 'POST') return void login(req, res)
      if (route === '/logout' && method === 'GET') return logout(req, res)

      // Opening a link remembers whose link it is; later pages then work
      // without repeating the token in every address.
      const linked = supplied ? store.personByToken(supplied) : undefined
      if (linked) remember(res, linked.token)
      const saved = cookieOf(req, SESSION_COOKIE)
      const remembered = saved ? store.personByToken(saved) : undefined
      const token = linked?.token ?? remembered?.token ?? ''
      if (token && !url.searchParams.get('token')) url.searchParams.set('token', token)

      // Somebody who pasted a link in the welcome form goes straight to the
      // page that fits them: the department for a leader, their own page for
      // everyone else.
      if (route === '/enter' && method === 'GET') {
        // Only what was just pasted counts here; a browser that already
        // remembered someone else must not be sent on silently.
        const person = supplied ? store.personByToken(supplied) : undefined
        if (!person) {
          sendHtml(
            res,
            renderWelcome({
              departmentName: department().name,
              error: '这个链接无法识别，请向部门负责人索取最新链接。',
            }),
            404,
          )
          return
        }
        const position = store.positionById(person.positionId)
        const role = accessOf(person, position, leaderGrants(db.raw))
        redirect(res, role === 'leader' ? '/' : '/report')
        return
      }

      // The pages that show the department as a whole are for the leaders.
      const guarded = ['/', '/collection', '/ledger', '/links', '/tasks', '/person']
      if (guarded.includes(route) && method === 'GET') {
        if (!token) {
          // No link yet: ask for one rather than turn the visitor away.
          sendHtml(res, renderWelcome({ departmentName: department().name }))
          return
        }
        const { person, role } = viewerOf(token)
        if (!person) {
          sendHtml(
            res,
            renderWelcome({
              departmentName: department().name,
              error: '这个链接无法识别，请向部门负责人索取最新链接。',
            }),
            404,
          )
          return
        }
        if (memberOnly(role, res)) return
      }
      if (route === '/' && method === 'GET') return overview(req, res, url)
      if (route === '/collection' && method === 'GET') return collection(req, res, url)
      if (route === '/ledger' && method === 'GET') return ledger(req, res)
      if (route === '/links' && method === 'GET') return links(req, res)
      if (route === '/report' && method === 'GET') return reportForm(req, res, url)
      if (route === '/report' && method === 'POST') return void submitReport(req, res)
      if (route === '/person' && method === 'GET') return personReport(req, res, url)
      if (route === '/tasks' && method === 'GET') return taskList(req, res, url)
      if (route === '/tasks/assign' && method === 'POST') return void assignTask(req, res)
      if (route === '/tasks/recurring' && method === 'POST') return void createRecurring(req, res)
      if (route === '/my-tasks' && method === 'GET') return myTasks(req, res, url)
      if (route === '/my-tasks/status' && method === 'POST') return void updateMyTask(req, res)
      if (route === '/style.css' && method === 'GET') return void styleSheet(req, res)
      if (route === '/healthz' && method === 'GET') return sendText(res, 'ok')
      sendHtml(res, layout({ title: '404', active: '', body: '<h1>404 页面不存在</h1>' }), 404)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      sendHtml(
        res,
        layout({
          title: '出错',
          active: '',
          body: `<h1>服务出错</h1><p class="muted">${escapeHtml(message)}</p>`,
        }),
        500,
      )
    }
  })

  return server
}
