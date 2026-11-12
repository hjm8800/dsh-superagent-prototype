import { escapeHtml, join, layout, pageHead, stat, bar, empty } from './html.ts'
import { LEVEL_LABELS, PROGRESS_LABELS, PERIOD_LABELS } from './types.ts'
import type { PeriodType, PositionView, Report, PositionProgress } from './types.ts'

/** Page templates for the tracker. */

export interface OverviewData {
  departmentName: string
  periodType: PeriodType
  periodKey: string
  periodLabel: string
  periods: { key: string; label: string }[]
  positions: PositionProgress[]
  peopleCount: number
  reportedCount: number
  tree: PositionView[]
  items: OverviewItem[]
  /**
   * How the completion was worked out: from the outcome files the team
   * filed, or from the reports people filled in.
   */
  mode: 'files' | 'reports'
  /** The completion per person, derived from the collected files. */
  filePeople?: {
    personId: string
    personName: string
    positionName: string
    files: number
    expected: number
    done: number
    percent: number
  }[]
  /** How many files the collector could not place. */
  unmatchedCount?: number
  /** The tasks of the period, for the dashboard. */
  tasks?: {
    total: number
    done: number
    blocked: number
    assigned: number
    recurring: number
  }
}

export interface OverviewItem {
  /** Nobody is named in the overview; everything is by post. */
  positionName: string
  level: string
  status: string
  summary: string
  blockers: string
  percent: number
  submittedAt: string
}

/** One expected output of a post. */
export interface ArtifactView {
  id: string
  positionName: string
  artifact: string
  keywords: string[]
  dutyIds: string[]
  note: string
}

/** A file the collector found, with what it was credited to. */
export interface CollectedFileView {
  name: string
  /** The post the file was credited to; nobody is named. */
  positionName: string
  artifactName: string
  matchedKeyword: string
  reason: string
  modifiedAt: string
}

export interface CollectionData {
  departmentName: string
  periodType: PeriodType
  periodKey: string
  periodLabel: string
  /** Where the files were read from. */
  source: string
  /** When the collection ran. */
  collectedAt: string
  artifacts: ArtifactView[]
  files: CollectedFileView[]
  /** Files that could not be credited. */
  unmatched: CollectedFileView[]
  /** Completion per person, derived from the files. */
  people: {
    personId: string
    personName: string
    positionName: string
    files: number
    expected: number
    done: number
    percent: number
  }[]
}

/**
 * The collection page: what the platform recognized, and what it did not.
 */
