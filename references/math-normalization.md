# 公式归一化规则

OCR（尤其 MinerU）输出的 LaTeX 通常"语法上能编译"，但直接放进 Markdown 会有几类固定问题。下面每条都来自实际踩坑，附真实的前后对照。

`scripts/normalize_math.py` 实现了全部规则，优先直接调用。

## 目标格式

- 行内：`$E = mc^2$`
- 独立：`$$` 单独成行，公式内容一行，`$$` 单独成行
- 保留原式号：`\tag{1-6}`（正文常引用"由式（1-6）可知"，去掉就对不上了）

Obsidian 用 KaTeX 渲染，与 MathJax 语法高度重合。下面规则都按 KaTeX 兼容性制定。

## 规则 1：中文放进 `\text{}` 而不是 `\mathrm{}`

**问题**：OCR 会把公式里的中文词用 `\mathrm{}` 包起来。KaTeX 对 `\mathrm{中文}` 会输出 `No character metrics for 'X' in style 'Main-Regular'` 告警，且渲染中文字符间距不齐。

```latex
% 归一化前（OCR 输出）
\eta = \frac {\mathrm{连续} X \mathrm{射线总强度}}{X \mathrm{射线管功率}} = K Z V

% 归一化后
\eta = \frac {\text{连续} X \text{射线总强度}}{X \text{射线管功率}} = K Z V
```

同理，裸露的中文（没被任何命令包住）也要包进 `\text{}`：

```latex
% 前
h v _ {K \alpha} = W _ {K} - W _ {L} 或 h v _ {K} = h v _ {L}

% 后（"或" 是中文，需包起来）
h v _ {K \alpha} = W _ {K} - W _ {L} \text{或} h v _ {K} = h v _ {L}
```

## 规则 2：展平嵌套的 `\text{}`

**问题**：OCR 重排时会产生 `\text{\text{...}}` 嵌套，KaTeX 里行为不确定。

```latex
% 前
F = \frac {A _ {b}}{A _ {e}} = \frac {\text {\text{一个晶胞中所有原子散射波的合成振幅}}}{\text {\text{一个电子的散射振幅}}}

% 后
F = \frac {A _ {b}}{A _ {e}} = \frac {\text{一个晶胞中所有原子散射波的合成振幅}}{\text{一个电子的散射振幅}}
```

## 规则 3：公式内的全角标点换成半角

**问题**：中文输入法打出的是全角括号 `（）`、逗号 `，`、分号 `；`。在数学模式里它们**不计入括号配平**，于是 `(...（...）...)` 这种混用会导致括号计数错误，而且渲染宽度异常。

```latex
% 前
\alpha = f ( \mathrm{或} r _ {n} （Z _ {e}） )

% 后
\alpha = f ( \text{或} r _ {n} (Z _ {e}) )
```

这条规则同时解决了大量的"括号不平衡"告警。归一化后应做一次括号配平检查。

## 规则 4：公式前后粘进来的正文要拆回正文

**问题**：OCR 有时把公式前面的一小段正文（含括号的）一起圈进公式块，导致公式开头挂着一截中文、括号还不配平。

```latex
% 前（"和像差（主要是球差" 是正文，被粘进了公式）
$$
\text{和像差（主要是球差} \varDelta r _ {s} = \frac {1}{4} C _ {s} \alpha^ {3}) 。
$$

% 后（正文回到正文，公式干净）
和像差（主要是球差
$$
\varDelta r _ {s} = \frac {1}{4} C _ {s} \alpha^ {3}
$$
）。
```

判断方法：如果公式里括号不配平，且开头是 `\text{中文...}` 这样的片段，尝试把这段前缀拆出来，看剩下的部分是否配平——配平就说明找到了正确的切点。

## 规则 5：行内公式跨界括号

**问题**：源文档里括号一半在正文、一半在公式内的写法，OCR 会照抄，导致行内公式自身括号不配平。

```markdown
% 前（后括号在公式里，公式自身不配平）
如果入射角与反射角相等，则 $\delta = a$ （ $\cos \theta - \cos \theta) = 0$ 。

% 后（括号还给正文，公式干净）
如果入射角与反射角相等，则 $\delta = a$ （ $\cos \theta - \cos \theta$） = 0 。
```

## 规则 6：`\rm` / `\it` 等简写正常化

OCR 会输出 `\rm`、`\it` 这类旧式简写。统一成 `\mathrm{}`：

```latex
% 前
d _ {\rm hkl}

% 后
d _ {\mathrm{hkl}}
```

