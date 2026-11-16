import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readdir, stat } from 'node:fs/promises'
import type { Dirent } from 'node:fs'
import { join, extname } from 'node:path'
import { promisify } from 'node:util'
import type { ArtifactGroup, CollectedFile, PeriodType } from './types.ts'

/**
 * Where the employees leave their outcome files, and how the collector reads
 * them.
 *
 * Two sources are supported: a Feishu drive folder, which is where this team
 * already works, and a local directory, for a share mounted on the machine
 * that runs the app.
 */

const run = promisify(execFile)

/**
 * Where the Feishu CLI lives.
 *
 * On Windows the command is a shim that only the shell can start, and the
 * executable itself sits beside it; prefer the executable so the collector
 * needs no shell.
 */
function defaultFeishuCli(): string {
  const candidates = [
    process.env['LARK_CLI'],
    'C:\\Users\\38307\\.dsh\\tools\\lark-cli-bin\\lark-cli.exe',
    process.env['USERPROFILE'] + '\\.dsh\\tools\\lark-cli-bin\\lark-cli.exe',
  ]
  for (const candidate of candidates) {
    if (candidate && existsSync(candidate)) return candidate
  }
  return 'lark-cli'
}

export interface FileSource {
  /** A short name for the source, shown in the collection log. */
  name: string
  /** List the files in the shared place. */
  list(): Promise<CollectedFile[]>
}

/** A folder on the machine running the app, such as a mounted network share. */
export class DirectorySource implements FileSource {
  readonly name: string
  private readonly directory: string
  /** How deep to look, so a share's subfolders are found too. */
  private readonly depth: number
  /**
   * Where the uploader's name comes from when the share itself does not
   * carry it. A share usually does not, so the name is taken from the file
   * name; a share that keeps a list of who owns what can say so here.
   */
  private readonly owners?: (fileName: string) => string

  constructor(
    name: string,
    directory: string,
    options: { depth?: number; owners?: (fileName: string) => string } = {},
  ) {
    this.name = name
    this.directory = directory
    this.depth = options.depth ?? 3
    this.owners = options.owners
  }

  async list(): Promise<CollectedFile[]> {
    const found: CollectedFile[] = []
    await this.walk(this.directory, 0, found)
    return found
  }

  private async walk(directory: string, level: number, found: CollectedFile[]): Promise<void> {
    if (level > this.depth) return
    let entries: Dirent[]
    try {
      entries = await readdir(directory, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const path = join(directory, entry.name)
      if (entry.isDirectory()) {
        await this.walk(path, level + 1, found)
        continue
      }
      if (!entry.isFile() || entry.name.startsWith('.')) continue
      const info = await stat(path).catch(() => undefined)
      if (!info) continue
      found.push({
        sourceId: path,
        name: entry.name,
        ownerName: this.owners?.(entry.name) ?? '',
        modifiedAt: info.mtime.toISOString(),
        location: path,
      })
    }
  }
}

/** A folder in Feishu drive, read through the Feishu CLI. */
export class FeishuFolderSource implements FileSource {
  readonly name: string
  private readonly folderToken: string
  private readonly cli: string

  constructor(name: string, folderToken: string, cli = defaultFeishuCli()) {
    this.name = name
    this.folderToken = folderToken
    this.cli = cli
  }