export function renderCollection(data: CollectionData): string {
  const artifactRows = data.artifacts
    .map(
      (a) => `<tr>
  <td>${escapeHtml(a.positionName)}</td>
  <td>${escapeHtml(a.artifact)}</td>
  <td class="note-cell">${escapeHtml(a.keywords.join('、'))}</td>
  <td class="num">${a.dutyIds.length}</td>
</tr>`,
    )
    .join('')

  const fileRows = data.files
    .map(
      (f) => `<tr>
  <td>${escapeHtml(f.name)}</td>
  <td>${escapeHtml(f.positionName || '—')}</td>
  <td>${escapeHtml(f.artifactName || '—')}</td>
  <td class="note-cell">${escapeHtml(f.matchedKeyword || '—')}</td>
  <td class="note-cell">${escapeHtml(f.modifiedAt.slice(0, 10))}</td>
</tr>`,
    )
    .join('')

  const unmatchedRows = data.unmatched
    .map(
      (f) => `<tr>
  <td>${escapeHtml(f.name)}</td>
  <td class="note-cell is-alert">${escapeHtml(f.reason)}</td>
</tr>`,
    )
    .join('')

  // The completion from the files, by post rather than by person.
  const byPost = new Map<string, { files: number; done: number; expected: number }>()
  for (const person of data.people) {
    const current = byPost.get(person.positionName) ?? { files: 0, done: 0, expected: 0 }
    current.files += person.files
    current.done += person.done
    current.expected += person.expected
    byPost.set(person.positionName, current)
  }
  const postRows = [...byPost.entries()]
    .map(([post, value]) => {
      const percent = value.expected === 0 ? 0 : Math.round((value.done / value.expected) * 1000) / 10
      return `<tr>
  <td>${escapeHtml(post)}</td>
  <td class="num">${value.files}</td>
  <td class="num">${value.done}/${value.expected}</td>
  <td class="wide">${bar(percent)}<span class="pct">${percent}%</span></td>
</tr>`
    })
    .join('')

  const body = `
${pageHead(
  `${data.departmentName} · 成果文件采集`,
  `${data.periodLabel}，来源：${escapeHtml(data.source)}，采集于 ${escapeHtml(data.collectedAt.slice(0, 16).replace('T', ' '))}`,
)}
<div class="stat-row">
  ${stat('认出的产出', String(data.files.length), '已归到岗位和产出')}
  ${stat('认不出的文件', String(data.unmatched.length), data.unmatched.length > 0 ? '需要确认' : '没有遗漏')}
  ${stat('预期产出项', String(data.artifacts.length), '各岗位应交的成果')}
  ${stat('有产出的岗位', String(byPost.size), '按岗位统计')}
</div>

<section class="card">
  <h2>按岗位统计</h2>
  ${
    postRows
      ? `<div class="table-wrap"><table class="table">
  <thead><tr><th>岗位</th><th>文件数</th><th>已交/应交</th><th>完成度</th></tr></thead>
  <tbody>${postRows}</tbody>
</table></div>`
      : empty('还没有采集到成果文件。')
  }
</section>

<section class="card">
  <h2>认出的产出</h2>
  ${
    fileRows
      ? `<div class="table-wrap"><table class="table">
  <thead><tr><th>文件</th><th>岗位</th><th>对应产出</th><th>命中关键词</th><th>修改时间</th></tr></thead>
  <tbody>${fileRows}</tbody>
</table></div>`
      : empty('还没有认出的成果文件。')
  }
</section>

${
  data.unmatched.length > 0
    ? `<section class="card">
  <h2>认不出的文件</h2>
  <p class="muted small">这些文件没有被算进完成情况。文件名里带上产出名即可自动归位。</p>
  <div class="table-wrap"><table class="table">
  <thead><tr><th>文件</th><th>原因</th></tr></thead>
  <tbody>${unmatchedRows}</tbody>
</table></div>
</section>`
    : ''
}

<section class="card">
  <h2>各岗位的预期产出</h2>
  <p class="muted small">文件名里出现任一关键词，就认为这项产出已交。</p>
  <div class="table-wrap"><table class="table">
  <thead><tr><th>岗位</th><th>预期产出</th><th>识别关键词</th><th>覆盖职责</th></tr></thead>
  <tbody>${artifactRows}</tbody>
</table></div>
</section>
`
  return layout({ title: '成果采集', active: 'collection', body })
}

