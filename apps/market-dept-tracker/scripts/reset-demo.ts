import { DatabaseSync } from 'node:sqlite'

const db = new DatabaseSync(process.argv[2])
db.exec('DELETE FROM report_items')
db.exec('DELETE FROM reports')
db.exec("DELETE FROM tasks WHERE title LIKE '%小程序%'")
const reports = db.prepare('SELECT COUNT(*) c FROM reports').get().c
const tasks = db.prepare('SELECT COUNT(*) c FROM tasks').get().c
const files = db.prepare('SELECT COUNT(*) c FROM collected_files').get().c
console.log(`reports=${reports} tasks=${tasks} collectedFiles=${files}`)
db.close()
