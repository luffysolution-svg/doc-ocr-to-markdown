#!/usr/bin/env python
"""LaTeX 公式归一化：把 OCR 输出转成 Obsidian 兼容且干净的形式。

规则说明见 references/math-normalization.md。

命令行用法（就地处理 Markdown 文件里的公式）：
    python normalize_math.py fix notes.md [more.md ...]     # 打印改写后的文件
    python normalize_math.py check notes.md                 # 只检查问题，不改动

作为库使用：
    from normalize_math import normalize_math, fix_inline_parens, plain_text
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 匹配 $$...$$ 与 $...$（不匹配 \$ 转义）
MATH_SPAN = re.compile(r'\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\$)(?:\\.|[^$\n])*?(?<!\\)\$')
INLINE_MATH = re.compile(r'(?<!\\)\$(?!\$)(?:\\.|[^$\n])*?(?<!\\)\$')
CJK = r'\u4e00-\u9fff'

GREEK = {
    r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
    r'\varepsilon': 'ε', r'\epsilon': 'ε', r'\theta': 'θ', r'\lambda': 'λ',
    r'\mu': 'μ', r'\nu': 'ν', r'\rho': 'ρ', r'\sigma': 'σ', r'\tau': 'τ',
    r'\varphi': 'φ', r'\phi': 'φ', r'\chi': 'χ', r'\psi': 'ψ', r'\omega': 'ω',
    r'\Delta': 'Δ', r'\Sigma': 'Σ', r'\Omega': 'Ω',
}
MISC = {r'\circ': '°', r'\times': '×', r'\cdot': '·', r'\pm': '±', r'\approx': '≈',
        r'\geq': '≥', r'\leq': '≤', r'\infty': '∞', r'\sim': '∼', r'\rightarrow': '→'}

STUCK_COMMANDS = re.compile(
    r'\\(vert|cdot|times|pm|approx|sim|leq|geq|ne|neq|in|subset|cap|cup|'
    r'forall|exists|partial|nabla|int|sum|prod|rightarrow|leftarrow|'
    r'longrightarrow|longleftarrow|leftrightarrow)([a-zA-Z])'
)


def _fix_stuck_commands(text: str) -> str:
    """修复反斜杠命令与紧随的拉丁字母粘连（如 \\vertHg → \\vert Hg, \\cdotkg → \\cdot kg）。"""
    return STUCK_COMMANDS.sub(r'\\\1 \2', text)


def _fix_text_subscripts(text: str) -> str:
    """修复 \\text{...} 内部出现未闭合下标 _ 或 ^ 导致的 KaTeX 语法错误（多见于化学式与材料名）。"""
    def replace_text(match):
        content = match.group(1)
        if '_' not in content and '^' not in content:
            return match.group(0)
        # 纯公式/化学式（无中文），转为 \\mathrm
        if not re.search(rf'[{CJK}]', content):
            return r'\mathrm{' + content + '}'
        # 含中文与化学式混排，如 MnO_2/石墨
        parts = re.split(rf'([{CJK}]+)', content)
        built = []
        for p in parts:
            if not p:
                continue
            if re.search(rf'[{CJK}]', p):
                built.append(r'\text{' + p + '}')
            else:
                built.append(r'\mathrm{' + p + '}')
        return ''.join(built)

    return re.sub(r'\\text\s*\{([^{}]*)\}', replace_text, text)


def _dedup_tags(text: str) -> str:
    """清理公式中重复的多重 \\tag（如 \\tag{7}\\tag{12.26} 或 \\tag{13.}\\tag{13.49}）。"""
    tag_matches = list(re.finditer(r'\\tag\*?\s*\{[^{}]*\}', text))
    if len(tag_matches) <= 1:
        return text
    # 提取所有 tag，优先选择包含章节小数点（如 12.26、13.49）的正式编号
    tags = [m.group(0) for m in tag_matches]
    best_tag = tags[-1]
    for t in reversed(tags):
        # 寻找形如 \\tag{XX.YY} 的完整编号，过滤未闭合的 \\tag{13.}
        inner = re.sub(r'^\\tag\*?\s*\{|\}$', '', t).strip()
        if re.search(r'\d+\.\d+', inner) and not inner.endswith('.'):
            best_tag = t
            break
    # 保留正文，去除多余 tag
    start_first = tag_matches[0].start()
    end_last = tag_matches[-1].end()
    return text[:start_first].rstrip() + ' ' + best_tag + text[end_last:]


def _fix_arrows_and_tags(text: str) -> str:
    """修复缺失大括号的 \\xrightarrow 以及损坏的式号脚注格式。"""
    # 修复 \\xrightarrow[x]y} 缺少左大括号
    text = re.sub(r'\\xrightarrow\s*\[([^{}\]]*)\]([a-zA-Z0-9_\-]+)\}', r'\\xrightarrow[\1]{\2}', text)
    # 修复 \\tag{12.45) $ ^{①} \} 等式号带脚注损坏的情况
    text = re.sub(r'\\tag\s*\{\s*(\d+(?:\.\d+)?)\)\s*\$?\s*\^?\{?([①②③④⑤⑥⑦⑧⑨⑩])\}?\s*\\?\}',
                  r'\\tag*{(\1)$^{\\text{\2}}$}', text)
    return text


def _collapse_text(text: str) -> str:
    """展平嵌套的 \\text{\\text{...}} → \\text{...}。"""
    open_re = re.compile(r'\\text\s*\{')
    out, index = [], 0
    while index < len(text):
        match = open_re.match(text, index)
        if not match:
            out.append(text[index])
            index += 1
            continue
        depth, cursor = 1, match.end()
        while cursor < len(text) and depth:
            if text[cursor] == '{':
                depth += 1
            elif text[cursor] == '}':
                depth -= 1
            cursor += 1
        if depth:
            out.append(text[index:])
            break
        inner = _collapse_text(text[match.end():cursor - 1])
        inner = re.sub(r'\\text\s*\{([^{}]*)\}', r'\1', inner)
        out.append('\\text{' + inner + '}')
        index = cursor
    return ''.join(out)


def _wrap_cjk(text: str) -> str:
    """把裸露的中文包进 \\text{}，已有的 \\text/\\mathrm 先保护起来避免嵌套。"""
    protected: list[str] = []

    def protect(match):
        protected.append(match.group(0))
        return f'\x00{len(protected) - 1}\x00'

    text = re.sub(r'\\(?:text|mathrm|operatorname|mathbf|mathit)\{[^{}]*\}', protect, text)
    pattern = rf'(?<![\x00a-zA-Z])([{CJK}][{CJK}\s]*[{CJK}])(?![\x00a-zA-Z])'
    text = re.sub(pattern, lambda m: '\\text{' + m.group(1).strip() + '}', text)
    for index, value in enumerate(protected):
        value = re.sub(rf'\\mathrm\{{([^{{}}]*[{CJK}][^{{}}]*)\}}', r'\\text{\1}', value)
        text = text.replace(f'\x00{index}\x00', value)
    return text


def normalize_math(text: str) -> str:
    """归一化一段公式内容（不含 $ 定界符）。"""
    text = text.replace('\\[', '$$').replace('\\]', '$$')
    text = text.replace('\\(', '$').replace('\\)', '$')
    # 修复命令与紧随字母粘连（如 \\vertHg → \\vert Hg, \\cdotkg → \\cdot kg）
    text = _fix_stuck_commands(text)
    # 修复缺失大括号的扩展箭头与式号脚注格式
    text = _fix_arrows_and_tags(text)
    # 清理多重重复 \\tag
    text = _dedup_tags(text)
    # 旧式简写：{\rm xxx} / {\it xxx} → {\mathrm{xxx}} / {\mathit{xxx}}
    text = re.sub(r'\{\s*\\(rm|it)\s+([^{}]*?)\s*\}',
                  lambda m: '{\\' + ('mathrm' if m.group(1) == 'rm' else 'mathit') + '{' + m.group(2) + '}}',
                  text)
    text = re.sub(r'\\(?:rm|it)\s*\{', r'\\mathrm{', text)
    text = re.sub(r'\\(?:rm|it)\b\s+', '', text)
    # 全角标点在数学模式里不被识别，必须换半角
    text = (text.replace('（', '(').replace('）', ')')
                .replace('，', ',').replace('；', ';').replace('：', ':'))
    # 修复 \\text{...} 里的化学下标
    text = _fix_text_subscripts(text)
    return _collapse_text(_wrap_cjk(text))


def fix_inline_parens(text: str) -> str:
    """修复行内公式跨界括号：括号一半在正文、一半在公式内的情况。"""
    spans = list(INLINE_MATH.finditer(text))
    if not spans:
        return text
    pieces, cursor = [], 0
    for span in spans:
        prose = text[cursor:span.start()]
        math = span.group()
        content = math[1:-1]
        opener = prose.rstrip().endswith(('（', '('))
        closer = prose.rstrip().endswith(('）', ')'))

        # 情况 1：正文有开括号，公式末尾有多余的闭括号
        if content.count(')') - content.count('(') == 1 and opener:
            balance, cut = 0, None
            for position, character in enumerate(content):
                if character == '(':
                    balance += 1
                elif character == ')':
                    if balance == 0:
                        cut = position
                        break
                    balance -= 1
            if cut is not None:
                bracket = '）' if prose.rstrip().endswith('（') else ')'
                pieces.append(prose)
                pieces.append('$' + content[:cut].rstrip() + '$')
                pieces.append(bracket + content[cut + 1:])
                cursor = span.end()
                continue
        # 情况 2：正文以闭括号结束，公式开头有多余的开括号
        elif content.count('(') - content.count(')') == 1 and closer:
            balance, cut = 0, None
            for position in range(len(content) - 1, -1, -1):
                character = content[position]
                if character == ')':
                    balance += 1
                elif character == '(':
                    if balance == 0:
                        cut = position
                        break
                    balance -= 1
            if cut is not None:
                bracket = '（' if prose.rstrip().endswith('）') else '('
                pieces.append(prose)
                pieces.append(bracket + content[:cut])
                pieces.append('$' + content[cut + 1:].strip() + '$')
                cursor = span.end()
                continue
        # 情况 3：公式开头吃进了开括号，如 $(\\mathrm{H}_2$
        elif (content.startswith('(') or content.startswith('（')) and content.count('(') - content.count(')') == 1:
            bracket = '（' if content.startswith('（') else '('
            inner = content[1:].strip()
            pieces.append(prose)
            pieces.append(bracket)
            pieces.append('$' + inner + '$')
            cursor = span.end()
            continue
        # 情况 4：公式结尾吃进了方括号，如 $...]$ 且内部方括号不平
        elif content.endswith(']') and content.count(']') - content.count('[') == 1:
            pieces.append(prose)
            pieces.append('$' + content[:-1].rstrip() + '$')
            pieces.append(']')
            cursor = span.end()
            continue

        pieces.append(prose)
        pieces.append(math)
        cursor = span.end()
    pieces.append(text[cursor:])
    return ''.join(pieces)


def split_leading_prose(tex: str) -> tuple[str | None, str, str | None]:
    """若公式开头粘了被截断的正文片段，把它（以及配对的尾括号）拆回正文。

    两种情况都会出现：
      1. 前缀片段让整个公式括号不配平；
      2. 前缀 `\\text{...}` 自身括号不配平，闭括号落在公式尾部
         （`和像差（主要是球差 ... α³）` —— 括号是跨片段的）。

    返回 (前导正文或 None, 公式本体, 尾部正文或 None)。
    """
    match = re.match(rf'\s*\\text\s*\{{([^{{}}]*[{CJK}][^{{}}]*)\}}\s*', tex)
    if not match:
        return None, tex, None

    leading = (match.group(1).strip(), match.end())
    rest = tex[leading[1]:]
    fragment = leading[0]

    # 前缀片段自身括号不配平 → 是被截断的正文，拆出去
    if fragment.count('(') <= fragment.count(')'):
        return None, tex, None

    deficit = fragment.count('(') - fragment.count(')')
    # 公式尾部应有对应数量的闭括号，把它们也还给正文。
    # 尾部可能跟着中文句号等标点，先剥离标点再找括号。
    trailing = ''
    core = rest.rstrip()
    punctuation = ''
    while core and core[-1] in '。．.、,;；':
        punctuation = core[-1] + punctuation
        core = core[:-1].rstrip()
    for _ in range(deficit):
        if core.endswith(')') or core.endswith('）'):
            core = core[:-1].rstrip()
            trailing = ')' + trailing
        else:
            break
    if not core:
        return None, tex, None
    if trailing:
        trailing = trailing.replace(')', '）')
    return fragment, core, (trailing + punctuation) or None


def plain_text(text: str) -> str:
    """把含公式的说明文字转成纯文本，用于标题、图注、alt 文本。

    标题和图注里绝不能留 $ 或 \\，否则会破坏 Markdown 结构。
    """
    text = re.sub(r'\\(?:mathrm|mathbf|text|operatorname|mathit)\{([^{}]*)\}', r'\1', text)
    for command, symbol in {**GREEK, **MISC}.items():
        text = text.replace(command, symbol)
    text = re.sub(r'\\([a-zA-Z]+)\s*', lambda m: ' ' + m.group(1) if len(m.group(1)) > 1 else m.group(1), text)
    text = re.sub(r'\$\$|\$', '', text)
    text = text.replace('^2', '²').replace('^3', '³')
    text = re.sub(r'\s*\{\s*°\s*\}', '°', text)
    text = re.sub(r'[{}]', '', text).replace('*', '')
    return re.sub(r'\s+', ' ', text).strip()


def paren_issues(text: str) -> list[str]:
    """找出括号不配平的公式，返回问题描述列表。"""
    issues = []
    for match in MATH_SPAN.finditer(text):
        raw = match.group()
        body = raw[2:-2] if raw.startswith('$$') else raw[1:-1]
        stripped = re.sub(r'\\[a-zA-Z]+|[{}_^]', '', body)
        for open_char, close_char in [('(', ')'), ('[', ']')]:
            if stripped.count(open_char) != stripped.count(close_char):
                issues.append(body.strip()[:80])
                break
    return issues


def process(text: str) -> str:
    """对整篇 Markdown 应用全部归一化规则。"""
    out, cursor = [], 0
    for match in MATH_SPAN.finditer(text):
        out.append(fix_inline_parens(text[cursor:match.start()]))
        raw = match.group()
        display = raw.startswith('$$')
        body = raw[2:-2] if display else raw[1:-1]
        leading, body, trailing = split_leading_prose(normalize_math(body))
        if leading:
            out.append(leading + (trailing or ''))
        out.append('$$\n' + body.strip() + '\n$$' if display else '$' + body.strip() + '$')
        cursor = match.end()
    out.append(fix_inline_parens(text[cursor:]))
    return ''.join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description='LaTeX 公式归一化')
    parser.add_argument('mode', choices=['fix', 'check'])
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()

    failed = False
    for name in args.files:
        path = Path(name)
        original = path.read_text(encoding='utf-8')
        if args.mode == 'check':
            issues = paren_issues(original)
            if issues:
                failed = True
                print(f'{path.name}: {len(issues)} 处括号不配平')
                for item in issues[:10]:
                    print(f'  {item}')
            else:
                print(f'{path.name}: 括号检查通过')
            continue
        fixed = process(original)
        sys.stdout.write(fixed if len(args.files) == 1 else f'<!-- {path.name} -->\n{fixed}')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
