---
name: doc-ocr-to-markdown
description: 把扫描教材、PDF图书、理工科课件、学术论文、讲义等整理成带高清图片、表格和可渲染LaTeX公式的Markdown知识库。当用户提出"整理成markdown"、"转成知识库"、"OCR识别教材/课件"、"提取公式"、"做成Obsidian笔记"、"扫描件转文字"、"教材分章节转md"，或需要将大批量学术文档转为结构化知识库供人类精读或Agent/RAG检索时使用。覆盖大文档智能分片、MinerU云端OCR、源文件文字交叉纠错、高清原图裁切与去重、科学公式归一化、单元格内嵌二维码提取、KaTeX全量100%编译校验与浏览器实渲闭环。
---

# 文档转 Markdown 知识库 (doc-ocr-to-markdown)

将整批文档（尤其包含复杂公式、图表、跨页表格的理工科教材、学术论文或扫描课件）整理成干净、自包含、长期可维护的 Markdown 知识库。产出同时面向人类专注阅读与智能体（Agent/RAG）精准检索。

---

## 1. 先判断，再动手（Preflight）

**不要一上来就盲目跑 OCR。** 执行前完成四项预检，可省去大量无效等待与返工：

1. **源文件有无原生文字层？**
   - 用 PyMuPDF 的 `page.get_text()` 或 `python-pptx` 检查。
   - 有文字时先区分原生文本与旧 OCR 层；可靠原生文字可作校对基准，旧 OCR 层仍须核对，不能仅凭字符数视为真值；
   - 若 100% 纯扫描件（字符数为 0），则完全依赖 OCR，需在交付报告中说明。
2. **源文件有无内嵌原图？**
   - PPTX 优先提取 `shape.image.blob`，PDF 检查 `page.get_images()`。
   - 内嵌原图分辨率远高于页面截图；只有整页扫描教材才从 PDF 按归一化坐标裁切。
3. **识别文档是“课件幻灯片”还是“长篇教材/专著”？**
   - **幻灯片课件 (`--mode slides`)**：一页一个独立主题，图文视觉集中，优先图置顶与 PPT 原生文字对齐。
   - **长篇教材/论文 (`--mode book`)**：连续行文，必须采用**自然流混排**（严禁将图强制提前）、**严格章节判定**（严禁将页顶首句误升 H2）、**保守保留段落边界**。
4. **安全交付目标路径：**
   - 默认输出到**全新的独立目录**（如 `<源目录>/<书名>-知识库`），严禁破坏覆盖原始文件。
   - 脚本只更新未被人工修改的生成文件，拒绝清空目录。默认只交付导航、正文 Markdown 与图片；构建状态及质检报告留在交付目录之外。

---

## 2. 核心执行工作流

### 步骤 1：智能切分与 OCR 识别

超长教材（>200 页或 >100 MB）需先切片，避免触发 MinerU 云端单文件限制：

```bash
# 1. 超长 PDF 自动切片（以 96 页为例）并生成 manifest.json 与 ids.tsv
python scripts/mineru_ocr.py split <源PDF路径> --out-root ./ocr-work --chunk-size 96

# 2. 批量提交 MinerU 云端 API（模型 vlm，开启公式与表格）
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv
```

- **坐标系统**：MinerU 返回的 `bbox` 永远是 **0–1000 归一化坐标**。换算真实像素/点阵必须乘以页面实际宽高 `[x0/1000*W, y0/1000*H, x1/1000*W, y1/1000*H]`。
- **网络回退**：若遇 `cdn-mineru.openxlab.org.cn` TLS 握手 EOF，脚本已内置公共 DoH 获取真实 IP 并使用 `curl --resolve` 绕过 fake-IP 劫持（详见 `references/mineru-api.md`）。

### 步骤 2：用原生文字校正 OCR 错字（PPTX/有文字层 PDF）

OCR 会出现形近字、同音字或英文字母误识（如“轫致辐射”误为“韧致辐射”、“ν”误为“v”）。
- 当前装配脚本支持 PPTX 按文本块位置重叠度（`overlap > 0.5`）与相似度（`sim >= 0.9`）校对；有文字层 PDF 需另外核对，不能假设已自动替换。
- 差异较大者记录在 `build-report.json` 的 `review` 字段中留待复核。

### 步骤 3：高清图表提取与资产保真

提取优先级：
1. **源文件内嵌原生图片**（最清晰）；
2. **按 OCR 坐标从原 PDF 以 2.5–3 倍缩放高清裁切**（用于扫描件）；
3. **表格内嵌图片提取**：遇到包含 `<img src="...">` 的表格（如各章拓展学习二维码），自动转存为图片并替换为 Markdown 相对引用，避免丢失；
4. **特殊复杂结构保真**：对三角形化学键等无法用标准 LaTeX 表达的二维空间图，以及深层合并的复杂科学实验表，开启 `--keep-table-images` 保留高清原版裁图双轨备查。

- **去重**：按 SHA-256 / SHA-1 内容哈希去重，杜绝挂图讲解产生的重复资产。
- **图内标签过滤**：文字块完全落在图片区域内（重叠度 > 0.6）且小于 12 字符的标签，丢弃不重复为正文。

### 步骤 4：科学公式严谨归一化

