# 科研文档：期刊 PDF 与 Zotero 来源

本入口复用教材装配、回源修复和全量校验。当前真实验证范围为英文、原生文字、双栏期刊论文；扫描论文、群组库、学位论文、标准、专利等待各自样本验证。主文引用的补充文件不等于已取得补充附件，按实际输入范围验收。

## 1. 输入与预检

独立 PDF 可直接处理。Zotero 是可选来源，不是元数据前置条件。先检查页数、文字层、内嵌图片和版面；可靠原生文字用于回源，不能只凭字符数量认定正确。保留原始 PDF 和 SHA-256。

读取 Zotero 时先用 Zotero 插件确认文献与附件。插件未暴露文件导出工具时，可使用只读本地 API 导出适配器：

```bash
python scripts/zotero_export.py --out ./research-work
```

适配器仅使用本地 API 选定的附件；API 的 `/items/<附件key>/file` 如返回 `file:` 重定向，仅复制该精确文件。不得遍历 storage 或访问 sqlite。`library.json` 保留条目、附件、哈希和状态，`ids.tsv` 提交不同文件；相同字节记录别名，不删除 Zotero 重复条目。无附件及失败条目单独报告。Zotero 需开启本地 API。

## 2. OCR 和单篇装配

MinerU 上传完整 PDF，元数据公开查询只发送 DOI 等检索信息。凭证仅从环境变量读取。英文样本显式指定语言；已有识别结果用 `resume`，不重复提交。

```bash
python scripts/mineru_ocr.py submit --out-root ./research-ocr \
    --id-file ./research-work/ids.tsv --language en

python scripts/build_research_document.py \
    --pdf ./research-work/sources/条目key-附件key.pdf \
    --raw ./research-ocr --metadata ./research-work/library.json --id 条目key \
    --out ./科研知识库/文献标识 --state-dir ./research-state/文献标识
```

独立 PDF 省略 `--metadata`；也可提供经原文核对的规范字段 JSON。`--raw` 接受单篇结果目录、包含各文献的 raw 根目录或含 raw 的 OCR 工作目录，多篇输入用 `--id` 精确选择。入口不会自行上传 OCR。

每篇输出 `原文.md`、`00-目录.md`、`images/`；批量调用由外层逐篇隔离失败，并在根目录生成导航。构建状态始终位于阅读目录之外。目录用稳定文献标识消歧，不用标题判断重复。缓存核对 PDF、OCR、修复、元数据和代码哈希，仍会重跑自动门禁；人工修改及未登记文件会阻止覆盖。

论文默认 `--reading-order source`，保留每页经核对的 OCR 阅读顺序，避免按高度交错双栏。该选项不证明 OCR 原始顺序一定正确；须检查栏尾与下一栏开头、跨页续文、通栏图和图题。教材原入口默认几何排序保持不变。

## 3. frontmatter 与来源证据

可用字段：`title`、`document_type`、`authors`、`date`、`publication_title`、`volume`、`issue`、`pages`、`doi`、`language`、`abstract`、`zotero_item_uri`，及来源已提供的 `url`、`keywords`。缺失字段省略。

```yaml
---
title: "原文标题"
document_type: journal_article
authors:
  - "完整作者姓名"
date: "2025-03"
publication_title: "期刊名称"
doi: "10.1234/example"
abstract: "原文摘要，不能替换成模型概述。"
---
```

不输出 `zotero_item_key`、`zotero_pdf_uri` 或附件 key。只在原 PDF 与来源关联可靠时输出条目 URI；无关联不伪造。作者保持原顺序，不用 et al. 截断；机构作者使用完整名称。日期不以 PDF 创建时间代替，在线发表日期和卷期日期不能混为一谈。

脚本读取 PDF 属性、XMP和首页出版线索，保守输出被原文印证的字段；不会自动猜出完整作者和摘要。字段不足时需回源填入规范 JSON，查看外部 `research/provenance.json` 的缺失和冲突。

`--crossref` 显式开启公开 DOI 查询，无密钥要求，结果有缓存；只有已确认 DOI 和严格归一化标题均匹配才补字段，不替换冲突字段。仅参考文献出现的 DOI 不能作为本文 DOI，匹配记录也不能证明版本相同。其他外部服务并非当前入口的必要配置。

## 4. 图表、公式与回源修复

按原 PDF 建立独立清单：正文与扩展图、表、独立公式及式号、图中公式、附录和末尾章节。清单不能取自 OCR 自身。逐个核对面板、图例、裁框、图题、表注和数值。PDF 矢量图可能由许多对象组成，单张内嵌位图不是整幅图；必要时按完整区域高清裁取。

```bash
python scripts/audit_figures.py ./research-ocr/raw/条目key \
    --out-repairs ./repairs-draft.json --out-report ./figure-audit.json

python scripts/build_research_document.py \
    --pdf ./source.pdf --raw ./research-ocr/raw/条目key \
    --metadata ./metadata.json --id 条目key \
    --out ./科研知识库/文献标识 --state-dir ./research-state/文献标识 \
    --repairs ./repairs.json
```

审查工具只生成候选草稿，尤其不能把一个图题内引用的其他图号当成合图依据。原页确认后使用既有 `update_image`、`add_image`、`skip` 和文本修复契约，旧值或定位不匹配即停止。格式见 [mineru-api.md](mineru-api.md)。不同 PDF 不得共用未分来源的修复列表。

复杂表格同时保留可检索表达和原版裁图；OCR 表体本身错乱时先保留正确原图，明确记录检索层局限。化学结构和图中公式保留高清完整原图；独立数学公式保留 LaTeX、式号，并逐一核对原文。原文疑似笔误也不能擅自改写。

## 5. 门禁与交付

入口自动执行结构/图片检查、全量公式抽取、KaTeX 编译，将命令、退出码、报告存入外部构建状态。零公式仅表示抽取结果为空，仍需来源清单确认；不造公式绕过检查。YAML 使用序列化器生成并在发布前纳入所有权哈希，预览与数学扫描不将元数据当正文。

之后必须完成：

1. 依据独立来源清单核对全部图表、独立公式和式号，以及参考文献、脚注、Methods、数据/代码可用性和附录；重点检查行内上下标、希腊字母、单位及续文。
2. 启动本地预览，逐篇检查 `dataset.ready === "true"`、`.math-error` 为 0、图片 `naturalWidth > 0`；检查真实页面的图题和公式显示。
3. 重跑验证缓存和人工修改保护。公共代码改动运行教材、课件、数学、发布和预览回归测试。

```bash
python scripts/preview_server.py ./科研知识库 --port 8766 \
    --katex-dir scripts/node_modules/katex/dist
python -m pytest tests -q
```

自动门禁通过时，报告仍标记 `draft_pending_source_and_browser_review`。只有来源对账与实际渲染也完成，才在单独验收记录中标为通过；存在已知内容遗漏、错图或科学含义错误时不得宣称完整。交付阅读目录和验收报告链接，保留最新测试产物；论文 PDF、个人库元数据、OCR 结果及批注不提交到技能仓库。
