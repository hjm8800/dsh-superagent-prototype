/**
 * Regenerate the normalized seed file from the market-department duties table.
 *
 *   node --experimental-strip-types scripts/import-xlsx.ts <input.xlsx> [output.json]
 *
 * The workbook is laid out as one row per post:
 *
 *   column B  the manager or the deputy manager, with their core duties
 *   column C  the same, as a block of labelled lines
 *   column D  the specialist (专责) heading
 *   column E  the specialist's duties
 *   column F  the clerk (业务员) or team (班组) heading under that specialist
 *   column G  that clerk's or team's duties
 *   column H  the boundary between the specialist and the clerk
 *   column I  a group under the specialist, with its people
 *   column J  extra notes and the roles inside that group
 *
 * Rows below the table proper repeat the same people as a staffing tracker.
 * They are read for their notes but do not create posts of their own.
 *
 * Because the table is merged, a cell only holds a value in the row where the
 * merge starts, so the rows are read in order and each heading opens a post.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { inflateRawSync } from 'node:zlib'

// ---------------------------------------------------------------- zip reader

/** Read the entries of a zip file into a map of name to bytes. */
function readZip(path: string): Map<string, Buffer> {
  const buffer = readFileSync(path)
  const entries = new Map<string, Buffer>()
  let eocd = -1
  for (let i = buffer.length - 22; i >= 0; i--) {
    if (buffer.readUInt32LE(i) === 0x06054b50) {
      eocd = i
      break
    }
  }
  if (eocd < 0) throw new Error(`${path} is not a zip file`)
  const count = buffer.readUInt16LE(eocd + 10)
  let pointer = buffer.readUInt32LE(eocd + 16)
  for (let i = 0; i < count; i++) {
    const method = buffer.readUInt16LE(pointer + 10)
    const compressedSize = buffer.readUInt32LE(pointer + 20)
    const nameLength = buffer.readUInt16LE(pointer + 28)
    const extraLength = buffer.readUInt16LE(pointer + 30)
    const commentLength = buffer.readUInt16LE(pointer + 32)
    const localOffset = buffer.readUInt32LE(pointer + 42)
    const name = buffer.subarray(pointer + 46, pointer + 46 + nameLength).toString('utf8')
    const localNameLength = buffer.readUInt16LE(localOffset + 26)
    const localExtraLength = buffer.readUInt16LE(localOffset + 28)
    const dataStart = localOffset + 30 + localNameLength + localExtraLength
    const data = buffer.subarray(dataStart, dataStart + compressedSize)
    // Zip method 8 is deflate, stored without a zlib header.
    entries.set(name, method === 0 ? data : inflateRawSync(data))
    pointer += 46 + nameLength + extraLength + commentLength
  }
  return entries
}

function decodeXml(text: string): string {
  return text
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&amp;/g, '&')
}

/** Read the first worksheet into a map of row number to column letter to text. */
function readSheet(path: string): Map<number, Map<string, string>> {
  const entries = readZip(path)
  const shared: string[] = []
  const sharedXml = entries.get('xl/sharedStrings.xml')?.toString('utf8')
  if (sharedXml) {
    for (const si of sharedXml.match(/<si>([\s\S]*?)<\/si>/g) ?? []) {
      const texts = [...si.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((m) => m[1] ?? '')
      shared.push(decodeXml(texts.join('')))
    }
  }
  const sheetName = [...entries.keys()].find((n) => /^xl\/worksheets\/sheet1\.xml$/.test(n))
  if (!sheetName) throw new Error(`${path} has no first worksheet`)

  const rows = new Map<number, Map<string, string>>()
  const sheetXml = entries.get(sheetName)!.toString('utf8')
  for (const rowMatch of sheetXml.matchAll(/<row[^>]*r="(\d+)"[^>]*>([\s\S]*?)<\/row>/g)) {
    const rowNumber = Number(rowMatch[1])
    const cells = new Map<string, string>()
    for (const cellMatch of rowMatch[2]!.matchAll(/<c\s([^>]*?)(\/>|>([\s\S]*?)<\/c>)/g)) {
      const attributes = cellMatch[1] ?? ''
      const body = cellMatch[3] ?? ''
      const column = /r="([A-Z]+)\d+"/.exec(attributes)?.[1]
      if (!column) continue
      const type = /t="([^"]+)"/.exec(attributes)?.[1]
      let value: string | undefined
      if (type === 's') {
        const index = /<v>(\d+)<\/v>/.exec(body)?.[1]
        value = index === undefined ? undefined : shared[Number(index)]
      } else if (type === 'inlineStr') {
        value = decodeXml([...body.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((m) => m[1] ?? '').join(''))
      } else {
        value = /<v>([\s\S]*?)<\/v>/.exec(body)?.[1]
      }
      if (value !== undefined && value.trim() !== '') cells.set(column, value.trim())
    }
    if (cells.size > 0) rows.set(rowNumber, cells)
  }
  return rows
}