export function renderOverview(data: OverviewData): string {
  const reported = data.reportedCount
  const total = data.peopleCount
  const rate = total === 0 ? 0 : Math.round((reported / total) * 100)
  const avg = data.positions.length === 0
    ? 0
    : Math.round((data.positions.reduce((sum, p) => sum + p.percent, 0) / data.positions.length) * 10) / 10
  const blocked = data.positions.reduce((sum, p) => sum + p.blocked, 0)
  const fromFiles = data.mode === 'files'
  const tasks = data.tasks ?? { total: 0, done: 0, blocked: 0, assigned: 0, recurring: 0 }

  const periodOptions = join(
    data.periods.map(
      (p) =>
        `<a class="chip${p.key === data.periodKey ? ' is-active' : ''}" href="/?type=${data.periodType}&period=${encodeURIComponent(p.key)}${fromFiles ? '&mode=files' : ''}">${escapeHtml(p.label)}</a>`,
    ),
  )

  const modeSwitch = `<div class="mode-switch">
  <a class="mode${fromFiles ? '' : ' is-active'}" href="/?type=${data.periodType}&period=${encodeURIComponent(data.periodKey)}&mode=reports">按填报统计</a>
  <a class="mode${fromFiles ? ' is-active' : ''}" href="/?type=${data.periodType}&period=${encodeURIComponent(data.periodKey)}&mode=files">按成果文件统计</a>
</div>`

  const rows = data.positions
    .filter((p) => p.total > 0)
    .map(
      (p) => `<tr>
  <td>${escapeHtml(p.positionName)}</td>
  <td><span class="tag tag-${p.level}">${escapeHtml(LEVEL_LABELS[p.level])}</span></td>
  <td class="num">${p.reported}/${p.total}</td>
  <td class="wide">${bar(p.percent)}<span class="pct">${p.percent}%</span></td>
  <td class="num">${p.done}</td>
  <td class="num${p.blocked > 0 ? ' is-alert' : ''}">${p.blocked}</td>
</tr>`,
    )
    .join('')

  const itemRows = data.items
    .map(
      (i) => `<tr>
  <td>${escapeHtml(i.positionName)}</td>
  <td>${escapeHtml(i.status)}</td>
  <td class="wide">${bar(i.percent)}<span class="pct">${i.percent}%</span></td>
  <td class="note-cell">${escapeHtml(i.summary || '—')}</td>
  <td class="note-cell${i.blockers ? ' is-alert' : ''}">${escapeHtml(i.blockers || '—')}</td>
</tr>`,
    )
    .join('')

  // The completion from the collected files, by post rather than by person.
  const fileByPost = new Map<string, { files: number; done: number; expected: number; percent: number }>()
  for (const person of data.filePeople ?? []) {
    const current = fileByPost.get(person.positionName) ?? { files: 0, done: 0, expected: 0, percent: 0 }
    current.files += person.files
    current.done += person.done
    current.expected += person.expected
    fileByPost.set(person.positionName, current)
  }
  const fileRows = [...fileByPost.entries()]
    .map(([post, value]) => {
      const percent = value.expected === 0 ? 0 : Math.round((value.done / value.expected) * 1000) / 10
      return `<tr>
  <td>${escapeHtml(post)}</td>
  <td class="num">${value.files}</td>
  <td class="num">${value.done}/${value.expected}</td>
  <td class="wide">${bar(percent)}<span class="pct">${percent}%</span></td>
</tr>`
    })
    .join('')

  const body = `
${pageHead(
  `${data.departmentName} · ${PERIOD_LABELS[data.periodType]}总览`,
  fromFiles
    ? `${data.periodLabel}，完成情况来自同事放到指定目录的成果文件。`
    : `${data.periodLabel}，共 ${total} 人应报，已报 ${reported} 人。`,
  fromFiles
    ? `<a class="btn" href="/collection">查看采集详情</a>`
    : `<a class="btn btn-primary" href="/report">我要填报</a>`,
)}
<div class="chips">${periodOptions}</div>
${modeSwitch}
<div class="stat-row">
  ${
    fromFiles
      ? `${stat('产出到位率', `${rate}%`, `${reported}/${total} 人交了成果`)}
  ${stat('平均完成度', `${avg}%`, '按岗位加权')}
  ${stat('认不出的文件', String(data.unmatchedCount ?? 0), (data.unmatchedCount ?? 0) > 0 ? '需要确认' : '没有遗漏')}
  ${stat('岗位数', String(data.positions.filter((p) => p.total > 0).length), '在编岗位')}`
      : `${stat('填报率', `${rate}%`, `${reported}/${total} 人已交`)}
  ${stat('平均完成度', `${avg}%`, '按岗位加权')}
  ${stat('受阻事项', String(blocked), blocked > 0 ? '需要关注' : '暂无受阻')}
  ${stat('岗位数', String(data.positions.filter((p) => p.total > 0).length), '在编岗位')}`
  }
</div>

<section class="card">
  <h2>按岗位汇总</h2>
  ${
    rows
      ? `<div class="table-wrap"><table class="table">
  <thead><tr><th>岗位</th><th>层级</th><th>已报/应报</th><th>完成度</th><th>已完成项</th><th>受阻项</th></tr></thead>
  <tbody>${rows}</tbody>
</table></div>`
      : empty('该周期还没有岗位数据。')
  }
</section>

${
  tasks.total > 0
    ? `<section class="card">
  <div class="card-head">
    <h2>任务清单</h2>
    <span class="muted small">${tasks.done}/${tasks.total} 完成${tasks.blocked > 0 ? `，${tasks.blocked} 项受阻` : ''}</span>
    ${bar(tasks.total === 0 ? 0 : (tasks.done / tasks.total) * 100)}
    <a class="btn" href="/tasks?type=${data.periodType}&period=${encodeURIComponent(data.periodKey)}">查看</a>
  </div>
  <div class="table-wrap"><table class="table">
  <thead><tr><th>上级下达</th><th>固定任务</th><th>已完成</th><th>受阻</th></tr></thead>
  <tbody><tr>
  <td class="num">${tasks.assigned}</td>
  <td class="num">${tasks.recurring}</td>
  <td class="num">${tasks.done}</td>
  <td class="num${tasks.blocked > 0 ? ' is-alert' : ''}">${tasks.blocked}</td>
</tr></tbody>
</table></div>
</section>`
    : ''
}

${
  fromFiles
    ? `<section class="card">
  <h2>按岗位统计</h2>
  ${
    fileRows
      ? `<div class="table-wrap"><table class="table">
  <thead><tr><th>岗位</th><th>成果文件</th><th>已交/应交</th><th>完成度</th></tr></thead>
  <tbody>${fileRows}</tbody>
</table></div>`
      : empty('该周期还没有采集到成果文件。')
  }
</section>`
    : `<section class="card">
  <h2>各岗位填报情况</h2>
  ${
    itemRows
      ? `<div class="table-wrap"><table class="table">
  <thead><tr><th>岗位</th><th>状态</th><th>完成度</th><th>本期小结</th><th>受阻</th></tr></thead>
  <tbody>${itemRows}</tbody>
</table></div>`
      : empty('该周期还没有人提交填报。')
  }
</section>`
}
`
  return layout({ title: '总览', active: 'overview', body })
}

