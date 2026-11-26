// The department rewrote the duty table on 2026-11-16. The seed still holds
// the old table, so this script runs after every start and puts the new org
// on top of it: renames, parents, new posts, the new duties, the people who
// moved and the accounts that follow the posts. It is idempotent.
const { DatabaseSync } = require('node:sqlite')
const { randomBytes, scryptSync } = require('node:crypto')
const { readFileSync } = require('node:fs')

const payload = JSON.parse(readFileSync(process.env.ORG_PAYLOAD || '/home/ubuntu/org-1116.json', 'utf8'))
const db = new DatabaseSync(process.env.ORG_DB || '/home/ubuntu/market-dept-tracker/data/tracker.db')
db.exec('PRAGMA foreign_keys = ON')
const dutyColumns = db.prepare('PRAGMA table_info(duties)').all()
if (!dutyColumns.some((column) => column.name === 'retired')) {
  db.exec('ALTER TABLE duties ADD COLUMN retired INTEGER NOT NULL DEFAULT 0')
}
const positionColumns = db.prepare('PRAGMA table_info(positions)').all()
if (!positionColumns.some((column) => column.name === 'retired')) {
  db.exec('ALTER TABLE positions ADD COLUMN retired INTEGER NOT NULL DEFAULT 0')
}

const log = []
const position = (name) => db.prepare('SELECT id, name, parent_id FROM positions WHERE name = ?').get(name)
const dept = db.prepare('SELECT id FROM departments ORDER BY code LIMIT 1').get()
const vgm = position('分管市场部副总经理')

const rename = (from, to) => {
  const row = position(from)
  if (!row) return position(to)
  if (!position(to)) {
    db.prepare('UPDATE positions SET name = ? WHERE id = ?').run(to, row.id)
    log.push(`${from} -> ${to}`)
  }
  return position(to) ?? row
}

rename('副经理', '副经理1')
rename('市场营销和合同管理专责', '市场营销专责')

const manager = position('经理')
const dep1 = position('副经理1')
const dep2 = position('副经理2')
const marketing = position('市场营销专责')
const tech = position('技经管理专责')
const bid = position('招标与采购专责')
const warehouse = position('物资与仓储专责')
const bidStaff = position('招投标管理业务员')
const techStaff = position('技经业务员')
const contractStaff = position('合同管理业务员')
const purchaseStaff = position('采购管理业务员')
const warehouseStaff = position('物资管理业务员')
const materialStaff = position('项目部材料员')
const warehouseTeam = position('仓储班')
const settlement = position('结算班')
const solar = position('光伏+用户标团')

const ensurePosition = (name, role, level, parentId) => {
  const existing = position(name)
  if (existing) return existing
  const max = db.prepare('SELECT MAX(ordinal) AS m FROM positions').get()
  const id = 'p-' + randomBytes(4).toString('hex')
  db.prepare(
    `INSERT INTO positions (id, department_id, name, role, level, parent_id, headcount_note, instructions, boundary, note, ordinal)
     VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', ?)`,
  ).run(id, dept.id, name, role, level, parentId, (max && max.m ? max.m : 0) + 1)
  log.push('added position ' + name)
  return { id, name }
}

const contractPost = position('合同管理专责') ?? ensurePosition('合同管理专责', '专责', 'specialist', dep1 ? dep1.id : null)
const marketingStaffPost =
  position('市场营销业务员') ?? ensurePosition('市场营销业务员', '业务员', 'specialist_staff', marketing ? marketing.id : null)

const setParent = (id, parentId) => {
  if (id && parentId) db.prepare('UPDATE positions SET parent_id = ? WHERE id = ?').run(parentId, id)
}
setParent(manager && manager.id, vgm && vgm.id)
setParent(dep1 && dep1.id, vgm && vgm.id)
setParent(dep2 && dep2.id, vgm && vgm.id)
setParent(settlement && settlement.id, manager && manager.id)
setParent(warehouse && warehouse.id, dep2 && dep2.id)
setParent(marketing && marketing.id, manager && manager.id)
setParent(tech && tech.id, manager && manager.id)
setParent(bid && bid.id, dep1 && dep1.id)
setParent(contractPost && contractPost.id, dep1 && dep1.id)
setParent(contractStaff && contractStaff.id, contractPost && contractPost.id)
setParent(warehouseStaff && warehouseStaff.id, warehouse && warehouse.id)
setParent(materialStaff && materialStaff.id, warehouse && warehouse.id)
setParent(warehouseTeam && warehouseTeam.id, warehouse && warehouse.id)
setParent(techStaff && techStaff.id, tech && tech.id)
setParent(bidStaff && bidStaff.id, bid && bid.id)
setParent(purchaseStaff && purchaseStaff.id, bid && bid.id)
setParent(marketingStaffPost && marketingStaffPost.id, marketing && marketing.id)
setParent(solar && solar.id, marketing && marketing.id)

