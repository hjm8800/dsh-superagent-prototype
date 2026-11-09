/**
 * Build a folder of outcome files, the way the team would leave them, so the
 * collector can be exercised end to end and the screenshots show real data.
 *
 *   node --experimental-strip-types scripts/make-demo-folder.ts <folder>
 */
import { mkdir, writeFile, rm } from 'node:fs/promises'
import { join } from 'node:path'

const folder = process.argv[2]
if (!folder) {
  console.error('usage: make-demo-folder.ts <folder>')
  process.exit(1)
}

await rm(folder, { recursive: true, force: true })
await mkdir(folder, { recursive: true })

/** The files the team would leave, with the owner the source would report. */
const files: { path: string; owner: string; body: string }[] = [
  { path: '黄美琳-合同台账-2026年11月.xlsx', owner: '黄美琳', body: '合同台账' },
  { path: '黄美琳-资金计划汇总表-11月.xlsx', owner: '黄美琳', body: '资金计划' },
  { path: '黄美琳-应收对账表-202611.xlsx', owner: '黄美琳', body: '应收对账' },
  { path: '林嘉美-采购台账-2026年11月.xlsx', owner: '林嘉美', body: '采购台账' },
  { path: '林嘉美-价格库更新.xlsx', owner: '林嘉美', body: '价格库' },
  { path: '黎嘉良-经营分析报告-11月.docx', owner: '黎嘉良', body: '经营分析' },
  { path: '黎嘉良-市场拓展计划-2026.docx', owner: '黎嘉良', body: '市场拓展' },
  { path: '刘俊杰-预结算汇总-11月.xlsx', owner: '刘俊杰', body: '预结算' },
  { path: '刘俊杰-成本预警数据.xlsx', owner: '刘俊杰', body: '成本预警' },
  { path: '曾梦妍-投标文件归档-11月.xlsx', owner: '曾梦妍', body: '投标' },
  { path: '曾梦妍-分包商履约评价.xlsx', owner: '曾梦妍', body: '履约评价' },
  { path: '冯文静-物资收发存台账-11月.xlsx', owner: '冯文静', body: '收发存' },
  { path: '冯文静-物资盘点表-2026年11月.xlsx', owner: '冯文静', body: '盘点' },
  { path: '潘清河-入库单据扫描件.pdf', owner: '潘清河', body: '入库单据' },
  { path: '潘清河-出库单据扫描件.pdf', owner: '潘清河', body: '出库单据' },
  { path: '蓝婉嫦-物资到货计划-11月.xlsx', owner: '蓝婉嫦', body: '到货计划' },
  { path: '蓝婉嫦-材料对账表-2026年11月.xlsx', owner: '蓝婉嫦', body: '材料对账' },
  { path: '李建眉-采购订单跟踪表.xlsx', owner: '李建眉', body: '采购' },
  // A file whose output is known but whose owner is not in the name.
  { path: '合同台账（副本）.xlsx', owner: '', body: '合同台账' },
  // A file nobody can place.
  { path: '扫描件20261105-01.pdf', owner: '', body: '扫描件' },
  // A file that is not an outcome at all.
  { path: '现场照片.jpg', owner: '黄美琳', body: '照片' },
]

for (const item of files) {
  const path = join(folder, item.path)
  await writeFile(path, `${item.body}\n${item.owner}\n`, 'utf8')
}

// The owners the source reports, kept beside the folder for the demo run.
await writeFile(
  join(folder, 'owners.json'),
  JSON.stringify(Object.fromEntries(files.map((f) => [f.path, f.owner])), null, 2),
  'utf8',
)

console.log(`wrote ${files.length} files into ${folder}`)
