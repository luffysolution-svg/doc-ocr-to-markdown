# 教材与学术长文档处理指引 (Book & Monograph Workflow)

长篇学术教材、专著、学位论文（通常 200–800 页，公式成千上万）与演示课件（PPTX，几十页）有本质区别。若直接套用课件流水线，会导致图文断裂、分片错位和大量标题误判。

本指南提供面向超长学术教材与专著的工业级处理标准。

---

## 1. 课件模式 (Slides) vs 教材模式 (Book) 的核心差异

| 维度 | 课件模式 (`--mode slides`) | 教材/专著模式 (`--mode book`) |
| :--- | :--- | :--- |
| **主导场景** | 讲义、PPT、专题汇报（每页一张幻灯片） | 经典教材、学术专著、科研论文（长篇连贯阅读） |
| **图文排版** | 图片汇总后置顶（符合 PPT 视觉中心） | **自然流混排**：严格按段落上下文穿插图表 |
| **跨页关系** | 页面间独立，一页一个主题 | **保留段落边界**：不凭缺少句末标点自动拼接，避免误合并正文与习题 |
| **标题判定** | 宽松（页顶短文本常为幻灯片标题） | **严格**：依据章/节编号正则（如 `第X章`、`8.1`），严禁把页顶首句误升标题 |
| **表格处理** | 转为简明 Markdown 表格 | **双轨保真**：Markdown 检索文本 + 复杂大表原版扫描高清裁片 |
| **嵌套资源** | 通常无内嵌图 | 自动提取单元格内嵌套二维码、化学符号与矢量小图 |

---

## 2. 500+ 页大文档的“分片-识别-组装”标准三步法

### 第一步：智能切分与 Manifest 生成

MinerU 云端批量 API 对单文件有体积极限（通常 200 MB / 600 页以内）。几百页扫描教材建议按 80–100 页分片：

```bash
# 自动按 96 页切片，并在工作目录生成 chunks/、manifest.json 与 ids.tsv
python scripts/mineru_ocr.py split path/to/textbook.pdf --out-root ./ocr-work --chunk-size 96
```

生成的 `manifest.json` 记录了每个分片的全局起始页码：
```json
{
  "source": "path/to/textbook.pdf",
  "pages": 556,
  "chunk_size": 96,
  "parts": [
    {"id": "part01", "start_page": 1, "end_page": 96, "pages": 96},
    {"id": "part02", "start_page": 97, "end_page": 192, "pages": 96}
  ]
}
```

### 第二步：批量提交与结果轮询

```bash
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv
```

接口采用一次性整批提交，轮询等待所有批块全部下载解压完成。

### 第三步：按全书章节重组 (Book Assembly)

准备各章节的页码起止表 `chapters.json`：
```json
[
  {"id": "front", "title": "书目信息与前言", "start": 1, "end": 13, "file": "01-前言.md"},
  {"id": "ch08", "title": "第八章 电解质溶液", "start": 24, "end": 85, "file": "08-电解质溶液.md"},
  {"id": "appendix", "title": "附录", "start": 536, "end": 540, "file": "15-附录.md"}
]
```

执行全书重组：
```bash
python scripts/build_markdown.py \
    --raw ./ocr-work/raw \
    --out ./知识库 \
    --mode book \
    --manifest ./ocr-work/manifest.json \
    --chapters ./chapters.json \
    --pdf path/to/textbook.pdf \
    --keep-table-images
```

---

## 3. 常见陷阱与应对策略

### 3.1 过滤模型推理与提示词泄漏
极少数空白页或衬页中，多模态 OCR 模型可能输出评测规则说明（如 `The Ground Truth image displays...`, `According to Rule 2...`）。
* **对策**：`build_markdown.py` 内置漏词过滤，自动识别并排除此类段落；核实空白隔页并记录在构建报告中。

### 3.2 跨页续表占位块
MinerU 在遇到跨页连续大表时（如活度因子表、标准电极电势表），会将后续页内容合并到首页表格中，后页仅留一个空 table 占位块。
* **对策**：空表块视为合法的续表占位，不判定为数据丢失。

### 3.3 单元格内嵌二维码与图表
现代教材各章末尾附带拓展数字化资源的二维码。
* **对策**：HTML 表格转换时，优先扫描所有 `<img src="...">`，下载为独立图片资产并替换为 `![二维码](images/...)` 相对引用，避免 `cell.get_text()` 吞图。

### 3.4 复杂科学大表与环状化学式双轨保真
* **复杂大表**：Markdown 纯文本表格方便全文搜索与 LLM 提取，但深层合并单元格和密集上下标可能失真。开启 `--keep-table-images` 会同步截取原版扫描图裁片，供科学计算复核。
* **化学环状键**：对于三角形、多边形等复杂空间结构，严禁使用 `\backslash` 乱拼字符，一律降级为局域原图裁切嵌入。
