# doc-ocr-to-markdown

把扫描件、PDF、PPT、教材、讲义等文档整理成带公式和图片的 Markdown 知识库 —— 一个给 AI 编码助手用的 [Agent Skill](https://docs.anthropic.com/en/docs/claude-code/skills)。

不是简单的文本提取。它处理的是这类文档真正的难点：**公式要还原成可渲染的 LaTeX、图片要清晰、OCR 错字要用源文件校正**。

## 解决什么问题

用普通 OCR 把一份带公式的课件转成 Markdown，你通常会得到：

- 公式变成一堆散落的单字符，分式结构和上下标全丢了
- 插图变成模糊的页面截图，或者干脆没提出来
- 同音字、形近字错误（"韧致辐射"→ 应为"轫致辐射"、"频率为 v"→ 应为"频率为 ν"）
- 图片里的标签被当成正文重复一遍
- 同一张图在连续多页重复出现，知识库里堆满重复文件

这个 skill 覆盖了从 OCR 到交付的完整流程，每条规则都来自实际踩过的坑。

## 包含什么

```
SKILL.md                        流程、输出标准、常见问题
references/
  mineru-api.md                 MinerU API 契约、结果结构、连接问题处理
  math-normalization.md         公式归一化 8 条规则，附真实前后对照
scripts/
  mineru_ocr.py                 OCR 提交/轮询/下载（含失败回退）
  normalize_math.py             公式归一化（可作库调用，也可命令行）
  extract_formulas.py           从 Markdown 抽取公式
  validate_output.py            交付前结构校验
  validate_math.cjs             KaTeX 全量公式编译校验
  preview_server.py             本地 Markdown + KaTeX 预览
  _selftest.py                  归一化规则自测
```

## 核心流程

1. **先判断，再动手** —— 源文件已有原生文字和内嵌原图时，可能根本不需要 OCR，直接提取更准更清晰。
2. **OCR** —— 用 MinerU 一次拿到文字、LaTeX 公式、表格、插图坐标。
3. **用源文件校正文字** —— 按文本框坐标 + 相似度与 OCR 配对，源文件文字是"真值"。
4. **提取图片** —— 优先取内嵌原图（原始分辨率），按内容哈希去重，剔除落在图片区域内的重复标签文字。
5. **归一化公式** —— 中文进 `\text{}`、展平嵌套、全角标点换半角、拆回被粘连的正文、修复跨界括号。
6. **组织正文** —— 按知识结构合并，同名小节不重复生成标题，标题层级不跳跃。
7. **全量校验** —— KaTeX 逐个编译全部公式（要求 100% 通过），再浏览器实际渲染验证。

## 安装

### 作为 Agent Skill

把整个目录放到技能目录下：

```bash
# 用户级（所有项目可用）
git clone https://github.com/<your-name>/doc-ocr-to-markdown ~/.agents/skills/doc-ocr-to-markdown
```

支持的路径（按优先级）：`<项目>/.zcode/skills/`、`<项目>/.agents/skills/`、`~/.zcode/skills/`、`~/.agents/skills/`。

### 依赖

```bash
pip install requests pymupdf pillow python-pptx markdown markdown-it-py beautifulsoup4
npm install katex          # 仅公式校验与预览需要
```

`python-pptx` 只在源文件是 PPTX 时需要。MinerU OCR 需要 `MINERU_API_TOKEN` 环境变量（见 [mineru.net](https://mineru.net)）。

## 使用

安装后，直接提需求即可触发：

> 把 `./课件/` 里的 PDF 整理成 Markdown 知识库，带公式和图片

也可以单独调用脚本：

```bash
# OCR（需要 MINERU_API_TOKEN）
python scripts/mineru_ocr.py submit --out-root ./ocr-out *.pdf

# 公式归一化
python scripts/normalize_math.py fix notes.md > notes-fixed.md
python scripts/normalize_math.py check notes.md          # 只检查

# 交付前校验
python scripts/validate_output.py ./知识库 --images-dir images

# 公式全量编译校验
python scripts/extract_formulas.py ./知识库 --out formulas.json
node scripts/validate_math.cjs formulas.json

# 本地预览
python scripts/preview_server.py ./知识库
```

## 一个具体的坑

MinerU 返回的 `bbox` 是 **0–1000 归一化坐标**，不是像素也不是 PDF point。拿它裁图或与源文件对象做位置比对前必须换算——这个坑不踩一次很难想到。

另一个：结果 zip 托管在 `cdn-mineru.openxlab.org.cn`，如果本机 DNS 被代理的 fake-IP 接管，这个域名会解析到保留地址，表现为 TLS 握手 EOF（而 `mineru.net` 本身正常，所以容易误判为网络没问题）。`mineru_ocr.py` 里用公共 DoH 取真实 IP 再指定连接目标，保留 TLS 校验。

## 输出标准

这个 skill 产出的知识库是"干净"的：

- 正文连续，没有页码、"来源"标注、知识导读、折叠原图块等编辑痕迹
- 图片用标准 `![](相对路径)` 直接预览，不用 HTML
- 相对路径，整个目录可复制可移动
- 公式用 Obsidian 兼容语法，同时通用 Markdown 渲染器也能处理

## 适用范围与限制

**适用**：含公式和图表的课件、教材、讲义、论文、扫描件；批量文档转知识库；为 RAG 或 Agent 检索准备结构化内容。

**限制**：图片内的文字不做 OCR 重建（保留原图）；纯扫描件没有源文字可校正，文字准确性完全依赖 OCR；表格识别质量取决于原文档版式复杂度。公式归一化规则针对 KaTeX/MathJax 语法，不覆盖所有 LaTeX 宏包。

## License

MIT
