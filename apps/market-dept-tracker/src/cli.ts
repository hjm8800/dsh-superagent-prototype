#!/usr/bin/env node
import { existsSync, readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { openDatabase, seedArtifacts, seedDatabase, seedFixedTasks, seedLeaders, slugify } from './db.ts'
import { grantLeaderAccess, leaderGrants, revokeLeaderAccess } from './access.ts'
import { Store } from './store.ts'
import { createTrackerServer } from './server.ts'
import { collect } from './collection.ts'
import { DirectorySource, FeishuFolderSource, type FileSource } from './collector.ts'

/**
 * Command line entry point.
 *
 *   market-dept-tracker [--port 3000] [--host 0.0.0.0] [--data ./data/tracker.db]
 *   market-dept-tracker seed [--data ...]
 *   market-dept-tracker links [--data ...]
 *
 * The database is created and seeded on first run.
 */

const HERE = dirname(fileURLToPath(import.meta.url))

/**
 * Windows consoles default to a legacy code page, which turns the Chinese
 * output into mojibake. Switch the console to UTF-8 before printing anything.
 */
function enableUtf8Console(): void {
  if (process.platform !== 'win32') return
  try {
    execSync('chcp 65001', { stdio: 'ignore', shell: 'cmd.exe' })
  } catch {
    // A console that cannot switch code pages still prints UTF-8 bytes; the
    // information is not lost, only the rendering may be off.
  }
}

enableUtf8Console()

function parseArgs(argv: string[]): { command: string; options: Record<string, string> } {
  const command = argv[0] && !argv[0].startsWith('-') ? argv[0] : 'serve'
  const rest = argv[0] && !argv[0].startsWith('-') ? argv.slice(1) : argv
  const options: Record<string, string> = {}
  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i]!
    if (!arg.startsWith('--')) continue
    const key = arg.slice(2)
    const next = rest[i + 1]
    if (next && !next.startsWith('--')) {
      options[key] = next
      i++
    } else {
      options[key] = 'true'
    }
  }
  return { command, options }
}

/** Locate the seed file, both from the repo and from an installed package. */
function findSeed(): string | undefined {
  const candidates = [
    resolve(HERE, '..', 'seed', 'market-department.json'),
    resolve(HERE, '..', '..', 'seed', 'market-department.json'),
    resolve(process.cwd(), 'seed', 'market-department.json'),
  ]
  return candidates.find((path) => existsSync(path))
}

/** Locate the expected-outputs file, beside the seed file. */
function findArtifacts(): string | undefined {
  const candidates = [
    resolve(HERE, '..', 'seed', 'artifacts.json'),
    resolve(HERE, '..', '..', 'seed', 'artifacts.json'),
    resolve(process.cwd(), 'seed', 'artifacts.json'),
  ]
  return candidates.find((path) => existsSync(path))
}

/** Locate the fixed-task file, beside the seed file. */
function findTaskSeed(): string | undefined {
  const candidates = [
    resolve(HERE, '..', 'seed', 'tasks.json'),
    resolve(HERE, '..', '..', 'seed', 'tasks.json'),
    resolve(process.cwd(), 'seed', 'tasks.json'),
  ]
  return candidates.find((path) => existsSync(path))
}

/** Locate the file naming who may see the whole department. */
function findLeaderSeed(): string | undefined {
  const candidates = [
    resolve(HERE, '..', 'seed', 'leaders.json'),
    resolve(HERE, '..', '..', 'seed', 'leaders.json'),
    resolve(process.cwd(), 'seed', 'leaders.json'),
  ]
  return candidates.find((path) => existsSync(path))
}

/** The shared place the employees leave their outcome files. */
function fileSource(options: Record<string, string>): FileSource | undefined {
  const folder = options['feishu-folder']
  if (folder) return new FeishuFolderSource('飞书云空间', folder)
  const directory = options['collect-dir']
  if (directory) {
    const path = resolve(directory)
    // A share that keeps a list of who owns which file; the name of the
    // file is the fallback.
    const rosterPath = join(path, 'owners.json')
    if (existsSync(rosterPath)) {
      const roster = JSON.parse(readFileSync(rosterPath, 'utf8')) as Record<string, string>
      return new DirectorySource('指定目录', path, { owners: (name) => roster[name] ?? '' })
    }
    return new DirectorySource('指定目录', path)
  }
  return undefined
}

const { command, options } = parseArgs(process.argv.slice(2))
const dataFile = resolve(options['data'] ?? join(process.cwd(), 'data', 'tracker.db'))
const seedPath = findSeed()
const artifactPath = findArtifacts()
const taskSeedPath = findTaskSeed()
const leaderSeedPath = findLeaderSeed()