## 规则 7：标题里的公式转纯文本

**问题**：标题里不该出现 LaTeX——Obsidian 的目录、大纲、搜索结果里会显示成 `\mathrm{R}^2` 原始字符。

```markdown
% 前
### 6.3.2 \mathrm{R}^2 比值规律对比法

% 后
### 6.3.2 R² 比值规律对比法
```

常用替换：`\mathrm{X}` → `X`、`^2` → `²`、`^3` → `³`、`\alpha` → `α`、`\theta` → `θ`、`\psi` → `ψ`、`\circ` → `°`、`\times` → `×`。

## 规则 8：图注里的公式也转纯文本

**问题**：图注会进入 `![]()` 的 alt 文本和图片说明。**如果图注里含 `$` 或 `\`，Markdown 解析器会把 image 标签拆坏**——实测这个 bug 在纯文本检查里完全看不出来，只有浏览器渲染才暴露。

```markdown
% 前（$ 破坏了 image 标签，图片不显示）
![图4-1 $\theta$ -sin $\theta$ 关系曲线](images/ch04/xxx.png)

% 后
![图4-1 θ -sin θ 关系曲线](images/ch04/xxx.png)
```

图注、alt 文本、标题一律不含 `$`、`\`、`{`、`}`、`*`。

## 规则 9：反斜杠命令与紧随物种符号粘连分离

**问题**：OCR 经常将操作符命令与物理量/化学符号连写，缺少空格，导致 LaTeX 将其视作不存在的未定义宏。

```latex
% 前（非法宏 \vertHg 和 \cdotkg）
\frac{\varphi_{\text{Cl}^-\vertHg_2Cl_2}(298\text{ K})}{\text{V}}
\mathrm{CuSO_4(1mol\cdotkg^{-1})}

% 后（补齐命令分隔空格）
\frac{\varphi_{\text{Cl}^-\vert Hg_2Cl_2}(298\text{ K})}{\text{V}}
\mathrm{CuSO_4(1mol\cdot kg^{-1})}
```

常见受影响命令：`\vert`、`\cdot`、`\times`、`\pm`、`\partial`、`\nabla`、`\sum` 等。

## 规则 10：化学式移出 `\text{}` 避免非法下标

**问题**：OCR 把电池电解液、电极材料或化学反应式放进 `\text{...}` 中，在文本模式下出现未闭合的下划线 `_` 会直接导致 KaTeX 语法解析中断（`Expected 'EOF', got '_'`）。

```latex
% 前
\text{NH_4Cl/ZnCl_2/MnO_2/湿C粉/NH_4Cl}

% 后（化学式放入 \mathrm，仅中文汉字放入 \text）
\mathrm{NH_4Cl/ZnCl_2/MnO_2/}\text{湿C粉}\mathrm{/NH_4Cl}
```

## 规则 11：式号重复 `\tag` 去重与损坏脚注标记清理

**问题**：OCR 时常产生双重 `\tag`（如同时识别了临时编号与真实章节编号），或将原书式号右上角的脚注上标（如 `(12.45)¹`）误识成跨界的未闭合花括号。

```latex
% 前
E = mc^2 \tag{7}\tag{12.26}
K_c = \frac{f^{\neq}}{f_{\mathrm{A}}} \tag{12.45) $ ^{①} \}

% 后
E = mc^2 \tag{12.26}
K_c = \frac{f^{\neq}}{f_{\mathrm{A}}} \tag*{(12.45)$^{\text{①}}$}
```

## 规则 12：复杂环状分子与化学键结构的原图降级

**问题**：OCR 试图用 `\begin{array}` 与 `\backslash` 拼凑三角形或环状化学键（如环丙烷异构化反应），导致公式无法解析且严重失真。

**原则**：对无法被标准 LaTeX 宏包优雅表达的复杂二维反应历程、空间环状骨架，**坚决不强行拼凑字符，使用源 PDF 局域高清裁切直接嵌入图片**，确保科研结论真实严谨。

## 校验

归一化做完后**必须全量编译校验**，不能抽查：

```bash
npm install katex
node scripts/validate_math.cjs formulas.json
```

要求 100% 通过。KaTeX 的 `throwOnError: true` 会把语法问题变成异常。

注意校验时要把 `$$` / `$` 外壳剥掉再传给 KaTeX——它只接受纯公式内容，连着定界符传会报 "Can't use function '$' in math mode"。

同时检查括号配平：剥掉 `\命令`、`{}`、`_`、`^` 之后统计 `()` 和 `[]` 是否成对。
