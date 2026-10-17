# wusen-doc2md

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![KaTeX 100%](https://img.shields.io/badge/KaTeX-100%25%20Strict-brightgreen.svg)](https://katex.org/)
[![Tests](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-Claude%20%7C%20ZCode%20%7C%20Codex-orange.svg)](https://docs.anthropic.com/en/docs/claude-code/skills)

把扫描教材、PDF 图书、理工科课件、中英文学术论文、硕博学位论文等文档整理成带高清图片、表格和可渲染 LaTeX 公式的 Markdown 知识库 —— 专为 AI 编码助手打造的工业级 [Agent Skill](https://docs.anthropic.com/en/docs/claude-code/skills)。

不只是文本提取。它专注解决理工科学术文档的真正痛点：**公式全量 KaTeX 编译、图表回源保真、多面板错切修复、表格内二维码保留、OCR 错字校对、超长学位论文与教材分片组装、孤儿切图安全清理，以及期刊 PDF 与 Zotero 元数据 frontmatter**。编译通过不等于内容完整，图表与科学符号仍须逐项核对原文，并经真实客户端渲染终验。

---

## 解决什么问题

用普通 OCR 工具直接转换理工科教材或学术论文，通常会遇到：

- **公式结构崩解**：散落为孤立字符，上下标、分式和希腊字母错乱；
- **排版倒置混乱**：课件逻辑盲目把整页图片置顶，长教材阅读顺序颠倒，页顶首句被误提为二级标题；
- **客户端渲染劫持**：正文中方括号注释（如 `[通常为 $\mathcal{N}(0, I)$ ]`）被 Obsidian 等编辑器误判为超链接，导致公式源码裸露为蓝色；
- **标题公式破坏性降级**：标题中的分子式（如 `$\mathrm{CO}_2$`）被粗暴剔除定界符，留下裸下划线 `CO_2` 伪代码并引发 Markdown 斜体冲突；
- **资产碎片堆积**：微调裁切框后，旧切图文件残留堆积，目录体积虚高；
- **关键资源丢失**：教材章节末尾的拓展学习二维码、复杂数据表被纯文本转换吞掉；
- **同音字/形近字错漏**："轫致辐射"→"韧致辐射"、"ν"→"v"、"N₀"→"No"；
- **模型推理泄漏**：多模态 OCR 模型的 "The Ground Truth image displays..." 等提示词进入正文。

本 Skill 提供标准流水线与严格质量门禁（KaTeX 全量编译通过 + 真实客户端渲染终验），规则来自真实教材与学术文献的实战踩坑。

---

## 三轨架构分流设计

针对不同文档形态，流水线采用三轨专业化分流架构，严禁单一模板生搬硬套：

| 维度 | 1. 扫描教材与长篇专著 (`--mode book`) | 2. 课件幻灯片与讲义 (`--mode slides`) | 3. 学术科研文献与学位论文 (Research) |
| :--- | :--- | :--- | :--- |
| **组织架构** | 多章节分流（`01-第一章.md`、`00-目录.md`） | 按讲次/课时组织 | 单篇独立文献一体化（主笔记 + `00-目录.md`） |
| **排版流向** | **几何自然流**混排（$\Delta y = 25$），严禁插图置顶 | **视觉置顶**排版（图置顶，承接文字与要点） | 默认保留经核对的物理 OCR 双栏阅读流 |
| **标题与习题** | 严格章节判定，精准区分小节标题与习题题干 | 保持幻灯片页面核心主题 | 统一学术标准命名 `{{firstCreator}} - {{year}} - {{title}}.md` |
| **图表处理** | 原 PDF 至少 3× 高清重裁，多面板 union 合并 | 优先提取 PPTX 内嵌原图，清洗图内小标签 | 高置信度多面板缝合，支持 SI 补充材料 |
| **元数据** | 保留实质性学术脚注，清洗运行页眉页脚 | 对齐原生文本层，按相似度校正错字 | 提取完整 YAML frontmatter（DOI、作者、摘要、Zotero URI） |

---

## 包含什么

```text
SKILL.md                        技能定义、执行准则与 13 项反模式清单
references/
  mineru-api.md                 MinerU API 契约、归一化坐标与 DoH 连接策略
  math-normalization.md         16 条科学公式归一化规则，附真实前后对照
  book-workflow.md              超长教材与学术专著分片识别、重组指引
  research-documents.md         学术论文与学位论文标准命名、frontmatter 与双栏规范
scripts/
  mineru_ocr.py                 大文档切分 (split) / 批量提交 / 轮询 / 容错下载
  build_markdown.py             Markdown 组装核心（支持 slides / book / 孤儿切图清理）
  build_research_document.py    科研文献专用一体化装配入口
  normalize_math.py             LaTeX 公式归一化引擎（命令分离、方括号防劫持、Unicode退避）
  extract_formulas.py           从 Markdown 抽取全部公式
  validate_output.py            交付前结构校验、宏命令残留与模型提示词泄漏检测
  validate_math.cjs             KaTeX 全量公式编译严格校验（100% 门禁）
  preview_server.py             本地离线 Markdown + KaTeX 预览沙箱
examples/
  RESULTS.md                    教材与课件实战报告（694页课件 + 556页物理化学经典教材）
  RESEARCH-VALIDATION.md        科研文献全量实测报告（611页中文核心/硕博论文 + 71页英文期刊）
```

---

## 核心流程

```text
[1. 预检 Preflight] ── 原生文字层与内嵌原图排查，形态分流（教材 / 课件 / 科研文献）
        │
[2. 切片与 OCR]    ── 超长 PDF 自动切块（96页/块）批量提交 MinerU 云端 API
        │
[3. 文字交叉校对]  ── 利用原生文字层/PPT文本对齐校对 OCR 形近字与拼写
        │
[4. 图表回源保真]  ── 提取内嵌原图 / PDF 3× 高清重裁，多面板外接矩形合并，保留表内二维码
        │
[5. 公式规范归一]  ── 16 条规则标准化（中文进 \text、不等号转义、方括号防劫持、标题公式保全）
        │
[6. 知识库组装]    ── 自然混排或视觉置顶，生成 00-目录.md 导航，可选 --prune-orphans 清理孤儿切图
        │
[7. 双重终验门禁]  ── KaTeX 全量 100% 编译通过 + Obsidian 真实客户端视图核验
```

---

## 真实实战规模全景

全套工具链均在真实大部头理工科文档中经过极限压测：

| 案例库 | 文档类型与规模 | 提取公式数 | 图片/图表数 | 质量指标与技术突破 |
| :--- | :--- | :---: | :---: | :--- |
| **案例一：材料分析测试方法** | 10 章 694 页 PPT 课件 | 2,118 个 | 199 张 | 公式 100% 通过，原生文字纠错 7 处，图片去重 |
| **案例二：物理化学（第六版）下册** | 556 页全扫描经典高校教材 | **7,523 个** | 332 张 | **KaTeX 7,523/7,523 全部通过（0 失败）**，提取 28 个拓展二维码 |
| **案例三：中文科研文献与硕博学位论文全集** | 5 篇共 611 页（含 240 页博士论文） | **5,797 个** | 398 张 | 解决 Obsidian 方括号链接劫持、保全标题化学式、**安全清理 89 个历史旧图** |
| **案例四：英文前沿期刊文献集** | 7 篇共 87 页顶级英文期刊 (Nature/Matter 等) | 876 个 | 114 张 | 双栏阅读流还原，突破 Windows 路径长度瓶颈，YAML frontmatter 全量注入 |
| **总计** | **近 2,000 页真实学术文档** | **16,314 个** | **1,043 张** | **全量编译通过率 100%，结构化零损坏交付** |

*详见 [examples/RESULTS.md](examples/RESULTS.md) 与 [examples/RESEARCH-VALIDATION.md](examples/RESEARCH-VALIDATION.md)。*

---

## 安装与依赖

### 安装技能

```bash
npx skills add luffysolution-svg/wusen-skills --skill wusen-doc2md -g
```

安装后进入包含本技能 `SKILL.md` 的目录。共享目录通常是 `~/.agents/skills/wusen-doc2md`，实际路径以安装器输出为准。下面的命令均从该技能目录运行。

### 配置运行环境

建议使用独立 Python 虚拟环境，避免改动系统 Python。要求 Python 3.10+ 和 Node.js 22.20+。

```bash
python -m venv .venv
```

激活环境：macOS/Linux 使用 `source .venv/bin/activate`；Windows PowerShell 使用 `.venv\Scripts\Activate.ps1`。也可让 AI 助手直接调用虚拟环境内的 Python，无须激活。

```bash
python -m pip install -r requirements.txt
npm --prefix scripts install --ignore-scripts --no-audit --no-fund
python scripts/doctor.py
```

`doctor.py` 只检查当前 Python、依赖、Node.js、KaTeX 和凭证配置状态，不安装依赖、不发起网络请求，也不输出凭证值。可加 `--json` 获取结构化结果。

云端 OCR 另外需要设置 `MINERU_API_TOKEN`（从 [mineru.net](https://mineru.net) 获取），然后检查：

```bash
python scripts/doctor.py --require-ocr
```

未设置凭证时，普通检查会提示云端 OCR 不可用；已满足依赖的本地装配、校验和预览仍可使用。`--require-ocr` 会将缺少凭证视为失败。退出码 `0` 表示所需检查通过，`1` 表示运行环境不完整。

### 验证安装

```bash
python -m unittest discover -s tests -v
```

Install with the shared `skills` CLI, enter the installed skill directory, create a Python virtual environment, install `requirements.txt` and the Node dependencies under `scripts/`, then run `python scripts/doctor.py`. Cloud OCR additionally requires `MINERU_API_TOKEN`.

---

## 常用命令速查

```bash
# 1. 超长文档切片（以 96 页切片为例）
python scripts/mineru_ocr.py split path/to/document.pdf --out-root ./ocr-work --chunk-size 96

# 2. 批量提交 MinerU 云端 API
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv

# 3. 教材模式组装（自然流混排、表内二维码提取、双轨扫描图备查、清理旧图）
python scripts/build_markdown.py \
    --raw ./ocr-work/raw \
    --out ./知识库 \
    --mode book \
    --manifest ./ocr-work/manifest.json \
    --chapters ./chapters.json \
    --pdf path/to/document.pdf \
    --keep-table-images \
    --prune-orphans

# 4. 课件模式组装（PPTX 原生文字校对与原图提取）
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode slides --source-dir ./课件 --pdf-dir ./课件PDF

# 5. 科研文献一体化组装（Zotero 学术标准命名、frontmatter 注入、双栏流）
python scripts/build_research_document.py --pdf ./source.pdf --raw ./ocr-work/raw/article \
    --metadata ./metadata.json --out "./科研知识库/第一作者 等 - 年份 - 论文标题"

# 6. 交付前结构与链接完整性校验
python scripts/validate_output.py ./知识库 --images-dir images

# 7. KaTeX 100% 全量公式编译门禁（0 容错）
python scripts/extract_formulas.py ./知识库 --out formulas.json
node scripts/validate_math.cjs formulas.json

# 8. 本地离线预览验证沙箱
python scripts/preview_server.py ./知识库 --port 8766 --katex-dir scripts/node_modules/katex/dist
```

---

## 输出规范

产出的知识库完全为人类长效阅读与智能体（RAG/Agent）索引设计：
- **干净交付**：默认仅导航、正文 Markdown 与图片；构建报告和增量状态存放在外部 `.<目录名>.build/`。
- **全相对路径**：图片采用 `![](images/...)` 标准相对格式，知识库目录整体可自由移动或跨设备同步；
- **双模态保真**：公式输出标准 LaTeX，复杂科学表格与复杂化学环状键保留原扫描图裁片双轨备查；
- **全角方括号规范**：中文解释性注释采用 `［...］`，彻底消除 Markdown 链接解析冲突；
- **入口导航**：根目录附带标准 URI 编码的 `00-目录.md`，可无缝融入 Obsidian 知识库体系。

---

## License

MIT
