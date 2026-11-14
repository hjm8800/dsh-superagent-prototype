# 结算AI工作台（MVP）

配网结算资料解析、三算对比、AI 操作台和复核留痕的可运行版本。演示数据已匿名化为项目 A/B/C。

## 功能

- 工作台：项目总览、待解析、风险提醒、待办节点；支持项目新建/编辑。
- 资料包：批量上传 DOCX/XLSX/XLS/PDF/XML/ZIP/DWG/DOC/图片，自动解析并记录状态；支持原文件下载。
- 设计解析：解析后自动导入项目画像、材料清册（甲供/乙供）、工程量、预算汇总；材料/工程量/预算可手工新增、删除、导出 Excel。
- 三算对比：设计量、预算量、结算量同表对比，可更新结算量并导出 CSV/Excel。
- 结算书初稿：按项目画像、材料、工程量、三算对比和预算生成 Excel 初稿，供人工复核。
- 材料超领：按供电所、班组汇总出库大于结算的材料，计算超领金额、跟踪向班组追回，支持 Excel 模板导入/导出。
- AI 操作台：资料完整性检查、设计资料解析、三算对比、结算书初稿、对数问答；未配置 LLM 时生成规则初稿，配置后调用私有化模型。
- 复核：AI 初稿“通过/退回”，复核记录与审计日志。
- 账号与权限：本地账号，管理员可创建用户/重置密码；项目成员维护；资料员、结算员、物资员、项目经理、管理员五种角色。

## 演示

- 打开 `/demo`（无需登录）查看 5 分钟演示路线：建项目、收资料、解析入库、现场验收、三算对比、AI 初稿与复核、结算书初稿、定案归档。
- 演示页可下载 8 页 `settlement-ai-demo.pptx` / PDF；文件在 `docs/settlement/`。
- 演示账号：`admin / admin123`；进入工作台后先改密码。
- 演示数据为匿名项目 A/B/C，仅用于内部演示。

## 运行

### Windows 一键启动

```powershell
cd apps/settlement-ai-platform
.\start.ps1
```

打开 http://127.0.0.1:8090 。脚本会创建 `.venv`、安装依赖、生成随机会话密钥，并以 `PLATFORM_DEMO=0` 启动。

### Linux

```bash
cd apps/settlement-ai-platform
bash start.sh
```

### 直接启动（开发用）

```bash
cd apps/settlement-ai-platform
python -m pip install -r requirements.txt
$env:PLATFORM_DEMO="0"; python -m uvicorn app:app --host 127.0.0.1 --port 8090
```

首次登录：`admin / admin123`。登录后先到“账号”修改密码，再到“用户管理”创建业务账号，最后在项目“成员”里把账号加入项目。

演示账号（仅演示库）：

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| ziliao | 123456 | 资料员 |
| jiesuan | 123456 | 结算员 |
| wuzi | 123456 | 物资员 |
| jingli | 123456 | 项目经理 |
| admin | admin123 | 管理员 |

开发/截图时可加 `?as=ziliao` 直接以演示身份访问（`PLATFORM_DEMO=1`）；生产环境必须设为 0。

## 部署

### Docker

```bash
cd apps/settlement-ai-platform
export PLATFORM_SECRET="$(python -c 'import secrets;print(secrets.token_hex(24))')"
docker compose up -d --build
```

数据保存在 `./data`，升级时保留该目录。生产建议放在内网反向代理（HTTPS）后，并限制访问来源。

### 数据与备份

- 数据库：`data/platform.db`
- 上传文件：`data/uploads/`
- 备份时同时复制以上两项；恢复时放回同一路径并重启。
- 账号是本地账号，不接 SSO；按当前范围也不接供电局系统。

## 解析插件

- DOCX：python-docx；XLSX：openpyxl；XLS：xlrd；PDF：PyMuPDF；XML：lxml；ZIP：zipfile。
- DWG：自动查找 ODA File Converter（`C:\Program Files\ODA\...`），转 DXF 后用 ezdxf 提取文字；未安装时标记“需插件”。
- DOC：自动查找 LibreOffice `soffice`，转 TXT；未安装时标记“需插件”。
- BDY3：预算软件原生格式，需厂商插件；请使用同目录 XLSX/XML 预算导出。
- 图片：Pillow 读取尺寸/EXIF，作为资料挂接。

## 配置

| 变量 | 说明 |
| --- | --- |
| `PLATFORM_DATA_DIR` | 数据目录，默认 `./data` |
| `PLATFORM_SECRET` | 会话签名密钥，生产必须使用随机值 |
| `PLATFORM_DEMO` | 1 开启 `?as=账号` 演示身份；生产设 0 |
| `LLM_BASE_URL` | 可选，OpenAI 兼容接口地址（如私有化模型 `/v1`） |
| `LLM_API_KEY` | 可选，模型密钥 |
| `LLM_MODEL` | 可选，模型名称 |

## MVP 边界

- 提供 Windows/Linux/Docker 启动方式，但尚未部署到公司内网服务器；上线需服务器、域名/端口和备份策略。
- 按当前范围不接 SSO 和供电局系统；账号为本地账号。
- 未做 BDY3 厂商插件、照片视觉识别、完整审批流；这些是后续。
- 解析结果需人工校对后才能作为结算依据；AI 不签章、不定案、不对外承诺。
- 不提交真实项目资料；上传文件保存在本地 `data/uploads`，已在 `.gitignore` 中排除。

## 测试

```bash
python -m pytest -q
```

当前 14 个测试通过。