// ------------------------------------------------------------------ parsing

const NUM = /^(\d+)[.、．]\s*(.+)$/
const CIRCLED = /^[①②③④⑤⑥⑦⑧⑨⑩]\s*(.+)$/
const BULLET = /^[-•*]\s*(.+)$/

/**
 * Split a duty blob into individual responsibility items.
 *
 * The table labels some lines as headings rather than duties - `工作指引：…`
 * and `核心职责一：…` - and those belong with the post, not in the list of
 * things an employee reports on.
 */
function splitItems(text: string): string[] {
  const lines = text
    .replace(/ /g, ' ')
    .split(/\r\n|\r|\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const items: string[] = []
  let buffer: string | null = null
  for (const line of lines) {
    // `工作指引：…` and the numbered `核心职责一：…` are headings that end the
    // duty before them; a plain `核心职责：（…）` is a scope label and stays.
    const headingAt = /(工作指引|核心职责[一二三四五六七八九十]?)[：:]/.exec(line)
    if (headingAt && !/^核心职责[：:]/.test(line)) {
      if (buffer) items.push(buffer)
      buffer = null
      const before = line.slice(0, headingAt.index).trim()
      if (before && /^[0-9]/.test(before)) items.push(before)
      continue
    }
    if (/^工作指引[：:]/.test(line)) {
      if (buffer) items.push(buffer)
      buffer = null
      continue
    }
    const match = NUM.exec(line) ?? CIRCLED.exec(line) ?? BULLET.exec(line)
    if (match) {
      if (buffer) items.push(buffer)
      buffer = match[match.length - 1]!.trim()
    } else {
      buffer = buffer ? `${buffer} ${line}` : line
    }
  }
  if (buffer) items.push(buffer)
  // The table is inconsistent about the closing full stop; drop it so the
  // same duty reads the same everywhere.
  return items.map((item) => item.replace(/[。.；;]$/, ''))
}

/**
 * Pull the people names out of a cell.
 *
 * The table writes the people either inside the brackets, as in
 * `合同管理业务员（黄美琳）`, or after them, as in
 * `项目部材料员（3人）蓝婉嫦，黎玉廷，陈印权`.
 */
function peopleOf(text: string): string[] {
  const head = (text.split('\n')[0] ?? '').trim()
  const inside = /（([^）]*)）/.exec(head)?.[1] ?? ''
  const afterBracket = head.split(/[）)]/).slice(1).join('）').trim()
  const namesIn = (source: string): string[] =>
    source
      .split(/[,，、\s]+/)
      .map((part) => part.trim())
      .filter((part) => /^[\u4e00-\u9fa5]{2,4}$/.test(part) && !/留空|待定/.test(part))
  // The brackets hold either the headcount or the names; take whichever
  // actually names someone.
  const inBrackets = namesIn(inside)
  if (inBrackets.length > 0) return inBrackets
  const after = namesIn(afterBracket)
  return after.length > 0 ? after : namesIn(head)
}

/**
 * Split a heading into the post name, its headcount note and the people
 * named in it.
 *
 * The table writes the headcount as `（5人）` and the people as `（黄美琳）`,
 * and sometimes both, as in `物资管理业务员（2人、新、男）`.
 */
function parseHeading(text: string): { name: string; headcount: string; members: string[] } {
  const head = (text.split('\n')[0] ?? '').trim()
  const inside = /（([^）]*)）/.exec(head)?.[1] ?? ''
  const headcountMatch = /(\d+)\s*人/.exec(inside)
  const headcount = headcountMatch ? `${headcountMatch[1]}人` : ''
  const name = head.replace(/（[^）]*）/, '').trim()
  const members = inside
    .split(/[、,，]/)
    .map((part) => part.trim())
    .filter((part) => /^[\u4e00-\u9fa5]{2,4}$/.test(part) && !/人$/.test(part))
  return { name, headcount, members }
}

