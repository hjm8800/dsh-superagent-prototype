import { escapeHtml, join, layout, pageHead, stat, bar, empty } from './html.ts'
import { LEVEL_LABELS, PERIOD_LABELS, TASK_KIND_LABELS, TASK_STATUS_LABELS } from './types.ts'
import type { PeriodType, TaskStatus, TaskView, TaskPhoto } from './types.ts'

/** The task list pages. */

/** A post, as the task list needs it. */
export interface TaskPosition {
  id: string
  name: string
  level: string
  headcount: string
  /** The people in the post, for assigning a task. */
  people: { id: string; name: string }[]
}

/** A post in the chain of command, with the posts under it. */
export interface TaskPositionNode extends TaskPosition {
  children: TaskPositionNode[]
}

export interface TaskListData {
  departmentName: string
  periodType: PeriodType
  periodKey: string
  periodLabel: string
  periods: { key: string; label: string }[]
  /** The post whose list is shown; all of them when none is picked. */
  position?: TaskPosition
  positions: TaskPosition[]
  tasks: TaskView[]
  /** The fixed tasks of the shown post. */
  recurring: {
    id: string
    title: string
    detail: string
    cadence: PeriodType
    dutyText: string
    artifactName: string
  }[]
  /** The post the person looking at the list belongs to. */
  viewerPositionName?: string
  /** The token of the person looking at the list. */
  viewerToken?: string
  /** Whether the viewer may give tasks out. */
  viewerCanAssign?: boolean
  /** The posts the viewer may give tasks to. */
  assignablePositions: TaskPosition[]
  /** The photos of each task, by task id. */
  photos?: Record<string, TaskPhoto[]>
  error?: string
}

const STATUS_ORDER: TaskStatus[] = ['open', 'in_progress', 'blocked', 'done', 'cancelled']

