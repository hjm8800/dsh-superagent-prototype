import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { openDatabase, seedArtifacts, seedDatabase } from '../src/db.ts'
import { Store } from '../src/store.ts'
import { collect } from '../src/collection.ts'
import { isOutcomeFile, matchArtifact, matchPerson, periodOf } from '../src/collector.ts'
import type { CollectedFile } from '../src/types.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const SEED = join(HERE, '..', 'seed', 'market-department.json')
const ARTIFACTS = join(HERE, '..', 'seed', 'artifacts.json')

function freshStore() {
  const db = openDatabase(':memory:')
  seedDatabase(db, SEED)
  seedArtifacts(db, ARTIFACTS)
  return { db, store: new Store(db) }
}

/** A source that returns the files it is given, without touching a disk. */
function fakeSource(files: CollectedFile[]) {
  return {
    name: '测试目录',
    async list(): Promise<CollectedFile[]> {
      return files
    },
  }
}

function file(name: string, ownerName = '', modifiedAt = '2026-11-05T03:00:00.000Z'): CollectedFile {
  return { sourceId: name, name, ownerName, modifiedAt, location: `/tmp/${name}` }
}

// ------------------------------------------------------------- recognition

test('outcome files are recognized by their extension', () => {
  assert.equal(isOutcomeFile('合同台账.xlsx'), true)
  assert.equal(isOutcomeFile('盘点表.pdf'), true)
  assert.equal(isOutcomeFile('notes.txt'), true)
  assert.equal(isOutcomeFile('photo.jpg'), false)
  assert.equal(isOutcomeFile('archive.zip'), false)
})

test('a file name that holds a keyword is recognized as that output', () => {
  const { store } = freshStore()
  const artifacts = store.artifacts()
  const match = matchArtifact('黄美琳-合同台账-2026年11月.xlsx', artifacts)
  assert.ok(match, 'the contract ledger should be recognized')
  assert.equal(match.artifact.artifact, '合同原件与合同台账')
  assert.equal(match.keyword, '合同台账')
})

test('the most specific keyword wins when a name holds several', () => {
  const { store } = freshStore()
  const artifacts = store.artifacts()
  const match = matchArtifact('物资收发存盘点表.xlsx', artifacts)
  assert.ok(match)
  assert.equal(match.keyword, '收发存')
})

test('a name without any keyword is not recognized', () => {
  const { store } = freshStore()
  assert.equal(matchArtifact('随便一个文件.xlsx', store.artifacts()), undefined)
})

test('the output of the filer own post is preferred over a colleague s', () => {
  const { store } = freshStore()
  const artifacts = store.artifacts()
  const specialist = store.positions().find((p) => p.name === '招标与采购专责')!
  const clerk = store.positions().find((p) => p.name === '招投标管理业务员')!
  // "投标" is a keyword of both posts; the filer's own post wins.
  const match = matchArtifact('投标文件归档-11月.xlsx', artifacts, specialist.id)
  assert.ok(match)
  assert.equal(match.artifact.positionId, specialist.id)
  assert.notEqual(match.artifact.positionId, clerk.id)
})

test('a file goes to the output of the post its owner belongs to', async () => {
  const { store } = freshStore()
  // 曾梦妍 is the 招标与采购专责; "投标" is a keyword of her post and of the
  // clerk's post under her, and her own post must win.
  await collect(store, fakeSource([file('曾梦妍-投标文件归档-11月.xlsx', '曾梦妍')]), {
    periodType: 'week',
    periodKey: '2026-W45',
  })
  const specialist = store.positions().find((p) => p.name === '招标与采购专责')!
  const files = store.collectedFiles('week', '2026-W45')
  assert.equal(files.length, 1)
  const artifact = store.artifacts().find((a) => a.id === files[0]?.artifactId)
  assert.equal(artifact?.positionId, specialist.id)
})

test('a file whose keyword only fits another post still counts for them', () => {
  const { store } = freshStore()
  const artifacts = store.artifacts()
  const clerk = store.positions().find((p) => p.name === '招投标管理业务员')!
  const match = matchArtifact('分包商履约评价.xlsx', artifacts, clerk.id)
  assert.ok(match)
  assert.equal(match.artifact.positionId, clerk.id)
})

test('a file is credited to its uploader, or to a name in the file name', () => {
  const people = [
    { id: 'huang-meilin', name: '黄美琳', positionId: 'contract-clerk' },
    { id: 'lin-jiamei', name: '林嘉美', positionId: 'purchase-clerk' },
  ]
  assert.equal(matchPerson('合同台账.xlsx', '黄美琳', people)?.id, 'huang-meilin')
  assert.equal(matchPerson('黄美琳-合同台账.xlsx', '', people)?.id, 'huang-meilin')
  assert.equal(matchPerson('合同台账.xlsx', '别人', people), undefined)
  // The post of the person comes with them, so the file can be matched to
  // the outputs of their own post.
  assert.equal(matchPerson('合同台账.xlsx', '黄美琳', people)?.positionId, 'contract-clerk')
})

test('a file belongs to the period it was last changed', () => {
  assert.equal(periodOf('2026-11-05T03:00:00.000Z', 'week').key, '2026-W45')
  assert.equal(periodOf('2026-11-05T03:00:00.000Z', 'month').key, '2026-11')
  assert.equal(periodOf('not a date', 'week').key, '')
})