export interface LedgerData {
  departmentName: string
  tree: PositionView[]
}

/**
 * The page a visitor sees before they have a link.
 *
 * The platform hands every person their own link, so a bare address has no
 * identity to work with; rather than turn the visitor away, the page asks for
 * the link.
 */
export function renderWelcome(data: { departmentName: string; error?: string }): string {
  const body = `
<div class="page-head"><div>
  <h1>${escapeHtml(data.departmentName)}工作台</h1>
  <p class="muted">岗位职责、任务清单与周／月完成情况</p>
</div></div>
${data.error ? `<div class="alert is-error">${escapeHtml(data.error)}</div>` : ''}
<section class="card">
  <h2>用岗位账户登录</h2>
  <p class="muted small">专责以上一人一个账户（账户名就是姓名）；业务员和班组整岗共用一个账户。账户和初始密码向部门负责人索取。</p>
  <form class="task-form" method="post" action="/login">
    <label class="field"><span>岗位账户</span>
      <input type="text" name="username" autocomplete="username" required autofocus>
    </label>
    <label class="field"><span>密码</span>
      <input type="password" name="password" autocomplete="current-password" required>
    </label>
    <div class="form-actions"><button class="btn btn-primary" type="submit">登录</button></div>
  </form>
</section>
`
  return layout({ title: '登录', active: '', body })
}

export function renderLedger(data: LedgerData): string {
  const renderNode = (node: PositionView): string => {
    // The ledger is by post; nobody is named here.
    const people = node.people.length
      ? `<p class="muted small">在岗 ${node.people.length} 人</p>`
      : '<p class="muted small">本岗位暂无人</p>'
    const duties = node.duties.length
      ? `<ol class="duties">${node.duties.map((d) => `<li>${escapeHtml(d.text)}</li>`).join('')}</ol>`
      : '<p class="muted">本岗位暂无单独列出的职责条目。</p>'
    const children = node.children.length
      ? `<div class="children">${node.children.map(renderNode).join('')}</div>`
      : ''
    return `<article class="position">
  <header class="position-head">
    <h3>${escapeHtml(node.name)}</h3>
    <span class="tag tag-${node.level}">${escapeHtml(LEVEL_LABELS[node.level])}</span>
    ${node.headcountNote ? `<span class="muted small">${escapeHtml(node.headcountNote)}</span>` : ''}
  </header>
  ${node.instructions ? `<p class="instructions">工作指引：${escapeHtml(node.instructions)}</p>` : ''}
  ${node.boundary ? `<p class="boundary">${escapeHtml(node.boundary).replace(/\n/g, '<br>')}</p>` : ''}
  ${node.note ? `<p class="muted small">${escapeHtml(node.note)}</p>` : ''}
  ${people ? `<div class="pos-people"><span class="label">在岗人员</span>${people}</div>` : ''}
  <div class="pos-duties"><span class="label">工作职责（${node.duties.length}）</span>${duties}</div>
  ${children}
</article>`
  }

  const body = `
${pageHead(`${data.departmentName} · 岗位职责台账`, '按层级展示每个岗位的职责范围与在岗人员。')}
<div class="ledger">${data.tree.map(renderNode).join('')}</div>
`
  return layout({ title: '岗位职责', active: 'ledger', body })
}

