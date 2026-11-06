# 市场部工作台（market-dept-tracker）

市场部岗位职责与周／月工作完成情况收集平台，第一版。

围绕《市场部岗位职责分工明细表》搭建：把岗位表变成可查询的职责台账，给每名同事一条专属填报链接，
同事按自己的岗位职责逐条填周报／月报，负责人从总览页看到部门、岗位、到人的完成情况。

## 快速开始

需要 Node.js 22.19+ 或 24+（用到内置的 `node:sqlite`，无需编译原生模块）。

```bash
cd apps/market-dept-tracker
pnpm install
pnpm build
node lib/src/cli.js --port 3000
```

首次启动会自动建库并写入岗位数据，浏览器打开 <http://127.0.0.1:3000> 即可。

常用参数：

```bash
node lib/src/cli.js --port 3000 --host 0.0.0.0 --data ./data/tracker.db
node lib/src/cli.js links --base https://your-host   # 打印每个人的专属填报链接
node lib/src/cli.js seed --data ./data/tracker.db    # 只重建岗位数据，不动填报记录
```

## 怎么用

1. **岗位职责**（`/ledger`）：按层级查看每个岗位的职责范围、在岗人员和职责边界。
2. **填报链接**（`/links`）：把每个人的专属链接发给对应同事，可以点「复制全部链接」一次拿走。
   链接带随机 token，同事打开就能填，不需要账号。
3. **我的填报**（`/report?token=...`）：同事按周期选择周报／月报，逐条职责选状态
   （已完成／进行中／未开始／受阻／不适用），填完成百分比和说明，再写本期小结、遇到的问题、下期计划。
   同一周期重复提交会覆盖上次内容，不会叠加。
4. **总览**（`/`）：看填报率、平均完成度、受阻事项；按岗位汇总完成度，并列出每个人的填报明细和卡点。

## 数据来源与结构

岗位、层级、职责条目和人员都来自《市场部岗位职责分工明细表(3)(1).xlsx》，
已整理进 `seed/market-department.json`：

- 层级：经理 → 副经理 → 专责 → 业务员／班组
- 17 个岗位、119 条职责条目、25 名在岗人员
- 表里未填人的岗位（技经业务员、物资管理业务员、结算班）保留岗位和职责，人员待定，
  在台账上标注原因

职责条目已从原表的整段文字拆成一条条可勾选、可打分的条目，
填报时按条对应，汇总时才能算出完成度。

### 表格改了怎么办

原表更新后重新导入：

```bash
node --experimental-strip-types scripts/import-xlsx.ts <新的表格.xlsx> seed/market-department.json
```

导入脚本会按表里的行列布局重新生成岗位数据。它内置了一份行位置对照表（`ROW_MAP`），
说明每个岗位在第几行、哪一列；原表结构调整时，改这份对照表即可，解析逻辑不用动。
导入后跑一遍 `pnpm test`，其中的「种子文件格式正确」会检查岗位、职责和人员是否还对得上。

导入脚本只依赖 Node 内置模块，表格本身不会被提交到仓库。

## 目录结构

```
src/
  types.ts    领域模型：岗位、职责、人员、填报、进度
  db.ts       SQLite 建表与岗位数据写入（node:sqlite，无原生依赖）
  store.ts    读写与汇总：岗位树、填报存取、按岗位汇总进度
  period.ts   周／月周期计算（ISO 周、月份）
  views.ts    页面渲染
  html.ts     HTML 片段与页面骨架
  server.ts   HTTP 路由
  cli.ts      命令行入口
  style.css   样式
seed/
  market-department.json   岗位数据（来自 xlsx）
test/
  tracker.test.ts          领域逻辑与数据正确性测试
scripts/
  demo-data.ts             写入示例填报，用于演示和截图
  capture-shots.ts         截图
  e2e-check.ts             对运行中的服务做端到端检查
```

## 开发

```bash
pnpm check     # 类型检查 + 测试 + 构建
pnpm test      # 只跑测试
```

## 说明

- 数据全部存在一个 SQLite 文件里（默认 `data/tracker.db`），复制这个文件即可整体迁移。
- 填报链接凭 token 访问，适合内部使用；链接泄露即等同于该人的填报权限，群里转发时注意。
- 目前只有市场部一个部门；接入工程部、综合部、安监部时，在 `seed/` 增加部门数据即可，
  数据模型已按部门划分。