export function renderTaskList(data: TaskListData): string {
  const periodOptions = join(
    data.periods.map(
      (p) =>
        `<a class="chip${p.key === data.periodKey ? ' is-active' : ''}" href="/tasks?type=${data.periodType}&period=${encodeURIComponent(p.key)}${data.position ? `&position=${encodeURIComponent(data.position.id)}` : ''}">${escapeHtml(p.label)}</a>`,
    ),
  )

  const positionChips = join(
    data.positions.map(
      (p) =>
        `<a class="chip${data.position?.id === p.id ? ' is-active' : ''}" href="/tasks?type=${data.periodType}&period=${encodeURIComponent(data.periodKey)}&position=${encodeURIComponent(p.id)}">${escapeHtml(p.name)}</a>`,
    ),
  )
  // The list is by post, so the same task held by several people of a post
  // is shown once, with how many of them have it done.
  type RolledUp = TaskView & { holders: number; doneBy: number }
  const grouped = new Map<string, RolledUp[]>()
  for (const task of data.tasks) {
    const list = grouped.get(task.positionName) ?? []
    const key = `${task.kind}|${task.title}|${task.periodKey}`
    const existing = list.find((t) => `${t.kind}|${t.title}|${t.periodKey}` === key)
    if (existing) {
      existing.holders++
      if (task.status === 'done') existing.doneBy++
      continue
    }
    list.push({ ...task, holders: 1, doneBy: task.status === 'done' ? 1 : 0 })
    grouped.set(task.positionName, list)
  }

  const postBlocks = [...grouped.entries()]
    .map(([post, list]) => {
      const done = list.reduce((sum, t) => sum + t.doneBy, 0)
      const total = list.reduce((sum, t) => sum + t.holders, 0)
      const percent = total === 0 ? 0 : Math.round((done / total) * 1000) / 10
      const rows = list
        .map(
          (t) => `<tr>
  <td>${escapeHtml(t.title)}${t.holders > 1 ? ` <span class="tag">${t.doneBy}/${t.holders} 人完成</span>` : ''}</td>
  <td><span class="tag tag-kind-${t.kind}">${escapeHtml(TASK_KIND_LABELS[t.kind])}</span></td>
  <td class="note-cell">${escapeHtml(t.detail || t.dutyText || t.artifactName || '—')}</td>
  <td><span class="status status-${t.status}">${escapeHtml(TASK_STATUS_LABELS[t.status])}</span></td>
  <td class="photo-cell">${(data.photos?.[t.id] ?? [])
    .map((p) => `<a href="/photos/${p.id}" target="_blank"><img class="thumb" src="/photos/${p.id}" alt="照片"></a>`)
    .join('')}</td>
</tr>`,
        )
        .join('')
      return `<section class="card">
  <div class="card-head">
    <h2>${escapeHtml(post)}</h2>
    <span class="muted small">${done}/${total} 完成</span>
    ${bar(percent)}
  </div>
  <div class="table-wrap"><table class="table">
  <thead><tr><th>任务</th><th>来源</th><th>说明</th><th>状态</th><th>照片</th></tr></thead>
  <tbody>${rows}</tbody>
</table></div>
</section>`
    })
    .join('')

  const recurringRows = data.recurring
    .map(
      (r) => `<tr>
  <td>${escapeHtml(r.title)}</td>
  <td><span class="tag">${escapeHtml(PERIOD_LABELS[r.cadence])}</span></td>
  <td class="note-cell">${escapeHtml(r.dutyText || r.artifactName || r.detail || '—')}</td>
</tr>`,
    )
    .join('')

  const assignForm = data.viewerCanAssign
    ? `<section class="card">
  <h2>下达任务</h2>
  <form class="task-form" method="post" action="/tasks/assign">
    <input type="hidden" name="token" value="${escapeHtml(data.viewerToken ?? '')}">
    <input type="hidden" name="periodType" value="${escapeHtml(data.periodType)}">
    <input type="hidden" name="periodKey" value="${escapeHtml(data.periodKey)}">
    <input type="hidden" name="periodLabel" value="${escapeHtml(data.periodLabel)}">
    <label class="field"><span>任务</span><input type="text" name="title" placeholder="要下属做什么" required></label>
    <label class="field"><span>说明</span><input type="text" name="detail" placeholder="补充要求（可选）"></label>
    <div class="form-row">
      <label class="field"><span>交给哪个岗位</span><select name="positionId" required>
        ${data.assignablePositions
          .map(
            (p) =>
              `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}（${p.people.length} 人）</option>`,
          )
          .join('')}
      </select></label>
      <label class="field"><span>周期</span><select name="periodType">
        <option value="week"${data.periodType === 'week' ? ' selected' : ''}>周</option>
        <option value="month"${data.periodType === 'month' ? ' selected' : ''}>月</option>
      </select></label>
    </div>
    <div class="form-actions"><button class="btn btn-primary" type="submit">下达</button></div>
  </form>
</section>`
    : ''

  const recurringForm = data.viewerCanAssign
    ? `<section class="card">
  <h2>设一个固定任务</h2>
  <p class="muted small">固定任务每周或每月自动出现在这个岗位每个人的清单里。</p>
  <form class="task-form" method="post" action="/tasks/recurring">
    <input type="hidden" name="token" value="${escapeHtml(data.viewerToken ?? '')}">
    <label class="field"><span>任务</span><input type="text" name="title" placeholder="每周期都要做的事" required></label>
    <label class="field"><span>说明</span><input type="text" name="detail" placeholder="补充要求（可选）"></label>
    <div class="form-row">
      <label class="field"><span>交给哪个岗位</span><select name="positionId" required>
        ${data.assignablePositions
          .map(
            (p) =>
              `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}（${p.people.length} 人）</option>`,
          )
          .join('')}
      </select></label>
      <label class="field"><span>频率</span><select name="cadence">
        <option value="week">每周</option>
        <option value="month">每月</option>
      </select></label>
    </div>
    <div class="form-actions"><button class="btn btn-primary" type="submit">设为固定任务</button></div>
  </form>
</section>`
    : ''

  const total = data.tasks.length
  const done = data.tasks.filter((t) => t.status === 'done').length
  const blocked = data.tasks.filter((t) => t.status === 'blocked').length
  const assigned = data.tasks.filter((t) => t.kind === 'assigned').length
  const recurringCount = data.tasks.filter((t) => t.kind === 'recurring').length

  const body = `
${pageHead(
  `${data.departmentName} · 任务清单`,
  `${data.periodLabel}${data.position ? ` · ${data.position.name}` : ' · 全部岗位'}`,
  data.viewerPositionName ? `<span class="muted">当前岗位：${escapeHtml(data.viewerPositionName)}</span>` : '',
)}
${data.error ? `<div class="alert is-error">${escapeHtml(data.error)}</div>` : ''}
<div class="chips">${periodOptions}</div>
<div class="chips">${positionChips}</div>
<div class="stat-row">
  ${stat('任务总数', String(total), '本周期')}
  ${stat('已完成', String(done), total === 0 ? '—' : `${Math.round((done / total) * 100)}%`)}
  ${stat('上级下达', String(assigned), '临时交办')}
  ${stat('固定任务', String(recurringCount), blocked > 0 ? `另有 ${blocked} 项受阻` : '按周期自动生成')}
</div>

${
  data.position && data.recurring.length > 0
    ? `<section class="card">
  <h2>${escapeHtml(data.position.name)}的固定任务</h2>
  <div class="table-wrap"><table class="table">
  <thead><tr><th>任务</th><th>频率</th><th>说明</th></tr></thead>
  <tbody>${recurringRows}</tbody>