interface SeedPosition {
  name: string
  role: string
  level: string
  parent: string | null
  headcount_note: string
  instructions: string
  boundary: string
  note: string
  duties: string[]
  members: string[]
}

function position(partial: Partial<SeedPosition> & { name: string; role: string; level: string }): SeedPosition {
  return {
    parent: null,
    headcount_note: '',
    instructions: '',
    boundary: '',
    note: '',
    duties: [],
    members: [],
    ...partial,
  }
}

/**
 * Where each post sits in the table.
 *
 * The table merges cells, so several posts have no cell of their own and the
 * generic scan cannot find them. This map names them explicitly. When the
 * source table changes, adjust the map rather than the parser.
 */
interface RowMapEntry {
  /** Row in the sheet. */
  row: number
  /** Which post this row holds. */
  name: string
  role: string
  level: string
  /** Post this one reports to. */
  parent?: string
  /** Columns to read the heading, duties, boundary and members from. */
  heading?: string
  duties?: string
  boundary?: string
  members?: string
  /** Extra note from another column. */
  note?: string
  /** People the source names outside the heading cell. */
  extraMembers?: string[]
  /** A note about the post, such as why it has nobody yet. */
  postNote?: string
}

const ROW_MAP: RowMapEntry[] = [
  { row: 2, name: '经理', role: '经理', level: 'manager', heading: 'B', duties: 'C' },
  { row: 6, name: '副经理', role: '副经理', level: 'deputy_manager', heading: 'B', duties: 'C' },
  {
    row: 2,
    name: '市场营销和合同管理专责',
    role: '专责',
    level: 'specialist',
    parent: '经理',
    heading: 'D',
    duties: 'E',
  },
  {
    row: 4,
    name: '技经管理专责',
    role: '专责',
    level: 'specialist',
    parent: '经理',
    heading: 'D',
    duties: 'E',
  },
  {
    row: 6,
    name: '招标与采购专责',
    role: '专责',
    level: 'specialist',
    parent: '副经理',
    heading: 'D',
    duties: 'E',
  },
  {
    row: 8,
    name: '物资与仓储专责',
    role: '专责',
    level: 'specialist',
    parent: '副经理',
    heading: 'D',
    duties: 'E',
    extraMembers: ['冯文静'],
  },
  {
    row: 2,
    name: '合同管理业务员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '市场营销和合同管理专责',
    heading: 'F',
    duties: 'G',
    boundary: 'H',
  },
  {
    row: 4,
    name: '技经业务员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '技经管理专责',
    heading: 'F',
    duties: 'G',
    boundary: 'H',
    postNote: '岗位待定人（表中记为「莫楠or新人」）',
  },
  {
    row: 5,
    name: '结算班',
    role: '班组',
    level: 'team',
    parent: '技经管理专责',
    heading: 'F',
    duties: 'G',
    postNote: '下放至各项目部，人员待明确',
  },
  {
    row: 6,
    name: '招投标管理业务员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '招标与采购专责',
    heading: 'F',
    duties: 'G',
    boundary: 'H',
    note: 'J',
  },
  {
    row: 7,
    name: '采购管理业务员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '招标与采购专责',
    heading: 'F',
    duties: 'G',
    note: 'J',
    extraMembers: ['李建眉'],
  },
  {
    row: 8,
    name: '物资管理业务员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '物资与仓储专责',
    heading: 'F',
    duties: 'G',
    boundary: 'H',
    postNote: '岗位待定人（表中记为「新、男」）',
  },
  {
    row: 9,
    name: '项目部材料员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '物资与仓储专责',
    heading: 'F',
    duties: 'G',
  },
  {
    row: 2,
    name: '光伏+用户标团',
    role: '业务员',
    level: 'specialist_staff',
    parent: '市场营销和合同管理专责',
    heading: 'I',
    members: 'I',
  },
  {
    row: 2,
    name: '市场拓展业务员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '光伏+用户标团',
    heading: 'J',
    duties: 'J',
  },
  {
    row: 2,
    name: '客户服务业务员',
    role: '业务员',
    level: 'specialist_staff',
    parent: '光伏+用户标团',
    heading: 'J',
    duties: 'J',
  },
  {
    row: 8,
    name: '仓储班',
    role: '班组',
    level: 'team',
    parent: '物资与仓储专责',
    heading: 'I',
    duties: 'J',
    members: 'I',
  },
]

