/** Small helpers for building HTML by hand. */

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Join a list of html fragments. */
export function join(parts: string[]): string {
  return parts.filter(Boolean).join('')
}

export function attr(name: string, value: string): string {
  return `${name}="${escapeHtml(value)}"`
}

export interface LayoutOptions {
  title: string
  /** The active navigation entry, highlighted in the header. */
  active: string
  body: string
  /** Extra content injected before `</body>`, e.g. page scripts. */
  script?: string
}

const NAV = [
  { key: 'overview', label: '总览', href: '/' },
  { key: 'tasks', label: '任务清单', href: '/tasks' },
  { key: 'collection', label: '成果采集', href: '/collection' },
  { key: 'ledger', label: '岗位职责', href: '/ledger' },
  { key: 'report', label: '我的填报', href: '/report' },
  { key: 'links', label: '岗位账户', href: '/links' },
  { key: 'logout', label: '退出', href: '/logout' },
]

export function layout(options: LayoutOptions): string {
  const nav = join(
    NAV.map((item) =>
      `<a class="nav-link${item.key === options.active ? ' is-active' : ''}" href="${item.href}">${escapeHtml(item.label)}</a>`,
    ),
  )
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(options.title)} · 市场部工作台</title>
<link rel="stylesheet" href="/style.css?v=3">
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="/">
      <span class="brand-mark">市</span>
      <span class="brand-text">
        <strong>市场部工作台</strong>
        <small>岗位职责与周／月完成情况</small>
      </span>
    </a>
    <nav class="nav">${nav}</nav>
  </div>
</header>
<main class="page">
${options.body}
</main>
${options.script ? `<script>${options.script}</script>` : ''}
</body>
</html>`
}

/** A page title block with optional description and actions. */
export function pageHead(title: string, description?: string, actions?: string): string {
  return `<div class="page-head">
  <div>
    <h1>${escapeHtml(title)}</h1>
    ${description ? `<p class="muted">${escapeHtml(description)}</p>` : ''}
  </div>
  ${actions ? `<div class="page-actions">${actions}</div>` : ''}
</div>`
}

/** A simple statistic tile. */
export function stat(label: string, value: string, hint?: string): string {
  return `<div class="stat">
  <span class="stat-value">${escapeHtml(value)}</span>
  <span class="stat-label">${escapeHtml(label)}</span>
  ${hint ? `<span class="stat-hint">${escapeHtml(hint)}</span>` : ''}
</div>`
}

/** A horizontal completion bar. */
export function bar(percent: number): string {
  const clamped = Math.max(0, Math.min(100, percent))
  return `<span class="bar"><span class="bar-fill" style="width:${clamped}%"></span></span>`
}

/** An empty-state block. */
export function empty(message: string): string {
  return `<div class="empty">${escapeHtml(message)}</div>`
}