</table></div>
</section>`
    : ''
}

${assignForm}
${recurringForm}

${
  postBlocks
    ? postBlocks
    : `<section class="card">${empty('这个周期还没有任务。')}</section>`
}
`
  return layout({ title: '任务清单', active: 'tasks', body })
}

/**
 * The page a person uses to work through their own tasks.
 */
export function renderMyTasks(data: {
  positionName: string
  periodType: PeriodType
  periodKey: string
  periodLabel: string
  periods: { key: string; label: string }[]
  tasks: TaskView[]
  /** The posts this person may give tasks to. */
  assignablePositions?: TaskPosition[]
  /** The photos of each task, by task id. */
  photos?: Record<string, TaskPhoto[]>
  /** The token of the person looking at the list. */
  viewerToken?: string
  error?: string
}): string {
  const periodOptions = join(
    data.periods.map(
      (p) =>
        `<a class="chip${p.key === data.periodKey ? ' is-active' : ''}" href="/my-tasks?type=${data.periodType}&period=${encodeURIComponent(p.key)}">${escapeHtml(p.label)}</a>`,
    ),
  )

  const photoBlock = (taskId: string): string => {
    const photos = data.photos?.[taskId] ?? []
    if (photos.length === 0) return ''
    return `<div class="photo-row">${photos
      .map((p) => `<a href="/photos/${p.id}" target="_blank"><img class="thumb" src="/photos/${p.id}" alt="照片"></a>`)
      .join('')}</div>`
  }

  const rows = data.tasks
    .map(
      (t) => `<tr>
  <td>${escapeHtml(t.title)}</td>
  <td><span class="tag tag-kind-${t.kind}">${escapeHtml(TASK_KIND_LABELS[t.kind])}</span></td>
  <td class="note-cell">${escapeHtml(t.detail || t.dutyText || t.artifactName || '—')}</td>
  <td class="wide">
    <form class="status-form" method="post" action="/my-tasks/status">
      <input type="hidden" name="token" value="${escapeHtml(t.id)}">
      <select name="status">
        ${STATUS_ORDER.map(
          (s) => `<option value="${s}"${t.status === s ? ' selected' : ''}>${escapeHtml(TASK_STATUS_LABELS[s])}</option>`,
        ).join('')}
      </select>
      <input type="text" name="note" value="${escapeHtml(t.note)}" placeholder="说明">
      <button class="btn" type="submit">保存</button>
    </form>
    ${photoBlock(t.id)}
    <form class="photo-form" method="post" action="/my-tasks/photos" enctype="multipart/form-data">
      <input type="hidden" name="taskId" value="${escapeHtml(t.id)}">
      <input type="hidden" name="token" value="${escapeHtml(data.viewerToken ?? '')}">
      <input type="hidden" name="periodType" value="${escapeHtml(data.periodType)}">
      <input type="hidden" name="periodKey" value="${escapeHtml(data.periodKey)}">
      <input type="file" name="photos" accept="image/*" multiple>
      <button class="btn" type="submit">上传照片</button>
    </form>
  </td>
</tr>`,
    )
    .join('')

  const done = data.tasks.filter((t) => t.status === 'done').length

  const assignForm =
    data.assignablePositions && data.assignablePositions.length > 0
      ? `<section class="card">
  <h2>给下属下达任务</h2>
  <form class="task-form" method="post" action="/tasks/assign">
    <input type="hidden" name="token" value="${escapeHtml(data.viewerToken ?? '')}">
    <input type="hidden" name="periodType" value="${escapeHtml(data.periodType)}">
    <input type="hidden" name="periodKey" value="${escapeHtml(data.periodKey)}">
    <label class="field"><span>任务</span><input type="text" name="title" placeholder="要下属做什么" required></label>
    <label class="field"><span>说明</span><input type="text" name="detail" placeholder="补充要求（可选）"></label>
    <label class="field"><span>交给哪个岗位</span><select name="positionId" required>
      ${data.assignablePositions
        .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}（${p.people.length} 人）</option>`)
        .join('')}
    </select></label>
    <div class="form-actions"><button class="btn btn-primary" type="submit">下达</button></div>
  </form>
