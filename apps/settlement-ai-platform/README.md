# 结算AI工作台（MVP）

配网结算资料解析、三算对比、AI 操作台和复核留痕的最小可用版本。为演示和试用，数据已匿名化为项目 A/B/C。

## 功能

- 工作台：项目总览、待解析、风险提醒、待办节点。
- 资料包：上传 DOCX/XLSX/XLS/PDF/XML/ZIP/DWG/DOC/图片，自动解析并记录状态。
- 设计解析：项目画像、材料清册（甲供/乙供）、工程量、预算汇总。
- 三算对比：设计量、预算量、结算量同表对比，可更新结算量并导出 CSV。
- AI 操作台：资料完整性检查、设计资料解析、三算对比、结算书初稿、对数问答；未配置 LLM 时生成规则初稿，配置后调用私有化模型。
- 复核：AI 初稿“通过/退回”，复核记录与审计日志。
- 角色：资料员、结算员、物资员、项目经理、管理员；项目级成员权限。

## 运行

```bash
cd apps/settlement-ai-platform
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8090
```

打开 http://127.0.0.1:8090 。

### 演示账号

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| ziliao | 123456 | 资料员 |
| jiesuan | 123456 | 结算员 |
| wuzi | 123456 | 物资员 |
| jingli | 123456 | 项目经理 |
| admin | admin123 | 管理员 |

开发/截图时可加 `?as=ziliao` 直接以演示身份访问（`PLATFORM_DEMO=1`，默认开启），生产环境应设为 0 并接统一认证。

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
| `PLATFORM_SECRET` | 会话签名密钥，生产必须修改 |
| `PLATFORM_DEMO` | 1 开启 `?as=账号` 演示身份；生产设 0 |
| `LLM_BASE_URL` | 可选，OpenAI 兼容接口地址（如私有化模型 `/v1`） |
| `LLM_API_KEY` | 可选，模型密钥 |
| `LLM_MODEL` | 可选，模型名称 |

## MVP 边界

- 未接公司统一认证、飞书 SSO、供电局系统、预算软件插件；这些是二期。
- 未做完整 RBAC/审批流；当前是项目级成员权限和简单复核。
- 解析结果需人工校对后才能作为结算依据；AI 不签章、不定案、不对外承诺。
- 不提交真实项目资料；上传文件保存在本地 `data/uploads`，已在 `.gitignore` 中排除。

## 测试

```bash
python -m pytest -q
```