const applyDuties = (postId, instructions, items) => {
  if (!postId) return
  db.prepare('UPDATE positions SET instructions = ? WHERE id = ?').run(instructions ?? '', postId)
  const keep = []
  items.forEach((text, index) => {
    const id = `${postId}#${index + 1}`
    keep.push(id)
    db.prepare(
      `INSERT INTO duties (id, position_id, ordinal, text, retired) VALUES (?, ?, ?, ?, 0)
       ON CONFLICT (id) DO UPDATE SET text = excluded.text, ordinal = excluded.ordinal, retired = 0`,
    ).run(id, postId, index + 1, text)
  })
  // Everything the new list does not name is retired (older report items may
  // still point at it, so it is hidden rather than deleted).
  const placeholders = keep.map(() => '?').join(',')
  db.prepare(`UPDATE duties SET retired = 1 WHERE position_id = ? AND id NOT IN (${placeholders})`).run(postId, ...keep)
}
applyDuties(manager && manager.id, payload.manager.instructions, payload.manager.items)
applyDuties(dep1 && dep1.id, payload.deputy1.instructions, payload.deputy1.items)
applyDuties(dep2 && dep2.id, payload.deputy2.instructions, payload.deputy2.items)
applyDuties(marketing && marketing.id, payload.marketingLead.instructions, payload.marketingLead.items)
applyDuties(tech && tech.id, payload.techLead.instructions, payload.techLead.items)
applyDuties(bid && bid.id, payload.bidLead.instructions, payload.bidLead.items)
applyDuties(contractPost && contractPost.id, payload.contractLead.instructions, payload.contractLead.items)
applyDuties(warehouse && warehouse.id, payload.warehouseLead.instructions, payload.warehouseLead.items)
applyDuties(techStaff && techStaff.id, '', payload.techStaff.items)
applyDuties(bidStaff && bidStaff.id, '', payload.bidStaff.items)
applyDuties(contractStaff && contractStaff.id, '', payload.contractStaff.items)
applyDuties(warehouseStaff && warehouseStaff.id, '', payload.warehouseStaff.items)
applyDuties(warehouseTeam && warehouseTeam.id, payload.warehouseTeam.instructions, payload.warehouseTeam.items)
applyDuties(materialStaff && materialStaff.id, '', payload.materialStaff.items)
applyDuties(marketingStaffPost && marketingStaffPost.id, '', ['按专责安排完成市场营销的具体事务（新表未单列，待部门补充）'])

const personByName = (name) => db.prepare('SELECT id, position_id FROM people WHERE name = ?').get(name)
const ensurePerson = (name, positionId, pending) => {
  const existing = personByName(name)
  if (existing) {
    if (existing.position_id !== positionId) {
      db.prepare('UPDATE people SET position_id = ? WHERE id = ?').run(positionId, existing.id)
    }
    return existing
  }
  const id = 'p-' + randomBytes(4).toString('hex')
  db.prepare(
    'INSERT INTO people (id, department_id, name, position_id, token, pending, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
  ).run(id, dept.id, name, positionId, randomBytes(16).toString('hex'), pending, '')
  log.push('added person ' + name)
  return { id, position_id: positionId }
}
const movePerson = (name, positionId) => {
  const person = personByName(name)
  if (person && positionId && person.position_id !== positionId) {
    db.prepare('UPDATE people SET position_id = ? WHERE id = ?').run(positionId, person.id)
    log.push(`${name} moved`)
  }
}