</section>`
      : ''

  const fixedForm =
    data.assignablePositions && data.assignablePositions.length > 0
      ? `<section class="card">
  <h2>给下属设固定任务</h2>
  <p class="muted small">固定任务每周或每月自动出现在这个岗位每个人的清单里。</p>
  <form class="task-form" method="post" action="/tasks/recurring">
    <input type="hidden" name="token" value="${escapeHtml(data.viewerToken ?? '')}">
    <label class="field"><span>任务</span><input type="text" name="title" placeholder="每周期都要做的事" required></label>
    <label class="field"><span>说明</span><input type="text" name="detail" placeholder="补充要求（可选）"></label>
    <div class="form-row">
      <label class="field"><span>交给哪个岗位</span><select name="positionId" required>
        ${data.assignablePositions
          .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}（${p.people.length} 人）</option>`)
          .join('')}
      </select></label>
      <label class="field"><span>频率</span><select name="cadence">
        <option value="week">每周</option>
        <option value="month">每月</option>
      </select></label>
    </div>
    <div class="form-actions"><button class="btn btn-primary" type="submit">设为固定任务</button></div>
  </form>
</section>`
      : ''

  const body = `
${pageHead(
  `${data.positionName} · 我的任务`,
  `${data.periodLabel}`,
)}
${data.error ? `<div class="alert is-error">${escapeHtml(data.error)}</div>` : ''}
<div class="chips">${periodOptions}</div>
<div class="stat-row">
  ${stat('任务', String(data.tasks.length), '本周期')}
  ${stat('已完成', String(done), data.tasks.length === 0 ? '—' : `${Math.round((done / data.tasks.length) * 100)}%`)}
  ${stat('待办', String(data.tasks.filter((t) => t.status === 'open').length), '还没开始')}
  ${stat('受阻', String(data.tasks.filter((t) => t.status === 'blocked').length), '需要支持')}
</div>
<section class="card">
  <h2>任务清单</h2>
  ${
    rows
      ? `<div class="table-wrap"><table class="table">
  <thead><tr><th>任务</th><th>来源</th><th>说明</th><th>状态</th></tr></thead>
  <tbody>${rows}</tbody>
</table></div>`
      : empty('这个周期还没有任务。')
  }
</section>
${assignForm}
${fixedForm}
`
  return layout({ title: '我的任务', active: 'tasks', body })
}

/**
 * The posts a person may give tasks to: the posts directly under their own.
 *
 * The chain is 副总经理 → 经理/副经理 → 其管辖的专责 → 其业务员. Each step
 * reaches only the posts directly below it, so a manager talks to their
 * specialists and a specialist talks to their own staff, not further.
 */
export function assignablePosts(
  tree: TaskPositionNode[],
  viewerPositionId: string,
): TaskPosition[] {
  const toTaskPosition = (node: TaskPositionNode): TaskPosition => ({
    id: node.id,
    name: node.name,
    level: node.level,
    headcount: node.headcount,
    people: node.people,
  })
  const find = (nodes: TaskPositionNode[]): TaskPositionNode | undefined => {
    for (const node of nodes) {
      if (node.id === viewerPositionId) return node
      const found = find(node.children)
      if (found) return found
    }
    return undefined
  }
  const viewer = find(tree)
  return viewer ? viewer.children.map(toTaskPosition) : []
}