所有公式转为 Obsidian 兼容的 LaTeX（行内 `$...$`，独立行 `$$...$$`）。核心规则（见 `references/math-normalization.md`）：
- 中文放进 `\text{}`，化学式放进 `\mathrm{}`；
- 保留文本分组语义；有歧义的嵌套分组和标点回源确认后再修；
- 修复粘连命令：`\vertHg` → `\vert Hg`，`\cdotkg` → `\cdot kg`；
- 化学下标处于文本模式时，核对后用数学模式保留其下标，例如 `\text{NH_4Cl}` → `\mathrm{NH_4Cl}`；
- 多重式号、未知命令、化学结构及跨正文括号须回看原页确认，通过 `--repairs` 显式应用，不自动猜测编号或物理含义；
- 编译通过只证明语法可接受；二维结构失真时保留原版裁图。

### 步骤 5：组织正文结构与知识库装配

```bash
# 课件模式（PPTX/幻灯片）：
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode slides --source-dir ./课件 --pdf-dir ./课件PDF

# 教材/专著模式（多批块长文档）：
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode book --manifest ./ocr-work/manifest.json --chapters ./chapters.json \
    --pdf <整本PDF路径> --keep-table-images
```

- **自然流混排**：教材模式下正文、公式、表格、图表依照出现顺序连贯流转。
- **段落边界**：保守保留 OCR 段落与跨页边界，避免误拼题目、公式及新段落。
- **标题与保真**：区分小节和习题；拆分章名与传入标题一致时去重；实质脚注和整页图保留，不按 OCR 标签或面积静默丢弃。
- **干净交付**：默认只生成导航、正文 Markdown 和图片；构建状态位于输出旁的 `.<目录名>.build/`，可用 `--state-dir` 指定外部工作目录。
- **可选检索**：仅当用户明确要求后续 Agent 检索、RAG 时添加 `--agent-index`，生成 `agent/` 分块与原页映射。
- **回源修复**：`--repairs repairs.json` 接受 `[{"page":1,"bbox":[100,200,800,300],"field":"text","old":"原识别","new":"核对后的内容","reason":"原页核对依据"}]`；可附 `set_type:"body_text"` 强制正文或 `set_type:"page_footnote"` 保留脚注，`text` 则仍参与标题判定。补原图使用 `action:"add_image"`、裁切 `bbox` 和 `after:{"bbox":[...],"text":"原始锚点"}`。旧值或定位不匹配立即停止，审计留在外部构建状态。
- **过滤模型泄漏**：自动清除 `The Ground Truth...` 等模型推理遗留文本。

### 步骤 6：交付前全量校验（核心质量门禁）

**交付前必须跑自动化校验，公式必须 100% 编译通过，绝不抽查：**

```bash
# 1. 结构与资源链接完整性检查
python scripts/validate_output.py ./知识库 --images-dir images

# 2. 抽取全部公式并执行本地 KaTeX 全量编译（要求 0 失败）
python scripts/extract_formulas.py ./知识库 --out formulas.json
node scripts/validate_math.cjs formulas.json
```

### 步骤 7：浏览器实际渲染沙箱验收

启动本地离线渲染服务，通过自动化或人工查看确认图片正常、公式报错为 0：

```bash
python scripts/preview_server.py ./知识库 --port 8766 --katex-dir scripts/node_modules/katex/dist
```
- 打开 `http://127.0.0.1:8766/` 验证 `document.documentElement.dataset.ready === "true"` 且 `.math-error` 数量为 0。

---

## 3. 自由度与边界 (Degrees of Freedom)

| 操作维度 | 控制级别 | 规范边界 |
| :--- | :--- | :--- |
| **归一化与语法** | **严格收紧 (Zero Tolerance)** | 坐标转换公式、KaTeX 编译通过率必须 **100%**；严禁出现未闭合定界符、绝对路径或损坏图片链接。 |
| **文件完整性** | **严格收紧** | 必须包含 `00-目录.md` 导航入口；图片全量可解码；严禁破坏性清空输出目录。 |
| **排版与段落合并** | **自适应灵活 (Moderate)** | 允许根据书籍逻辑组织前言、目录、章节、附录与索引；平滑处理断行；合理合并子图。 |
| **复杂化学/特殊结构** | **工程务实 (Pragmatic)** | 遇到二维环状分子或复杂推导，若 LaTeX 表达失真，优先采用高保真原图局域裁切，确保科学真实性。 |

---

## 4. 严禁的反模式 (Anti-Patterns)

1. ❌ **一上来就 OCR**：忽略已有矢量文字和高清原图，把简单提取变成低效 OCR。
2. ❌ **破坏性清空目录**：在未获授权或调试子章节时调用 `rmtree`，导致前序成果丢失。
3. ❌ **课件逻辑套用教材**：把一整页的所有插图强制置顶，打乱长篇著作阅读逻辑；把页顶首句误判为 H2 标题。
4. ❌ **公式抽查交付**：认为抽查几页没报错即合格；数千个公式中哪怕 1 个语法错都会导致 Obsidian/前端整块崩溃。
5. ❌ **丢弃表格内嵌图片**：提取表格时使用纯文本截断，导致关键二维码、实验图标全部遗失。
6. ❌ **残留模型提示词**：把 OCR 模型的“The Ground Truth image displays...”等幻觉说明写入知识库。

---

## 5. 参考文档与工具链

- `references/mineru-api.md` — MinerU 官方契约、数据结构、归一化坐标与 DoH 连接策略
- `references/math-normalization.md` — 12 条科学公式归一化规则与真实例子
- `references/book-workflow.md` — 500+ 页学术专著与教材的切片、全局映射与重组指南
- `examples/RESULTS.md` — 包含《材料分析测试方法》PPT课件与《物理化学（第六版）下册》556页超长教材全流程实战成果
