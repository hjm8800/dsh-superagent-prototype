import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { Store } from './store.ts'
import { PROGRESS_STATUSES, type PeriodType, type ProgressStatus, type ReportItem } from './types.ts'
import { periodFor, periodRange, recentPeriods } from './period.ts'
import {
  renderOverview,
  renderLedger,
  renderReportForm,
  renderLinks,
  renderPersonReport,
  renderCollection,
  type OverviewItem,
} from './views.ts'
import { escapeHtml, layout } from './html.ts'
import type { Department } from './types.ts'

/** HTTP layer: routes requests to the store and renders pages. */

export interface ServerOptions {
  store: Store
  /** Public base URL, used to build the links handed to employees. */
  baseUrl: string
  /** Fixed "now", so tests are deterministic. */
  now?: () => Date
}

const HERE = dirname(fileURLToPath(import.meta.url))

export function createTrackerServer(options: ServerOptions) {
  const { store } = options
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

  const redirect = (res: ServerResponse, location: string) => {
    res.writeHead(302, { location })
    res.end()
  }

  const readBody = async (req: IncomingMessage): Promise<URLSearchParams> => {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    return new URLSearchParams(Buffer.concat(chunks).toString('utf8'))
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
          personName: person?.name ?? report.personId,
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

    const fileViews = files.map((file) => {
      const person = file.personId ? personById.get(file.personId) : undefined
      const artifact = file.artifactId ? artifactById.get(file.artifactId) : undefined
      return {
        name: file.name,
        personName: person?.name ?? '',
        positionName: person ? positionName(person.positionId) : '',
        artifactName: artifact?.artifact ?? '',
        matchedKeyword: file.matchedKeyword,
        reason: file.reason,
        modifiedAt: file.modifiedAt,
        ownerHint: file.ownerName,
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
        files: fileViews.filter((f) => f.personName && f.artifactName),
        unmatched: fileViews.filter((f) => !(f.personName && f.artifactName)),
        people: store.artifactProgressByPerson(periodType, periodKey, dept.id),
      }),
    )
  }

  const links = (_req: IncomingMessage, res: ServerResponse) => {
    const dept = department()
    const positions = store.positions(dept.id)
    const people = store.people(dept.id)
    sendHtml(
      res,
      renderLinks({
        departmentName: dept.name,
        people: people.map((person) => {
          const position = positions.find((p) => p.id === person.positionId)
          return {
            id: person.id,
            name: person.name,
            positionName: position?.name ?? '',
            level: position?.level ?? 'specialist_staff',
            pending: person.pending,
            note: person.note,
            token: person.token,
            link: `${options.baseUrl}/report?token=${person.token}`,
          }
        }),
      }),
    )
  }

  const reportForm = (req: IncomingMessage, res: ServerResponse, url: URL) => {
    const token = url.searchParams.get('token') ?? ''
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
        personName: person.name,
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
        personName: person.name,
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

  const server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', options.baseUrl)
    const route = url.pathname
    const method = req.method ?? 'GET'

    try {
      if (route === '/' && method === 'GET') return overview(req, res, url)
      if (route === '/collection' && method === 'GET') return collection(req, res, url)
      if (route === '/ledger' && method === 'GET') return ledger(req, res)
      if (route === '/links' && method === 'GET') return links(req, res)
      if (route === '/report' && method === 'GET') return reportForm(req, res, url)
      if (route === '/report' && method === 'POST') return void submitReport(req, res)
      if (route === '/person' && method === 'GET') return personReport(req, res, url)
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
