// Capture the UI screenshots used as before/after evidence in the pull
// request. Writes PNG files into docs/shots/.
import { openDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const HERE = dirname(fileURLToPath(import.meta.url))
const BASE = process.argv[2] ?? 'http://127.0.0.1:3111'
const OUT = process.argv[3] ?? join(HERE, '..', '..', '..', 'docs', 'shots')
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const DATA = join(HERE, '..', 'data', 'tracker.db')

const db = openDatabase(DATA)
const store = new Store(db)
const clerk = store.people().find((p) => p.name === '黄美琳')!

const shots: { file: string; url: string; width: number; height: number }[] = [
  { file: 'overview.png', url: `${BASE}/?type=week&period=2026-W45`, width: 1440, height: 1500 },
  { file: 'ledger.png', url: `${BASE}/ledger`, width: 1440, height: 2400 },
  { file: 'report-form.png', url: `${BASE}/report?token=${clerk.token}`, width: 1440, height: 1600 },
  { file: 'links.png', url: `${BASE}/links`, width: 1440, height: 1200 },
]

for (const shot of shots) {
  execFileSync(
    EDGE,
    [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      `--window-size=${shot.width},${shot.height}`,
      `--screenshot=${join(OUT, shot.file)}`,
      '--virtual-time-budget=4000',
      shot.url,
    ],
    { stdio: 'ignore' },
  )
  console.log(`captured ${shot.file}`)
}

db.close()