const managerPerson = db.prepare('SELECT id, name FROM people WHERE position_id = ? ORDER BY name LIMIT 1').get(manager.id)
if (managerPerson && managerPerson.name !== '蔡金强') {
  db.prepare('UPDATE people SET name = ? WHERE id = ?').run('蔡金强', managerPerson.id)
  log.push('manager -> 蔡金强')
}
// The two deputy posts are held by real people now; keep the person and the
// account name in step on every start.
const renamePersonAt = (positionId, name) => {
  if (!positionId) return
  const person = db.prepare('SELECT id, name FROM people WHERE position_id = ? ORDER BY name LIMIT 1').get(positionId)
  if (!person) return
  if (person.name !== name) {
    db.prepare('UPDATE people SET name = ? WHERE id = ?').run(name, person.id)
    log.push(`${person.name} -> ${name}`)
  }
  db.prepare('UPDATE accounts SET username = ? WHERE person_id = ?').run(name, person.id)
}
renamePersonAt(dep1 && dep1.id, '袁静')
renamePersonAt(dep2 && dep2.id, '李捷谦')
const fengWenjing = personByName('冯文静')
if (fengWenjing && bidStaff) movePerson('冯文静', bidStaff.id)
const fengTianyou = warehouse ? ensurePerson('冯天佑', warehouse.id, 0) : undefined
if (techStaff) ensurePerson('莫楠', techStaff.id, 0)
if (bidStaff) ensurePerson('汪桦', bidStaff.id, 1)
if (marketingStaffPost) movePerson('林嘉美', marketingStaffPost.id)

const hash = (password) => {
  const salt = randomBytes(16)
  return `scrypt$${salt.toString('hex')}$${scryptSync(password, salt, 32).toString('hex')}`
}
const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
const newPassword = () => Array.from(randomBytes(10), (b) => alphabet[b % alphabet.length]).join('')
const newToken = () => randomBytes(16).toString('hex')

const managerAccount = managerPerson
  ? db.prepare('SELECT id FROM accounts WHERE person_id = ?').get(managerPerson.id)
  : undefined
if (managerAccount) {
  db.prepare("UPDATE accounts SET username = '蔡金强' WHERE id = ?").run(managerAccount.id)
}

if (fengWenjing && fengTianyou) {
  const account = db.prepare('SELECT id FROM accounts WHERE person_id = ?').get(fengWenjing.id)
  if (account) {
    const password = newPassword()
    db.prepare(
      "UPDATE accounts SET person_id = ?, username = '冯天佑', password_hash = ?, password_plain = ?, token = ? WHERE id = ?",
    ).run(fengTianyou.id, hash(password), password, newToken(), account.id)
    log.push('warehouse account -> 冯天佑')
  }
}

const ensureSharedAccount = (positionId, username) => {
  if (!positionId) return
  const existing = db.prepare('SELECT id FROM accounts WHERE position_id = ? AND person_id IS NULL').get(positionId)
  if (existing) return
  const password = newPassword()
  db.prepare(
    'INSERT INTO accounts (id, position_id, person_id, username, password_hash, password_plain, token, created_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?)',
  ).run('acct-' + positionId, positionId, username, hash(password), password, newToken(), new Date().toISOString())
  log.push('added account ' + username)
}
if (marketingStaffPost) ensureSharedAccount(marketingStaffPost.id, '市场营销业务员')
if (techStaff) ensureSharedAccount(techStaff.id, '技经业务员')

// The 11-16 table leaves these posts out: they are retired (hidden from the
// tree and the lists) and their logins stop working. Their history stays.
const retiredPosts = ['光伏+用户标团', '市场拓展业务员', '客户服务业务员', '采购管理业务员']
for (const name of retiredPosts) {
  const row = position(name)
  if (!row) continue
  const before = db.prepare('SELECT retired FROM positions WHERE id = ?').get(row.id)
  db.prepare('UPDATE positions SET retired = 1 WHERE id = ?').run(row.id)
  const removed = db.prepare('DELETE FROM accounts WHERE position_id = ?').run(row.id)
  if (!before || !before.retired || removed.changes) {
    log.push('retired post ' + name + (removed.changes ? ' (account removed)' : ''))
  }
}
// 李建眉 was on the buyer post; the new table has them helping the bid team.
if (bidStaff) movePerson('李建眉', bidStaff.id)

const summary = {
  log,
  posts: db
    .prepare('SELECT p.name, p.level, p.parent_id, (SELECT COUNT(*) FROM duties d WHERE d.position_id = p.id AND d.retired = 0) AS duties FROM positions p WHERE p.retired = 0 ORDER BY p.ordinal')
    .all(),
  people: db.prepare('SELECT name, position_id, pending FROM people ORDER BY name').all(),
}
console.log(JSON.stringify(summary, null, 2))
db.close()
