# 市场部工作台（market-dept-tracker）

市场部岗位职责与周／月工作完成情况收集平台，第一版。

围绕《市场部岗位职责分工明细表》搭建：把岗位表变成可查询的职责台账，把每个岗位应交的成果列成清单，
同事把手上的成果文件放进指定目录，平台自动识别这些文件属于谁、对应哪项产出，汇总成部门、岗位、到人的完成情况。

## 两种收集方式

**按成果文件（推荐）**：同事把成果文件放进指定目录，不用逐条填表。

```bash
node lib/src/cli.js collect --collect-dir /path/to/共享目录 --type week
node lib/src/cli.js collect --feishu-folder <云空间文件夹 token> --type week
```

已经建好一个飞书云空间文件夹：**市场部成果文件**（token `QdoHfTzajlRwxedg2skc8YW3nob`）。
把同事拉进这个文件夹，他们把成果文件放进去，然后跑：

```bash
node lib/src/cli.js collect --feishu-folder QdoHfTzajlRwxedg2skc8YW3nob --type week
```

平台读这个目录（或飞书云空间文件夹），按文件名里的关键词认出这是哪项产出、按上传人认出是哪个岗位，
然后把该岗位的职责算成已完成。没有交的文件算未交，认不出来的文件列出来并说明原因，不静默丢弃。

文件名写成「产出名」就够，例如 `合同台账-2026年11月.xlsx`；上传人是同事时，平台按上传人认岗位。
同名产出分多人时，会合并显示「已交/应交」。

**按填报**：每人一条专属链接，打开即填，逐条职责选状态。适合需要写小结、说卡点的情况。

两种方式的总览页可以随时切换，填报是补充，成果文件是主渠道。

## 微信小程序

`miniprogram/` 是微信小程序版，电脑和手机都能用，同事进入一次自己的链接即可。
它和网页读同一份数据，覆盖我的任务、职责与填报、部门总览、成果采集、岗位职责、下达任务、固定任务；
发布需要小程序账号、HTTPS 域名和微信审核，步骤见
[miniprogram/README.md](miniprogram/README.md)。

## 快速开始

需要 Node.js 22.19+ 或 24+（用到内置的 `node:sqlite`，无需编译原生模块）。

```bash
cd apps/market-dept-tracker
pnpm install
pnpm build
node lib/src/cli.js --port 3000
```

首次启动会自动建库并写入岗位数据和预期产出清单，浏览器打开 <http://127.0.0.1:3000> 即可。

常用参数：

```bash
node lib/src/cli.js --port 3000 --host 0.0.0.0 --data ./data/tracker.db
node lib/src/cli.js collect --collect-dir ./共享目录 --type week    # 采集成果文件
node lib/src/cli.js collect --feishu-folder fldcnXXXX               # 采集飞书云空间
node lib/src/cli.js links --base https://your-host                   # 打印每个人的专属填报链接
node lib/src/cli.js seed --data ./data/tracker.db                    # 只重建岗位数据，不动填报记录
```

## 页面

- **总览**（`/`）：产出到位率、平均完成度、受阻事项；按岗位汇总，并列出每个人的完成情况。可切换「按成果文件统计」和「按填报统计」。
- **成果采集**（`/collection`）：认出了哪些产出、认不出的文件和原因、各岗位的预期产出与识别关键词。
- **岗位职责**（`/ledger`）：按层级查看每个岗位的职责范围、在岗人员和职责边界。
- **我的填报**（`/report?token=...`）：逐条职责填周报／月报。
- **填报链接**（`/links`）：每个人的专属链接，可一次复制。

## 数据来源与结构

岗位、层级、职责条目和人员都来自《市场部岗位职责分工明细表(3)(1).xlsx》，
已整理进 `seed/market-department.json`：

- 层级：经理 → 副经理 → 专责 → 业务员／班组
- 17 个岗位、119 条职责条目、25 名在岗人员
- 表里未填人的岗位（技经业务员、物资管理业务员、结算班）保留岗位和职责，人员待定，在台账上标注原因

职责条目已从原表的整段文字拆成一条条可勾选、可打分的条目，填报时按条对应，汇总时才能算出完成度。

每个岗位的**预期产出**写在 `seed/artifacts.json` 里：一项产出覆盖哪几条职责、哪些关键词能认出它。
这是自动识别的依据，也是「这个岗位该交什么」的清单。

### 表格改了怎么办

原表更新后重新导入：

```bash
node --experimental-strip-types scripts/import-xlsx.ts <新的表格.xlsx> seed/market-department.json
```

导入脚本会按表里的行列布局重新生成岗位数据。它内置了一份行位置对照表（`ROW_MAP`），
说明每个岗位在第几行、哪一列；原表结构调整时，改这份对照表即可，解析逻辑不用动。
导入后跑一遍 `pnpm test`，其中的「种子文件格式正确」会检查岗位、职责和人员是否还对得上。

产出清单是单独一份 `seed/artifacts.json`，岗位变了或者换了产出，改这份就行，不用动导入脚本。
导入脚本只依赖 Node 内置模块，表格本身不会被提交到仓库。

## 目录结构

```
src/
  types.ts    领域模型：岗位、职责、产出、采集结果
  db.ts       SQLite 建表与数据写入（node:sqlite，无原生依赖）
  store.ts    读写与汇总：岗位树、产出、采集结果、按岗位和按人汇总
  collector.ts 文件来源（飞书云空间／本地目录）与识别规则
  collection.ts 采集流程：读文件、认产出、认归属
  period.ts   周／月周期计算（ISO 周、月份）
  views.ts    页面渲染
  html.ts     HTML 片段与页面骨架
  server.ts   HTTP 路由
  cli.ts      命令行入口
  style.css   样式
seed/
  market-department.json   岗位数据（来自 xlsx）
  artifacts.json           各岗位的预期产出与识别关键词
test/
  tracker.test.ts          领域逻辑与数据正确性测试
  collector.test.ts        识别与采集测试
scripts/
  import-xlsx.ts           从 xlsx 重新生成岗位数据
  make-demo-folder.ts      生成一份示例成果目录
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
- 服务默认只监听本机，发链接给同事前需要用 `--host 0.0.0.0` 并放在同事能访问到的地址后面。
- 目前只有市场部一个部门；接入工程部、综合部、安监部时，在 `seed/` 增加部门数据即可，数据模型已按部门划分。
