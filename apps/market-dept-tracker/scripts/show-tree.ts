import { openDatabase, seedDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const db = openDatabase(':memory:')
seedDatabase(db, join(HERE, '..', 'seed', 'market-department.json'))
const store = new Store(db)
const tree = store.tree('mkt')

const walk = (nodes: typeof tree, depth = 0): void => {
  for (const node of nodes) {
    console.log(
      '  '.repeat(depth) +
        `${node.name} [${node.id}] people=${node.people.map((p) => p.name).join(',') || '-'}`,
    )
    walk(node.children, depth + 1)
  }
}
walk(tree)
db.close()