  async list(): Promise<CollectedFile[]> {
    const files: CollectedFile[] = []
    let pageToken = ''
    for (let page = 0; page < 20; page++) {
      const args = [
        'drive',
        'files',
        'list',
        '--as',
        'bot',
        '--folder-token',
        this.folderToken,
        '--page-size',
        '200',
      ]
      if (pageToken) args.push('--page-token', pageToken)
      const { stdout } = await run(this.cli, args, { maxBuffer: 32 * 1024 * 1024 })
      const parsed = parseCliJson(stdout)
      const items = (parsed?.data?.files ?? parsed?.data?.items ?? []) as DriveFile[]
      for (const item of items) {
        if (item.type !== 'file') continue
        if (item.shortcut_info?.target_token) continue
        files.push({
          sourceId: item.token,
          name: item.name,
          ownerName: item.owner_id ?? '',
          modifiedAt: isoFromMillis(item.modified_time),
          location: `feishu:${this.folderToken}`,
        })
      }
      pageToken = parsed?.data?.page_token ?? ''
      if (!pageToken) break
    }
    return files
  }
}

interface DriveFile {
  token: string
  name: string
  type?: string
  owner_id?: string
  modified_time?: string | number
  shortcut_info?: { target_token?: string }
}

/** The CLI prints its answer with a banner on the first line; take the JSON. */
function parseCliJson(stdout: string): any {
  const start = stdout.indexOf('{')
  if (start < 0) return undefined
  try {
    return JSON.parse(stdout.slice(start))
  } catch {
    return undefined
  }
}

function isoFromMillis(value: string | number | undefined): string {
  if (value === undefined) return ''
  const millis = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(millis)) return ''
  return new Date(millis).toISOString()
}

/** Extensions the collector recognizes as outcome files. */
const RECOGNIZED = new Set([
  '.xlsx', '.xls', '.csv', '.docx', '.doc', '.pdf', '.pptx', '.txt', '.md',
])

export function isOutcomeFile(name: string): boolean {
  return RECOGNIZED.has(extname(name).toLowerCase())
}

/**
 * Recognize a file as the output of a duty.
 *
 * A file name that contains one of the group's keywords is that group's
 * output. The longest keyword wins, so a name that holds several is credited
 * to the most specific one.
 *
 * When the person who filed it is known, the groups of their own post come
 * first: each post produces its own outputs, and the same word can appear in
 * a colleague's file name too.
 */
export function matchArtifact(
  fileName: string,
  artifacts: ArtifactGroup[],
  positionId?: string,
): { artifact: ArtifactGroup; keyword: string } | undefined {
  const name = fileName.toLowerCase()
  const own = positionId ? artifacts.filter((a) => a.positionId === positionId) : []
  const others = positionId ? artifacts.filter((a) => a.positionId !== positionId) : []
  for (const candidates of positionId ? [own, others] : [artifacts]) {
    let best: { artifact: ArtifactGroup; keyword: string } | undefined
    for (const artifact of candidates) {
      for (const keyword of artifact.keywords) {
        if (!name.includes(keyword.toLowerCase())) continue
        if (!best || keyword.length > best.keyword.length) best = { artifact, keyword }
      }
    }
    if (best) return best
  }
  return undefined
}

/**
 * Find the person a file belongs to.
 *
 * The uploader's name is the strongest signal. A name in the file itself is
 * the fallback for a share where the uploader is not one of the team.
 */
export function matchPerson(
  fileName: string,
  ownerName: string,
  people: { id: string; name: string; positionId?: string }[],
): { id: string; positionId?: string } | undefined {
  const owner = ownerName.trim()
  if (owner) {
    const byOwner = people.find((p) => p.name === owner)
    if (byOwner) return { id: byOwner.id, positionId: byOwner.positionId }
  }
  const name = fileName.toLowerCase()
  for (const person of people) {
    if (person.name.length >= 2 && name.includes(person.name.toLowerCase())) {
      return { id: person.id, positionId: person.positionId }
    }
  }
  return undefined
}

/** The period a file belongs to, taken from when it was last changed. */
export function periodOf(modifiedAt: string, type: PeriodType): { key: string; label: string } {
  const date = new Date(modifiedAt)
  if (Number.isNaN(date.getTime())) return { key: '', label: '' }
  if (type === 'month') {
    const key = `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`
    return { key, label: `${date.getUTCFullYear()}年${date.getUTCMonth() + 1}月` }
  }
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()))
  const day = target.getUTCDay() || 7
  target.setUTCDate(target.getUTCDate() + 4 - day)
  const year = target.getUTCFullYear()
  const yearStart = new Date(Date.UTC(year, 0, 1))
  const week = Math.ceil(((target.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
  const key = `${year}-W${String(week).padStart(2, '0')}`
  return { key, label: `${year}年第${week}周` }
}
