// Fill the database with sample reports, so screenshots and demos show the
// dashboard with data instead of an empty state.
import { openDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const DATA = process.argv[2] ?? join(HERE, '..', 'data', 'tracker.db')
const db = openDatabase(DATA)
const store = new Store(db)

type Pattern = { name: string; summary: string; blockers: string; next: string; mix: string[] }

const PATTERNS: Pattern[] = [
  {
    name: '黄美琳',
    summary: '完成工程合同、分包合同原件归档与合同台账登记；汇总各班组资金计划并上报；完成回款提醒与对账。',
    blockers: '财务发票流转较慢，影响部分付款资料闭环。',
    next: '推进逾期货款催收，完成本月合同终审。',
    mix: ['done', 'done', 'in_progress', 'done', 'in_progress', 'not_started', 'done'],
  },
  {
    name: '黎嘉良',
    summary: '牵头完成非关联业务成本测算与采购限价；与重大客户完成商务谈判并签约 2 项；统筹经营指标与回款率。',
    blockers: '个别项目结算资料尚未闭环，影响回款率统计口径。',
    next: '推进客户回访与工程回访，组织政策宣贯。',
    mix: ['done', 'done', 'in_progress', 'done', 'in_progress', 'done', 'not_started'],
  },
  {
    name: '刘俊杰',
    summary: '完成 3 个项目部工程预结算编制与成本分析；班组计提资料审核完成；分包人工费用限价测算已下发。',
    blockers: '',
    next: '核对成本预警数据，配合合同专责审查结算条款。',
    mix: ['done', 'done', 'in_progress', 'done', 'in_progress', 'not_started'],
  },
  {
    name: '曾梦妍',
    summary: '组织分包招标 2 场，投标文件制作与递交完成；完成分包商资质备案与履约评价资料整理。',
    blockers: '招投标管理业务员岗位尚未到位，投标统筹工作由 3 人分摊，效率偏低。',
    next: '推进采购流程对接，完善供应商价格库。',
    mix: ['done', 'in_progress', 'done', 'blocked', 'not_started'],
  },
  {
    name: '林嘉美',
    summary: '完成二十万以下自主采购全流程 5 笔；供应商信息维护与价格库更新完成；采购合同、发票、验收单移交合同专责。',
    blockers: '',
    next: '继续跟踪采购系统流程与付款资料归档。',
    mix: ['done', 'done', 'in_progress', 'not_started', 'done'],
  },
  {
    name: '冯文静',
    summary: '完成工程物资到货计划安排与班组材料对数；仓储班收发存盘点完成，账卡物一致。',
    blockers: '部分物资短缺尚未反馈到位，需采购专责协同。',
    next: '完成物资电子账与手工账核对，组织月度抽查。',
    mix: ['done', 'in_progress', 'done', 'not_started', 'in_progress', 'done'],
  },
  {
    name: '潘清河',
    summary: '完成仓库物料收发存登记与呆滞物料台账整理，单据归档齐全。',
    blockers: '',
    next: '配合完成月度盘点与废旧物资处理。',
    mix: ['done', 'done', 'in_progress', 'not_started'],
  },
  {
    name: '蓝婉嫦',
    summary: '完成 2 个项目部材料对账与结算材料整理，局材料增资计划调拨已上报。',
    blockers: '项目部余料退库进度滞后。',
    next: '监督班组按期完成退库。',
    mix: ['done', 'in_progress', 'not_started', 'done'],
  },
]

const PERIODS = [
  { type: 'week', key: '2026-W45', label: '2026年第45周' },
  { type: 'month', key: '2026-10', label: '2026年10月' },
]

let count = 0
for (const period of PERIODS) {
  for (const pattern of PATTERNS) {
    const person = store.people().find((p) => p.name === pattern.name)
    if (!person) continue
    const position = store.positionById(person.positionId)
    if (!position) continue
    const duties = position.duties
    const items = duties.map((duty, index) => {
      const status = (pattern.mix[index % pattern.mix.length] ?? 'not_started') as
        | 'done'
        | 'in_progress'
        | 'not_started'
        | 'blocked'
        | 'not_applicable'
      const percent = status === 'done' ? 100 : status === 'in_progress' ? 60 : status === 'blocked' ? 30 : 0
      return { dutyId: duty.id, status, percent, note: '' }
    })
    store.saveReport({
      personId: person.id,
      periodType: period.type,
      periodKey: period.key,
      periodLabel: period.label,
      summary: pattern.summary,
      blockers: pattern.blockers,
      nextPlan: pattern.next,
      items,
    })
    count++
  }
}

console.log(`wrote ${count} sample reports`)
db.close()
