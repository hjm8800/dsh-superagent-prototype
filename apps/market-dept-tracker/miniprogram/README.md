# 市场部工作台 · 微信小程序

电脑端和手机端都能用。同事进入一次自己的链接，之后打开小程序就看到自己的任务清单。

## 它能做什么

- **我的任务**：只显示自己的任务（上级下达的和每周／每月固定任务），可以直接改状态、写说明；周／月可以切换。
- **职责与填报**：本岗位的职责逐条列出，选状态、填完成百分比、写说明，再写本期小结，直接在手机上提交周报／月报；周／月可以切换。
- **部门总览**（仅领导）：各岗位已交成果数、完成度、任务完成情况、认不出的文件数；周／月可以切换。
- **成果采集**（仅领导）：认出/认不出的文件数、按岗位的完成度、认不出的文件及原因、认出的产出。
- **岗位职责**（仅领导）：按层级列出全部门每个岗位的职责和职责边界，只显示在岗人数，不显示人名。
- **下达任务**（仅领导）：选一个岗位下达，该岗位的人都收到。
- **固定任务**（仅领导）：给一个岗位设每周／每月的固定任务，之后自动出现在该岗位每个人的清单里。
- 登录用部门发的专属链接，不用账号密码；电脑上的微信打开小程序也能用同一个链接。

它和网页版读同一份数据，一边改了另一边立刻能看到。

## 目录

```
miniprogram/
  app.js              启动、记住链接、发请求
  app.json            页面与底部标签
  app.wxss            样式
  config.js           平台地址（已指向 https://morebrighter.cn）
  project.config.json 开发者工具的项目配置
  pages/
    login/            粘贴链接进入
    tasks/            我的任务
    duties/           职责与填报
    overview/         部门总览（仅领导）
    collection/       成果采集（仅领导）
    ledger/           岗位职责台账（仅领导）
    assign/           下达任务（仅领导）
    recurring/        设固定任务（仅领导）
  sitemap.json
```

## 怎么发起来

AppID 已经填好（`wx22e42e97d2c93a04`），平台已经部署在 `https://morebrighter.cn`。分两步：先在开发机上看到界面，再发布给全部门。

### 第一步：在电脑上看到界面

1. 装 [微信开发者工具](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html)。
2. 「导入项目」，目录选本文件夹，AppID 会自动认出来。
3. 点右上角「详情 → 本地设置」，勾上
   **「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**（还没把域名加进白名单时先这样跑）。
4. 编译。小程序连的是 `config.js` 里的 `https://morebrighter.cn`，用同事的链接就能看到真实数据。

### 第二步：发布给全部门

小程序发布后只能请求 **HTTPS** 域名，而且域名要在微信后台白名单里：

- 平台已经在 HTTPS 上跑着（Nginx + Let's Encrypt 证书，自动续期）。
- 微信后台「开发 → 开发管理 → 服务器域名 → request 合法域名」加上 `https://morebrighter.cn`；
  这个域名需要已完成 ICP 备案。

然后改 `config.js`（已经改好）：

```js
module.exports = { baseUrl: 'https://morebrighter.cn' }
```

最后用开发者工具点「上传」，到微信后台「管理 → 版本管理」提交审核，通过后发布。

## 用法

1. 部门负责人把每个人的专属链接发下去（网页版「填报链接」页可以一键复制）。
2. 同事在微信里打开小程序，把链接粘贴进去，点「进入」。
3. 之后打开小程序就直接是自己的任务清单；换人时点「换个链接」。

## 和网页版的关系

两个前端，一个后端：

```
             ┌── 网页（浏览器）  /  /tasks  /ledger ...
数据库 ── 服务 ┤
             └── 小程序        /api/me  /api/my-tasks  /api/overview ...
```

小程序用的是 `/api/…` 这组接口，和网页走的是同一份数据和同一套权限规则。

## 接口

| 方法 | 地址 | 说明 |
| --- | --- | --- |
| GET | `/api/me?token=` | 这个链接是谁、哪个岗位、能不能看全部门 |
| GET | `/api/periods?type=week` | 可选的周／月 |
| GET | `/api/my-tasks?token=&type=&period=` | 自己的任务清单 |
| POST | `/api/my-tasks/status` | 改任务状态，表单字段 `token`、`id`、`status`、`note` |
| GET | `/api/my-duties?token=&type=&period=` | 本岗位职责和已填报内容 |
| POST | `/api/report` | 提交周报／月报 |
| GET | `/api/overview?token=&type=&period=` | 部门总览（仅领导） |
| GET | `/api/collection?token=&type=&period=` | 成果采集（仅领导） |
| GET | `/api/ledger?token=` | 岗位职责台账（仅领导） |
| GET | `/api/assignable?token=` | 可以下达任务的岗位（仅领导） |
| POST | `/api/tasks/assign` | 下达任务（仅领导） |
| POST | `/api/tasks/recurring` | 设固定任务（仅领导） |

## 还没到位的

- 小程序还没有在微信开发者工具里实际运行过（这台机器上没有装）；上面的页面和接口都按规范写、静态校验和接口联调通过，但在微信里跑通之前不能算验证过。
- 微信后台加 request 合法域名、上传、提交审核这三步只能由管理员账号做。
- 小程序还没有截图；等开发者工具跑通后补上。
