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
}

export interface OverviewItem {
  personName: string
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
  personName: string
  positionName: string
  artifactName: string
  matchedKeyword: string
  reason: string
  modifiedAt: string
  /** The uploader, when the file could not be credited to a person. */
  ownerHint?: string
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
  <td>${escapeHtml(f.personName || '—')}</td>
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
  <td>${escapeHtml(f.ownerHint ?? '')}</td>
  <td class="note-cell is-alert">${escapeHtml(f.reason)}</td>
</tr>`,
    )
    .join('')

  const personRows = data.people
    .filter((p) => p.expected > 0)
    .map(
      (p) => `<tr>
  <td>${escapeHtml(p.personName)}</td>
  <td>${escapeHtml(p.positionName)}</td>
  <td class="num">${p.files}</td>
  <td class="num">${p.done}/${p.expected}</td>
  <td class="wide">${bar(p.percent)}<span class="pct">${p.percent}%</span></td>
</tr>`,
    )
    .join('')

  const body = `
${pageHead(
  `${data.departmentName} · 成果文件采集`,
  `${data.periodLabel}，来源：${escapeHtml(data.source)}，采集于 ${escapeHtml(data.collectedAt.slice(0, 16).replace('T', ' '))}`,
)}
<div class="stat-row">
  ${stat('认出的产出', String(data.files.length), '已归到人和产出')}
  ${stat('认不出的文件', String(data.unmatched.length), data.unmatched.length > 0 ? '需要确认' : '没有遗漏')}
  ${stat('预期产出项', String(data.artifacts.length), '各岗位应交的成果')}
  ${stat('有产出的岗位', String(data.people.filter((p) => p.files > 0).length), '按人统计')}
</div>

<section class="card">
  <h2>按人统计</h2>
  ${
    personRows
      ? `<table class="table">
  <thead><tr><th>姓名</th><th>岗位</th><th>文件数</th><th>已交/应交</th><th>完成度</th></tr></thead>
  <tbody>${personRows}</tbody>
</table>`
      : empty('还没有采集到成果文件。')
  }
</section>

<section class="card">
  <h2>认出的产出</h2>
  ${
    fileRows
      ? `<table class="table">
  <thead><tr><th>文件</th><th>提交人</th><th>岗位</th><th>对应产出</th><th>命中关键词</th><th>修改时间</th></tr></thead>
  <tbody>${fileRows}</tbody>
</table>`
      : empty('还没有认出的成果文件。')
  }
</section>

${
  data.unmatched.length > 0
    ? `<section class="card">
  <h2>认不出的文件</h2>
  <p class="muted small">这些文件没有被算进完成情况。改名成「姓名＋产出名」即可自动归位。</p>
  <table class="table">
  <thead><tr><th>文件</th><th>上传人</th><th>原因</th></tr></thead>
  <tbody>${unmatchedRows}</tbody>
</table>
</section>`
    : ''
}

<section class="card">
  <h2>各岗位的预期产出</h2>
  <p class="muted small">文件名里出现任一关键词，就认为这项产出已交。</p>
  <table class="table">
  <thead><tr><th>岗位</th><th>预期产出</th><th>识别关键词</th><th>覆盖职责</th></tr></thead>
  <tbody>${artifactRows}</tbody>
</table>
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
  <td>${escapeHtml(i.personName)}</td>
  <td>${escapeHtml(i.positionName)}</td>
  <td>${escapeHtml(i.status)}</td>
  <td class="wide">${bar(i.percent)}<span class="pct">${i.percent}%</span></td>
  <td class="note-cell">${escapeHtml(i.summary || '—')}</td>
  <td class="note-cell${i.blockers ? ' is-alert' : ''}">${escapeHtml(i.blockers || '—')}</td>
</tr>`,
    )
    .join('')

  const fileRows = (data.filePeople ?? [])
    .filter((p) => p.expected > 0)
    .map(
      (p) => `<tr>
  <td>${escapeHtml(p.personName)}</td>
  <td>${escapeHtml(p.positionName)}</td>
  <td class="num">${p.files}</td>
  <td class="num">${p.done}/${p.expected}</td>
  <td class="wide">${bar(p.percent)}<span class="pct">${p.percent}%</span></td>
</tr>`,
    )
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
      ? `<table class="table">
  <thead><tr><th>岗位</th><th>层级</th><th>已报/应报</th><th>完成度</th><th>已完成项</th><th>受阻项</th></tr></thead>
  <tbody>${rows}</tbody>
</table>`
      : empty('该周期还没有岗位数据。')
  }
</section>

${
  fromFiles
    ? `<section class="card">
  <h2>按人统计</h2>
  ${
    fileRows
      ? `<table class="table">
  <thead><tr><th>姓名</th><th>岗位</th><th>成果文件</th><th>已交/应交</th><th>完成度</th></tr></thead>
  <tbody>${fileRows}</tbody>
</table>`
      : empty('该周期还没有采集到成果文件。')
  }
</section>`
    : `<section class="card">
  <h2>个人填报明细</h2>
  ${
    itemRows
      ? `<table class="table">
  <thead><tr><th>姓名</th><th>岗位</th><th>状态</th><th>完成度</th><th>本期小结</th><th>受阻</th></tr></thead>
  <tbody>${itemRows}</tbody>
</table>`
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

export function renderLedger(data: LedgerData): string {
  const renderNode = (node: PositionView): string => {
    const people = node.people.length
      ? `<ul class="people">${node.people
          .map((p) => `<li>${escapeHtml(p.name)}${p.pending ? '<span class="tag tag-pending">待定岗</span>' : ''}</li>`)
          .join('')}</ul>`
      : ''
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
  personName: string
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
  `${data.personName} · ${PERIOD_LABELS[data.periodType]}填报`,
  `${data.positionName} · ${data.periodLabel}（${data.rangeStart} 至 ${data.rangeEnd}）`,
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
  people: {
    id: string
    name: string
    positionName: string
    level: string
    pending: boolean
    note: string
    link: string
    token: string
  }[]
}

export function renderLinks(data: LinksData): string {
  const rows = data.people
    .map(
      (p) => `<tr>
  <td>${escapeHtml(p.name)}${p.pending ? ' <span class="tag tag-pending">待定岗</span>' : ''}</td>
  <td>${escapeHtml(p.positionName)}</td>
  <td><span class="tag tag-${p.level}">${escapeHtml(LEVEL_LABELS[p.level as keyof typeof LEVEL_LABELS])}</span></td>
  <td class="note-cell">${escapeHtml(p.note || '—')}</td>
  <td class="link-cell"><code>${escapeHtml(p.link)}</code></td>
</tr>`,
    )
    .join('')

  const body = `
${pageHead(
  `${data.departmentName} · 填报链接`,
  '把下面的专属链接发给对应同事，他们打开链接即可填报自己的周报／月报，无需登录。',
  `<button class="btn" type="button" id="copy-all">复制全部链接</button>`,
)}
<section class="card">
  ${
    rows
      ? `<table class="table">
  <thead><tr><th>姓名</th><th>岗位</th><th>层级</th><th>备注</th><th>专属链接</th></tr></thead>
  <tbody>${rows}</tbody>
</table>`
      : empty('暂无人员。')
  }
</section>
`
  return layout({
    title: '填报链接',
    active: 'links',
    body,
    script: `
document.getElementById('copy-all')?.addEventListener('click', async () => {
  const links = ${JSON.stringify(data.people.map((p) => p.link))};
  const text = links.join('\\n');
  try {
    await navigator.clipboard.writeText(text);
    const btn = document.getElementById('copy-all');
    btn.textContent = '已复制';
    setTimeout(() => { btn.textContent = '复制全部链接'; }, 1500);
  } catch {
    alert('复制失败，请手动选中复制。');
  }
});`,
  })
}

export interface PersonReportData {
  personName: string
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
  `${data.personName} · ${PERIOD_LABELS[data.report.periodType]}`,
  `${data.positionName} · ${data.report.periodLabel}，提交于 ${escapeHtml(data.report.submittedAt)}`,
)}
<div class="stat-row">
  ${stat('完成项', String(data.report.items.filter((i) => i.status === 'done').length))}
  ${stat('进行中', String(data.report.items.filter((i) => i.status === 'in_progress').length))}
  ${stat('受阻', String(data.report.items.filter((i) => i.status === 'blocked').length))}
</div>
<section class="card">
  <h2>逐条职责</h2>
  <table class="table">
    <thead><tr><th>职责</th><th>状态</th><th>完成度</th><th>说明</th></tr></thead>
    <tbody>${items}</tbody>
  </table>
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