/**
 * Take the part of a cell that belongs to one role.
 *
 * The 光伏 group's cell lists two roles, each opening with a circled marker,
 * so the block runs from that marker to the next one.
 */
function roleBlock(text: string, roleName: string): string {
  const lines = text.split(/\r\n|\r|\n/)
  const marker = new RegExp(`^[①②③④⑤⑥⑦⑧⑨⑩]\\s*${roleName}`)
  const start = lines.findIndex((line) => marker.test(line.trim()))
  if (start < 0) return text
  const rest = lines.slice(start + 1)
  const end = rest.findIndex((line) => /^[①②③④⑤⑥⑦⑧⑨⑩]/.test(line.trim()))
  return (end < 0 ? rest : rest.slice(0, end)).join('\n')
}

/**
 * Read the manager's and the deputy manager's core duties.
 *
 * Their cell labels the duties as `核心职责：…` and separates them with the
 * Chinese comma.
 */
function managerDuties(text: string): string[] {
  const line = text.split(/\r\n|\r|\n/).find((l) => l.includes('核心职责：'))
  if (!line) return []
  const value = line.slice(line.indexOf('核心职责：') + '核心职责：'.length)
  return value
    .split(/[、,，]/)
    .map((part) => part.trim())
    .filter(Boolean)
}

/** The work instruction a manager or specialist row carries. */
function instructionOf(text: string): string {
  const line = text.split(/\r\n|\r|\n/).find((l) => l.includes('工作指引：'))
  return line ? line.slice(line.indexOf('工作指引：') + '工作指引：'.length).trim() : ''
}

/**
 * The people a specialist's heading names.
 *
 * The specialist's name and the person in it sit on two lines, as in
 * `市场营销和合同管理专责` and `（黎嘉良）`. The table writes `（留空）` when a
 * post has nobody yet.
 */
function specialistMembers(text: string): string[] {
  const lines = text.split(/\r\n|\r|\n/).map((line) => line.trim())
  for (const line of lines.slice(1)) {
    const inside = /（([^）]*)）/.exec(line)?.[1]
    if (!inside) continue
    const names = inside
      .split(/[、,，]/)
      .map((name) => name.trim())
      .filter((name) => /^[\u4e00-\u9fa5]{2,4}$/.test(name) && !/留空|待定|or/.test(name))
    if (names.length > 0) return names
  }
  return []
}

/**
 * The people a group's heading names.
 *
 * A group writes the headcount and the people together, as in
 * `光伏+用户标团（刘正伦、胡可富…9人）`.
 */
function groupMembers(text: string): string[] {
  const inside = /（([^）]*)）/.exec(text)?.[1] ?? ''
  return inside
    .split(/[、,，]/)
    .map((name) => name.trim())
    .filter((name) => /^[\u4e00-\u9fa5]{2,4}$/.test(name))
}