if (!seedPath) {
  console.error('找不到岗位种子数据 seed/market-department.json')
  process.exit(1)
}

const db = openDatabase(dataFile)
const counts = seedDatabase(db, seedPath)
const artifactCount = artifactPath ? seedArtifacts(db, artifactPath) : 0
const taskCount = taskSeedPath ? seedFixedTasks(db, taskSeedPath) : 0
const leaderCount = leaderSeedPath ? seedLeaders(db, leaderSeedPath) : 0
const store = new Store(db)

if (command === 'seed') {
  // The counts say what was added; the totals say what the database holds.
  const totals = {
    positions: store.positions().length,
    people: store.people().length,
    duties: store.positions().reduce((sum, p) => sum + store.duties(p.id).length, 0),
    artifacts: store.artifacts().length,
    fixedTasks: store.recurringTasks().length,
  }
  console.log(
    `${dataFile}：现有 ${totals.positions} 个岗位、${totals.people} 名人员、${totals.duties} 条职责、${totals.artifacts} 项预期产出、${totals.fixedTasks} 项固定任务`,
  )
  if (counts.positions > 0) {
    console.log(
      `本次新增：${counts.positions} 个岗位、${counts.people} 名人员、${counts.duties} 条职责、${artifactCount} 项预期产出、${taskCount} 项固定任务`,
    )
  }
  db.close()
  process.exit(0)
}

if (command === 'links') {
  const base = options['base'] ?? `http://localhost:${options['port'] ?? '3000'}`
  for (const person of store.people()) {
    const position = store.positionById(person.positionId)
    console.log(`${person.name}\t${position?.name ?? ''}\t${base}/report?token=${person.token}`)
  }
  db.close()
  process.exit(0)
}

if (command === 'leaders') {
  // Who may see the whole department: the manager, the deputy manager and the
  // company leaders the department head names.
  const action = options['action'] ?? 'list'
  const name = options['name']
  if (action === 'list') {
    const grants = leaderGrants(db.raw)
    if (grants.length === 0) {
      console.log('还没有指定可以查看全部信息的人。')
    }
    for (const grant of grants) {
      const person = store.people().find((p) => p.id === grant.id)
      console.log(`${grant.name}\t${person ? '在花名册上' : '不在花名册上'}\t${grant.id}`)
    }
    db.close()
    process.exit(0)
  }
  if (!name) {
    console.error('请给出名字：--name <姓名>')
    process.exit(1)
  }
  const person = store.people().find((p) => p.name === name)
  const id = person?.id ?? slugify(name)
  if (action === 'add') {
    grantLeaderAccess(db.raw, { id, name, grantedBy: options['by'] ?? '', grantedAt: new Date().toISOString() })
    console.log(`已给 ${name} 查看全部信息的权限。`)
  } else if (action === 'remove') {
    revokeLeaderAccess(db.raw, id)
    console.log(`已收回 ${name} 查看全部信息的权限。`)
  } else {
    console.error('用法：--action list | add | remove')
    process.exit(1)
  }
  db.close()
  process.exit(0)
}

if (command === 'collect') {
  const source = fileSource(options)
  if (!source) {
    console.error('请指定成果文件的位置：--feishu-folder <云空间文件夹 token> 或 --collect-dir <目录>')
    process.exit(1)
  }
  const periodType = options['type'] === 'month' ? 'month' : 'week'
  const outcome = await collect(store, source, {
    periodType,
    periodKey: options['period'],
    periodLabel: options['period-label'],
  })
  console.log(`来源：${outcome.source}`)
  console.log(`周期：${outcome.periodLabel}（${outcome.periodKey}）`)
  console.log(`认出的产出 ${outcome.credited} 份，认不出 ${outcome.uncredited} 份，非成果文件 ${outcome.ignored} 份`)
  if (outcome.unmatched.length > 0) {
    console.log('')
    console.log('认不出的文件：')
    for (const item of outcome.unmatched) {
      console.log(`  ${item.file.name} —— ${item.reason}`)
    }
  }
  db.close()
  process.exit(0)
}

const port = Number(options['port'] ?? process.env['PORT'] ?? 3000)
const host = options['host'] ?? process.env['HOST'] ?? '127.0.0.1'
const baseUrl = options['base'] ?? `http://${host === '0.0.0.0' ? 'localhost' : host}:${port}`

const server = createTrackerServer({ store, db, baseUrl })
server.listen(port, host, () => {
  console.log(`市场部工作台已启动：${baseUrl}`)
  console.log(`数据文件：${dataFile}`)
  console.log(`已加载 ${counts.positions} 个岗位、${counts.people} 名人员`)
})

const shutdown = () => {
  server.close(() => {
    db.close()
    process.exit(0)
  })
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
