# Zotero 双向同步规范与操作指南 (Zotero Bidirectional Sync)

本文档定义知识库（Obsidian / Markdown）与本地 Zotero 客户端之间的双向全量同步契约，涵盖元数据（Metadata）、阅读笔记（Notes）、PDF 批注高亮（Annotations）以及附件（Attachments）的协同机制。

---

## 1. 核心原则与安全门禁

1. **安全第一，默认只读（Dry-Run）**：
   - 任何涉及 Zotero 数据库（`zotero.sqlite`）或本地 API 的写操作，**默认均为 `--dry-run` 预览对比模式**；
   - 仅当显式传入 `--apply` 时，才执行真正的写入，杜绝任何自动化静默覆写破坏用户个人核心文献资产。
2. **单一数据真实源（Single Source of Truth）对齐**：
   - **元数据**：以 PDF 首页印刷信息、出版商官方 DOI 及 Crossref 经核对后的字段为准，校正 Zotero 中因抓取不全导致的乱码或截断；
   - **阅读笔记**：以本地 Markdown 中用户与 Agent 沉淀的深度笔记（`## 阅读笔记`）为准，增量推送至 Zotero 形成关联子笔记（Child Note）；
   - **批注高亮**：以 Zotero 原生 PDF Reader 中的用户划线与批注为准，增量拉取至 Markdown 知识库中生成引用卡片。

---

## 2. 字段映射表 (Metadata Field Mapping)

| Markdown Frontmatter (YAML) | Zotero Item Field (API / Data) | 说明 |
| :--- | :--- | :--- |
| `title` | `title` | 论文完整标题（校正去除多余换行与首尾空白） |
| `authors` (`list[str]`) | `creators` (`list[dict]`) | 完整作者列表（不截断、保留通讯作者与组织作者） |
| `date` | `date` | 出版日期（保留真实精度，如 `2026-04`） |
| `publication_title` | `publicationTitle` | 期刊名称 / 会议论文集名 |
| `volume` | `volume` | 卷号 |
| `issue` | `issue` | 期号 |
| `pages` | `pages` | 起止页码（如 `100-115`） |
| `doi` | `DOI` | 规范化 DOI（去除 `https://doi.org/` 前缀） |
| `abstract` | `abstractNote` | 原文完整摘要（纯文本段落） |
| `url` | `url` | 官方在线出版物网址 |
| `language` | `language` | 语种（如 `zh-CN`, `en`） |
| `keywords` (`list[str]`) | `tags` (`list[dict]`) | 关键词列表 |
| `zotero_item_uri` | `links.self` / `key` | 唯一的 Zotero 条目跳转与定位锚点 |
| `has_supplement` | `relations` / Frontmatter | 关联的补充材料（SI）Obsidian 双链 |
| `supplement_to` | `relations` / Frontmatter | 关联的主文（Main Paper）Obsidian 双链 |

---

## 3. CLI 命令行使用方式 (`scripts/zotero_sync.py`)

### 3.1 比较元数据差异（只读审查）
```bash
python scripts/zotero_sync.py "./科研知识库/第一作者 等 - 2026 - 论文标题/第一作者 等 - 2026 - 论文标题.md"
```
- 输出本地 frontmatter 与 Zotero 对应条目的差异字段、附件总数及笔记状态；不修改任何文件。

### 3.2 确认回写元数据与推送阅读笔记
```bash
python scripts/zotero_sync.py "./科研知识库/第一作者 等 - 2026 - 论文标题/第一作者 等 - 2026 - 论文标题.md" \
    --apply --report sync-report.json
```
- 将校正后的元数据通过本地 API 回写至 Zotero 条目；
- 将正文中 `## 阅读笔记` 段落自动转换为富文本 HTML 并作为 child note 写入 Zotero（若已存在则去重，不重复新增）。

### 3.3 双向拉取 Zotero PDF 批注至 Markdown
```bash
python scripts/zotero_sync.py "./科研知识库/第一作者 等 - 2026 - 论文标题/第一作者 等 - 2026 - 论文标题.md" \
    --pull-annotations --apply
```
- 扫描对应 PDF 附件下所有划线高亮与用户评论，在 Markdown 正文末尾生成 `## Zotero 批注与高亮` 引用卡片（含对应页码）。

---

## 4. Agent 交互式执行模式（Zotero MCP 工具联动）

在支持 Zotero 官方插件的交互会话中，Agent 可按需组合调用内置 MCP 工具执行细粒度同步：

1. **元数据更新**：调用 `mcp__plugin_zotero_zotero__update_item`，传入 `key` 与待更新的 `fields`；
2. **笔记挂载**：调用 `mcp__plugin_zotero_zotero__add_note`，传入 `parentKey` 与 Markdown 内容；
3. **补充附件关联**：调用 `mcp__plugin_zotero_zotero__attach_pdf`，将知识库生成的 SI PDF 或重组高清版 PDF 挂载至对应条目；
4. **批注同步**：调用 `mcp__plugin_zotero_zotero__save_highlights` 或 `search_annotations` 检索并保存会话内的高亮批注。
