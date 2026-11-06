#!/usr/bin/env node
import { existsSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { openDatabase, seedDatabase } from './db.ts'
import { Store } from './store.ts'
import { createTrackerServer } from './server.ts'

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

const { command, options } = parseArgs(process.argv.slice(2))
const dataFile = resolve(options['data'] ?? join(process.cwd(), 'data', 'tracker.db'))
const seedPath = findSeed()

if (!seedPath) {
  console.error('找不到岗位种子数据 seed/market-department.json')
  process.exit(1)
}

const db = openDatabase(dataFile)
const counts = seedDatabase(db, seedPath)
const store = new Store(db)

if (command === 'seed') {
  console.log(`已写入 ${counts.positions} 个岗位、${counts.people} 名人员、${counts.duties} 条职责到 ${dataFile}`)
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

const port = Number(options['port'] ?? process.env['PORT'] ?? 3000)
const host = options['host'] ?? process.env['HOST'] ?? '127.0.0.1'
const baseUrl = options['base'] ?? `http://${host === '0.0.0.0' ? 'localhost' : host}:${port}`

const server = createTrackerServer({ store, baseUrl })
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