function buildSeed(path: string): { departments: unknown[] } {
  const rows = readSheet(path)
  const cell = (row: number, column: string): string => rows.get(row)?.get(column) ?? ''

  const positions: SeedPosition[] = []
  // The two roles inside the 光伏 group are staffed from the group roster,
  // so collect the roster before assigning people.
  const groupRoster = groupMembers(cell(2, 'I'))
  // The table's lower block repeats the people as a staffing tracker; their
  // notes say which post each one fills.
  const trackerNotes: { name: string; note: string }[] = [
    { name: '刘晶', note: '技经辅助1人；投标+分包管理+挂账流程+综合事务' },
    { name: '张寅', note: '招投标辅助1人' },
    { name: '冯文静', note: '物资仓储专责1人' },
    { name: '张自力', note: '物资业务1人（如不配专责则2人）' },
    { name: '陈师明', note: '仓储班收发料3人' },
    { name: '莫桂凤', note: '仓储班' },
    { name: '蓝婉嫦', note: '项目部材料员' },
  ]
  /** The people the tracker block places in a post, by post name. */
  const trackerMembers = (postName: string): string[] => {
    const wanted: Record<string, string[]> = {
      招投标管理业务员: ['刘晶', '张寅'],
      物资与仓储专责: ['冯文静'],
    }
    return wanted[postName] ?? []
  }
  let rosterTaken = 0

  for (const entry of ROW_MAP) {
    const headingText = entry.heading ? cell(entry.row, entry.heading) : ''
    const heading = parseHeading(headingText)
    const name = entry.name

    // Where the people of this post come from, in the order the table gives
    // them: the side note, the group roster, the tracker block, the heading.
    const found: string[] = []
    const add = (names: string[]): void => {
      for (const person of names) if (!found.includes(person)) found.push(person)
    }
    if (entry.members === 'I') add(groupMembers(cell(entry.row, 'I')))
    else if (entry.members) add(peopleOf(cell(entry.row, entry.members)))
    if (entry.level === 'specialist') add(specialistMembers(headingText))
    if (entry.name === '市场拓展业务员') add(groupRoster.slice(0, 1))
    if (entry.name === '客户服务业务员') add(groupRoster.slice(1, 3))
    if (entry.name === '光伏+用户标团') add(groupRoster.slice(3))
    add(trackerMembers(entry.name))
    if (entry.name === '仓储班') add(peopleOf(cell(entry.row, 'I')))
    // The heading names the people of the post; the side notes add to them.
    add(peopleOf(headingText))
    add(entry.extraMembers ?? [])
    // The manager and the deputy manager posts hold no one in the table.
    if (entry.level === 'manager' || entry.level === 'deputy_manager') found.length = 0

    // A post's people are the ones the table gives for that post alone; a
    // group hands its first members to its two named roles.
    const members =
      entry.name === '光伏+用户标团'
        ? groupRoster.slice(3)
        : entry.name === '市场拓展业务员'
          ? groupRoster.slice(0, 1)
          : entry.name === '客户服务业务员'
            ? groupRoster.slice(1, 3)
            : found

    // The 光伏 group's two roles share one cell, each introduced by a circled
    // marker; take the block that belongs to this post.
    let duties = entry.duties ? splitItems(cell(entry.row, entry.duties)) : []
    if (entry.name === '市场拓展业务员' || entry.name === '客户服务业务员') {
      duties = splitItems(roleBlock(cell(entry.row, entry.duties!), entry.name))
    }
    // The manager and the deputy manager list their duties as one labelled
    // line rather than as a numbered list.
    if (entry.level === 'manager' || entry.level === 'deputy_manager') {
      duties = managerDuties(cell(entry.row, 'C'))
    }

    positions.push(
      position({
        name,
        role: entry.role,
        level: entry.level,
        parent: entry.parent ?? null,
        headcount_note: heading.headcount,
        instructions:
          entry.level === 'manager' || entry.level === 'deputy_manager'
            ? instructionOf(cell(entry.row, 'C'))
            : entry.level === 'specialist'
              ? instructionOf(cell(entry.row, entry.duties ?? 'E'))
              : '',
        boundary: entry.boundary ? cell(entry.row, entry.boundary) : '',
        note: [entry.postNote ?? '', entry.note ? cell(entry.row, entry.note) : '']
          .filter(Boolean)
          .join('；'),
        duties,
        members,
      }),
    )
  }

  return {
    departments: [
      {
        code: 'MKT',
        name: '市场部',
        name_en: 'Marketing Department',
        positions,
        pending: [],
      },
    ],
  }
}

const input = process.argv[2]
const output = process.argv[3]
if (!input) {
  console.error('usage: import-xlsx.ts <input.xlsx> [output.json]')
  process.exit(1)
}
const seed = buildSeed(input)
const target = output ?? 'seed/market-department.json'
writeFileSync(target, JSON.stringify(seed, null, 2), 'utf8')
const positions = (seed as { departments: { positions: SeedPosition[] }[] }).departments[0]!.positions
console.log(`imported ${positions.length} positions into ${target}`)
for (const p of positions) {
  console.log(
    `  ${p.level.padEnd(18)} ${p.name.padEnd(26)} duties=${String(p.duties.length).padStart(2)} ` +
      `members=${p.members.join(',')}`,
  )
}