export interface ReportFormData {
  /** Nobody is named; the page is titled by the post. */
  positionName: string
  periodType: PeriodType
  periodKey: string
  periodLabel: string
  rangeStart: string
  rangeEnd: string
  periods: { key: string; label: string }[]
  duties: { id: string; text: string; ordinal: number }[]
  existing?: Report
  token: string
  tokenField: string
  error?: string
  /** Set after a successful submission. */
  saved?: boolean
}

const STATUS_ORDER = ['done', 'in_progress', 'not_started', 'blocked', 'not_applicable'] as const

export function renderReportForm(data: ReportFormData): string {
  const existingItems = new Map((data.existing?.items ?? []).map((i) => [i.dutyId, i]))

  const dutyBlocks = data.duties
    .map((duty) => {
      const current = existingItems.get(duty.id)
      const status = current?.status ?? 'not_started'
      const radios = STATUS_ORDER.map(
        (s) =>
          `<label class="status-opt"><input type="radio" name="status:${escapeHtml(duty.id)}" value="${s}"${status === s ? ' checked' : ''}><span>${escapeHtml(PROGRESS_LABELS[s])}</span></label>`,
      ).join('')
      return `<fieldset class="duty" data-duty-id="${escapeHtml(duty.id)}">
  <legend><span class="duty-no">${duty.ordinal}</span>${escapeHtml(duty.text)}</legend>
  <div class="status-row">${radios}</div>
  <div class="duty-fields">
    <label>完成百分比 <input type="number" name="percent:${escapeHtml(duty.id)}" min="0" max="100" step="5" placeholder="0-100" value="${current?.percent ?? ''}"></label>
    <label class="grow">说明 <input type="text" name="note:${escapeHtml(duty.id)}" value="${escapeHtml(current?.note ?? '')}" placeholder="做了什么 / 卡在哪里"></label>
  </div>
</fieldset>`
    })
    .join('')

  const periodOptions = join(
    data.periods.map(
      (p) =>
        `<a class="chip${p.key === data.periodKey ? ' is-active' : ''}" href="/report?token=${encodeURIComponent(data.token)}&type=${data.periodType}&period=${encodeURIComponent(p.key)}">${escapeHtml(p.label)}</a>`,
    ),
  )

  const body = `
${pageHead(
  `${data.positionName} · ${PERIOD_LABELS[data.periodType]}填报`,
  `${data.periodLabel}（${data.rangeStart} 至 ${data.rangeEnd}）`,
)}
${data.error ? `<div class="alert is-error">${escapeHtml(data.error)}</div>` : ''}
${data.saved ? `<div class="alert is-success">已提交${escapeHtml(PERIOD_LABELS[data.periodType])}，负责人可在总览页看到。</div>` : ''}
<div class="chips">${periodOptions}</div>
<form class="report-form" method="post" action="/report">
  <input type="hidden" name="token" value="${escapeHtml(data.token)}">
  <input type="hidden" name="periodType" value="${escapeHtml(data.periodType)}">
  <input type="hidden" name="periodKey" value="${escapeHtml(data.periodKey)}">
  <input type="hidden" name="periodLabel" value="${escapeHtml(data.periodLabel)}">

  <section class="card">
    <h2>逐条职责填报</h2>
    <p class="muted small">每条职责选择状态；「进行中」和「受阻」建议补充百分比与说明，便于上级汇总。</p>
    ${dutyBlocks || empty('本岗位暂无职责条目，请先在岗位职责台账中确认。')}
  </section>

  <section class="card">
    <h2>本期小结</h2>
    <label class="field"><span>本期完成情况</span><textarea name="summary" rows="3" placeholder="本期主要完成了哪些工作">${escapeHtml(data.existing?.summary ?? '')}</textarea></label>
    <label class="field"><span>遇到的问题</span><textarea name="blockers" rows="3" placeholder="需要协调或支持的事项">${escapeHtml(data.existing?.blockers ?? '')}</textarea></label>
    <label class="field"><span>下期计划</span><textarea name="nextPlan" rows="3" placeholder="下一周期的工作安排">${escapeHtml(data.existing?.nextPlan ?? '')}</textarea></label>
  </section>

  <div class="form-actions">
    <button class="btn btn-primary" type="submit">提交${escapeHtml(PERIOD_LABELS[data.periodType])}</button>
    <span class="muted small">重复提交会覆盖本次周期的填报内容。</span>
  </div>
</form>
`
  return layout({ title: '填报', active: 'report', body })
}