// -------------------------------------------------------------- collection

test('a collected file credits the duty it answers for', async () => {
  const { store } = freshStore()
  const outcome = await collect(store, fakeSource([file('黄美琳-合同台账-11月.xlsx', '黄美琳')]), {
    periodType: 'week',
    periodKey: '2026-W45',
    periodLabel: '2026年第45周',
  })
  assert.equal(outcome.credited, 1)
  assert.equal(outcome.uncredited, 0)
  assert.equal(outcome.matches.length, 1)

  const clerk = store.people().find((p) => p.name === '黄美琳')!
  const progress = store.artifactProgressByPerson('week', '2026-W45').find((p) => p.personId === clerk.id)!
  assert.ok(progress.expected > 0, 'the post should expect outputs')
  assert.ok(progress.done > 0, 'the filed contract ledger should count')
  assert.equal(progress.files, 1)
})

test('a file nobody can place is kept and explained, not dropped', async () => {
  const { store } = freshStore()
  const outcome = await collect(store, fakeSource([file('随手拍的照片.xlsx', '不认识的人')]), {
    periodType: 'week',
    periodKey: '2026-W45',
  })
  assert.equal(outcome.credited, 0)
  assert.equal(outcome.uncredited, 1)
  assert.equal(outcome.unmatched.length, 1)
  assert.ok(outcome.unmatched[0]?.reason, 'the unmatched file must say why')
  // It is still stored, so the manager can see it.
  assert.equal(store.collectedFiles('week', '2026-W45').length, 1)
})

test('a file whose output is known but whose owner is not is explained', async () => {
  const { store } = freshStore()
  const outcome = await collect(store, fakeSource([file('合同台账.xlsx', '')]), {
    periodType: 'week',
    periodKey: '2026-W45',
  })
  assert.equal(outcome.uncredited, 1)
  assert.match(outcome.unmatched[0]?.reason ?? '', /认不出是谁/)
})

test('files that are not outcome files are ignored, not counted as missing', async () => {
  const { store } = freshStore()
  const outcome = await collect(store, fakeSource([file('photo.jpg', '黄美琳'), file('archive.zip', '黄美琳')]), {
    periodType: 'week',
    periodKey: '2026-W45',
  })
  assert.equal(outcome.ignored, 2)
  assert.equal(outcome.credited, 0)
  assert.equal(store.collectedFiles('week', '2026-W45').length, 0)
})

test('a markdown report counts as an outcome file', () => {
  assert.equal(isOutcomeFile('周报说明.md'), true)
})

test('collecting twice updates the same file rather than adding a second one', async () => {
  const { store } = freshStore()
  await collect(store, fakeSource([file('合同台账.xlsx', '黄美琳')]), {
    periodType: 'week',
    periodKey: '2026-W45',
  })
  await collect(store, fakeSource([file('合同台账.xlsx', '黄美琳')]), {
    periodType: 'week',
    periodKey: '2026-W45',
  })
  assert.equal(store.collectedFiles('week', '2026-W45').length, 1)
})

test('without a named period each file counts towards the week it was changed in', async () => {
  const { store } = freshStore()
  await collect(
    store,
    fakeSource([
      file('合同台账-本周.xlsx', '黄美琳', '2026-11-05T03:00:00.000Z'),
      file('资金计划-上周.xlsx', '黄美琳', '2026-10-28T03:00:00.000Z'),
    ]),
    { periodType: 'week' },
  )
  assert.equal(store.collectedFiles('week', '2026-W45').length, 1)
  assert.equal(store.collectedFiles('week', '2026-W44').length, 1)
  // The week the file was changed in is the week the numbers belong to.
  const lastWeek = store.artifactProgressByPerson('week', '2026-W44').find((p) => p.personName === '黄美琳')
  assert.ok((lastWeek?.done ?? 0) > 0, 'the file from last week must count for last week')
})

test('a post where nobody filed anything shows as incomplete', () => {
  const { store } = freshStore()
  const progress = store.artifactProgress('week', '2026-W45')
  const warehouse = progress.find((p) => p.positionName === '仓储班')!
  assert.ok(warehouse, '仓储班 should be in the progress')
  assert.equal(warehouse.reported, 0)
  assert.equal(warehouse.total, 5)
  assert.equal(warehouse.percent, 0)
  assert.ok(warehouse.expected > 0, 'the post should expect outputs')
})

test('the expected outputs are loaded for every post that declares one', () => {
  const { store } = freshStore()
  const artifacts = store.artifacts()
  assert.ok(artifacts.length >= 12, `expected a set of expected outputs, got ${artifacts.length}`)
  for (const artifact of artifacts) {
    assert.ok(artifact.keywords.length > 0, `${artifact.artifact} has no keywords`)
    assert.ok(artifact.dutyIds.length > 0, `${artifact.artifact} covers no duty`)
    // The duties it names must exist.
    const position = store.positionById(artifact.positionId)!
    for (const dutyId of artifact.dutyIds) {
      assert.ok(
        position.duties.some((duty) => duty.id === dutyId),
        `${artifact.artifact} names a duty that does not exist: ${dutyId}`,
      )
    }
  }
})
