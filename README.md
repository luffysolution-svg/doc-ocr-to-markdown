# doc-ocr-to-markdown

把扫描教材、PDF图书、理工科课件、学术论文、讲义等文档整理成带高清图片、表格和可渲染 LaTeX 公式的 Markdown 知识库 —— 一个给 AI 编码助手用的工业级 [Agent Skill](https://docs.anthropic.com/en/docs/claude-code/skills)。

不仅是简单的文本提取，它专注解决高难度理工科学术文档的真正痛点：**公式 100% 还原为可渲染的 KaTeX/LaTeX、插图高清去重、表格内二维码完整保留、OCR 错字双轨纠错、500+ 页超长教材智能分片组装**。

## 解决什么问题

用普通 OCR 工具直接转换理工科教材或带公式的课件，通常会遇到：

- **公式结构崩解**：散落为孤立字符，上下标、分式和希腊字母错乱；
- **排版倒置混乱**：课件逻辑盲目把整页图片置顶，长教材阅读顺序颠倒，页顶首句被误提为二级标题；
- **关键资源丢失**：教材章节末尾的拓展学习二维码被纯文本转换吞掉；
- **同音字/形近字错漏**："轫致辐射"→"韧致辐射"、"ν"→"v"、"N₀"→"No"；
- **模型推理泄漏**：多模态 OCR 模型的 "The Ground Truth image displays..." 等提示词进入正文；
- **重复图片堆积**：同一幅原理图在多张幻灯片被重复截取数十次。

本 Skill 提供标准工业流水线与严格质量门禁（KaTeX 全量 100% 编译通过），每条规则均来自 500+ 页真实经典教材的实战踩坑。

## 包含什么

```
SKILL.md                        技能定义、执行准则与反模式清单
references/
  mineru-api.md                 MinerU API 契约、归一化坐标与 DoH 连接策略
  math-normalization.md         12 条科学公式归一化规则，附真实前后对照
  book-workflow.md              超长教材与学术专著分片识别、重组指引
scripts/
  mineru_ocr.py                 大文档切分 (split) / 批量提交 / 轮询 / 容错下载
  build_markdown.py             Markdown 组装核心（支持 slides 课件与 book 教材双模式）
  normalize_math.py             LaTeX 公式归一化引擎（命令分离、式号修复、括号配平）
  extract_formulas.py           从 Markdown 抽取全部公式
  validate_output.py            交付前结构校验与模型提示词泄漏检测
  validate_math.cjs             KaTeX 全量公式编译严格校验（100% 门禁）
  preview_server.py             本地离线 Markdown + KaTeX 预览沙箱
  _selftest.py                  公式归一化与边缘用例自动化测试
examples/
  RESULTS.md                    实战案例报告（694页材料课件 + 556页物理化学经典教材）
```

## 核心流程

1. **先判断，再动手** —— 检查原生文字与内嵌原图，确定是幻灯片 (`--mode slides`) 还是长篇教材 (`--mode book`)。
2. **切片与 OCR** —— 超长文档调用 `mineru_ocr.py split` 自动按页切块并批量提交，单文档直接提交。
3. **文字交叉校对** —— 源文件有文字层时（PPTX/DOCX），以原生文字为基准自动纠正 OCR 错字。
4. **图表高清提取** —— 源文件内嵌原图优先，扫描件从 PDF 2.5–3× 裁切，哈希去重，提取表格单元格内嵌二维码。
5. **科学公式归一化** —— 命令粘连分离、化学下标隔离、双重式号去重、跨界括号修复，二维环状结构原图保真。
6. **结构组织与重组** —— 教材模式保持自然图文混排，自动平滑续接跨页段落，按章节定义生成统一知识库。
7. **全量编译与渲染验证** —— KaTeX 逐一编译全部公式（要求 100% 通过），本地预览沙箱确认无错。

## 真实实战规模

| 案例 | 类型与规模 | 公式数量 | 图片/图表 | 质量指标 |
| :--- | :--- | :--- | :--- | :--- |
| **案例一：材料分析测试方法** | 10 章 694 页 PPT 课件 | 2,118 个 | 199 张 | 公式 100% 通过，纠错 7 处 |
| **案例二：物理化学（第六版）下册** | 556 页全扫描经典高校教材 | **7,523 个** | 332 张 (含28个二维码) | **KaTeX 7,523/7,523 全部通过（0 失败）**，0 坏链 |

*详见 [examples/RESULTS.md](examples/RESULTS.md)。*

## 安装与依赖

```bash
# 克隆至技能目录（以用户级为例）
git clone https://github.com/luffysolution-svg/doc-ocr-to-markdown ~/.agents/skills/doc-ocr-to-markdown

# Python 依赖
pip install requests pymupdf pillow python-pptx markdown markdown-it-py beautifulsoup4

# Node.js 依赖（用于 KaTeX 100% 公式全量编译与本地预览）
npm install katex
```

环境变量：MinerU 云端识别需要设置 `MINERU_API_TOKEN`（获取方式见 [mineru.net](https://mineru.net)）。

## 常用命令

```bash
# 1. 超长教材智能切片（如 556 页 PDF 拆为 96 页切片）
python scripts/mineru_ocr.py split path/to/textbook.pdf --out-root ./ocr-work --chunk-size 96

# 2. 批量提交 MinerU 云端 API
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv

# 3. 教材模式组装（自然图文流混排、表内二维码提取、双轨扫描图备查）
python scripts/build_markdown.py \
    --raw ./ocr-work/raw \
    --out ./知识库 \
    --mode book \
    --manifest ./ocr-work/manifest.json \
    --chapters ./chapters.json \
    --pdf path/to/textbook.pdf \
    --keep-table-images

# 4. 课件模式组装（PPTX 原生文字校对与原图提取）
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode slides --source-dir ./课件 --pdf-dir ./课件PDF

# 5. 交付前结构与链接校验
python scripts/validate_output.py ./知识库 --images-dir images

# 6. KaTeX 100% 全量公式编译门禁（0 容错）
python scripts/extract_formulas.py ./知识库 --out formulas.json
node scripts/validate_math.cjs formulas.json

# 7. 本地离线预览验证沙箱
python scripts/preview_server.py ./知识库 --port 8766
```

## 输出规范

产出的知识库完全为人类长效阅读与智能体（RAG/Agent）索引设计：
- **纯净连续**：剔除页眉页脚、页码、模型推理泄漏、折叠块与多余编辑痕迹；
- **全相对路径**：图片采用 `![](images/chXX/...)` 标准格式，知识库目录整体可移植；
- **双模态保真**：公式输出标准 LaTeX，复杂科学表格与复杂化学环状键保留原扫描图裁片双轨备查；
- **入口导航**：根目录附带包含各章各节锚点的 `00-目录.md`。

## License

MIT