export interface LinksData {
  departmentName: string
  /** The login of each post; nobody is named. */
  posts: {
    name: string
    level: string
    headcount: number
    /** The account everyone in the post logs in with. */
    username: string
    password: string
  }[]
}

export function renderLinks(data: LinksData): string {
  const rows = data.posts
    .map(
      (p) => `<tr>
  <td>${escapeHtml(p.name)}</td>
  <td><span class="tag tag-${p.level}">${escapeHtml(LEVEL_LABELS[p.level as keyof typeof LEVEL_LABELS])}</span></td>
  <td class="num">${p.headcount}</td>
  <td><code>${escapeHtml(p.username)}</code></td>
  <td class="link-cell"><code>${escapeHtml(p.password || '—')}</code></td>
</tr>`,
    )
    .join('')

  const accounts = data.posts.map((p) => `${p.name}\t${p.username}\t${p.password}`)
  const body = `
${pageHead(
  `${data.departmentName} · 岗位账户`,
  '专责以上一人一个账户（账户名就是姓名）；业务员和班组整岗共用一个账户。把岗位、账户和初始密码发给对应同事，他们登录平台后即可填报周报／月报、查看任务。',
  `<button class="btn" type="button" id="copy-all">复制全部账户</button>`,
)}
<section class="card">
  ${
    rows
      ? `<div class="table-wrap"><table class="table">
  <thead><tr><th>岗位</th><th>层级</th><th>人数</th><th>账户</th><th>初始密码</th></tr></thead>
  <tbody>${rows}</tbody>
</table></div>`
      : empty('还没有岗位账户。')
  }
</section>
`
  return layout({
    title: '岗位账户',
    active: 'links',
    body,
    script: `
const copy = async (text, id, label) => {
  try {
    await navigator.clipboard.writeText(text);
    const btn = document.getElementById(id);
    btn.textContent = '已复制';
    setTimeout(() => { btn.textContent = label; }, 1500);
  } catch {
    alert('复制失败，请手动选中复制。');
  }
};
document.getElementById('copy-all')?.addEventListener('click', () => copy(${JSON.stringify(accounts.join('\n'))}, 'copy-all', '复制全部账户'));`,
  })
}

export interface PersonReportData {
  /** Nobody is named; the page is titled by the post. */
  positionName: string
  report: Report
  duties: Map<string, string>
}

export function renderPersonReport(data: PersonReportData): string {
  const items = data.report.items
    .map(
      (item) => `<tr>
  <td>${escapeHtml(data.duties.get(item.dutyId) ?? item.dutyId)}</td>
  <td><span class="status status-${item.status}">${escapeHtml(PROGRESS_LABELS[item.status])}</span></td>
  <td class="num">${item.percent ?? '—'}</td>
  <td class="note-cell">${escapeHtml(item.note || '—')}</td>
</tr>`,
    )
    .join('')

  const body = `
${pageHead(
  `${data.positionName} · ${PERIOD_LABELS[data.report.periodType]}`,
  `${data.report.periodLabel}，提交于 ${escapeHtml(data.report.submittedAt)}`,
)}
<div class="stat-row">
  ${stat('完成项', String(data.report.items.filter((i) => i.status === 'done').length))}
  ${stat('进行中', String(data.report.items.filter((i) => i.status === 'in_progress').length))}
  ${stat('受阻', String(data.report.items.filter((i) => i.status === 'blocked').length))}
</div>
<section class="card">
  <h2>逐条职责</h2>
  <div class="table-wrap"><table class="table">
    <thead><tr><th>职责</th><th>状态</th><th>完成度</th><th>说明</th></tr></thead>
    <tbody>${items}</tbody>
  </table></div>
</section>
<section class="card">
  <h2>本期小结</h2>
  <p class="note-cell">${escapeHtml(data.report.summary || '—')}</p>
  <h3 class="small-heading">遇到的问题</h3>
  <p class="note-cell${data.report.blockers ? ' is-alert' : ''}">${escapeHtml(data.report.blockers || '—')}</p>
  <h3 class="small-heading">下期计划</h3>
  <p class="note-cell">${escapeHtml(data.report.nextPlan || '—')}</p>
</section>
`
  return layout({ title: '填报详情', active: 'report', body })
}
