// Copy the stylesheet and the seed file next to the compiled output, so the
// built app can run from `lib/` without the TypeScript sources.
import { copyFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(dirname(fileURLToPath(import.meta.url)))
const out = join(root, 'lib', 'src')

mkdirSync(out, { recursive: true })
copyFileSync(join(root, 'src', 'style.css'), join(out, 'style.css'))
mkdirSync(join(root, 'lib', 'seed'), { recursive: true })
copyFileSync(join(root, 'seed', 'market-department.json'), join(root, 'lib', 'seed', 'market-department.json'))
copyFileSync(join(root, 'seed', 'artifacts.json'), join(root, 'lib', 'seed', 'artifacts.json'))
copyFileSync(join(root, 'seed', 'tasks.json'), join(root, 'lib', 'seed', 'tasks.json'))
copyFileSync(join(root, 'seed', 'leaders.json'), join(root, 'lib', 'seed', 'leaders.json'))

console.log('copied style.css and the seed files into lib/')
