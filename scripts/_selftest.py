"""公式归一化规则自测。改动 normalize_math.py 后运行，确认各条规则仍然生效。

    python _selftest.py

每个用例都对应一个实际踩过的坑。
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_math import (normalize_math, fix_inline_parens, plain_text,
                            split_leading_prose, paren_issues)

cases = [
    # (说明, 输入, 期望包含)
    ('中文进 text',
     r'\eta = \frac {\mathrm{连续} X \mathrm{射线总强度}}{X \mathrm{射线管功率}}',
     r'\text{连续}'),
    ('嵌套 text 展平',
     r'F = \frac {\text {\text{一个晶胞中所有原子散射波的合成振幅}}}{\text {\text{一个电子的散射振幅}}}',
     r'\text{一个晶胞中所有原子散射波的合成振幅}'),
    ('全角括号换半角',
     r'\alpha = f ( \mathrm{或} r _ {n} （Z _ {e}） )',
     '(Z _ {e})'),
    ('rm 简写',
     r'd _ {\rm hkl}',
     r'\mathrm{'),
    ('标题纯文本',
     r'6.3.2 \mathrm{R}^2 比值规律对比法',
     'R²'),
    ('图注纯文本',
     r'图4-1 $\theta$ -sin $\theta$ 关系曲线',
     'θ'),
    ('粘连命令 vert',
     r'\frac{\varphi_{\text{Cl}^-\vertHg_2Cl_2}(298)}{\text{V}}',
     r'\vert Hg'),
    ('粘连命令 cdot',
     r'\mathrm{CuSO_4(1mol\cdotkg^{-1})}',
     r'\cdot kg'),
    ('化学下标移出 text',
     r'\text{NH_4Cl/ZnCl_2/石墨}',
     r'\mathrm{NH_4Cl/ZnCl_2/}\text{石墨}'),
    ('重复 tag 去重',
     r'E = mc^2 \tag{7}\tag{12.26}',
     r'\tag{12.26}'),
    ('损坏 tag 脚注修复',
     r'K_c = 1 \tag{12.45) $ ^{①} \}',
     r'\tag*{(12.45)$^{\text{①}}$}'),
]
failures = []
for name, source, expected in cases:
    for func in (normalize_math, plain_text):
        try:
            result = func(source)
        except Exception:
            continue
        if expected in result:
            print(f'  通过 {name}（{func.__name__}）-> {result[:70]}')
            break
    else:
        failures.append((name, source, expected, normalize_math(source), plain_text(source)))

print()
# 跨界括号
source = r'如果入射角与反射角相等，则 $\delta = a$ （ $\cos \theta - \cos \theta) = 0$ 。'
fixed = fix_inline_parens(source)
print('跨界括号:', fixed)
if fixed.count('(') != fixed.count(')'):
    failures.append(('跨界括号', source, '配对', fixed, ''))

source2 = r'如果入射角与反射角相等，则 $\delta = a$ （ $\cos \theta - \cos \theta$） = 0 。'
print('已正确:', fix_inline_parens(source2) == source2)

source3 = r'直链反应 $(\mathrm{H}_2$ 和 $\mathrm{Cl}_2$ 反应的历程）'
fixed3 = fix_inline_parens(source3)
print('公式开头括号移出:', fixed3)
if fixed3 != r'直链反应 ($\mathrm{H}_2$ 和 $\mathrm{Cl}_2$ 反应的历程）':
    failures.append(('公式开头括号移出', source3, '修复', fixed3, ''))

source4 = r'如果 n = \infty，则可得到式 [因为 $(p / p_s)^{\infty} \rightarrow 0]$ 成立'
fixed4 = fix_inline_parens(source4)
print('公式末尾方括号移出:', fixed4)
if '$] 成立' not in fixed4:
    failures.append(('公式末尾方括号移出', source4, '修复', fixed4, ''))

# 粘连正文
tex = r'\text {和像差（主要是球差} \varDelta r _ {s} = \frac {1}{4} C _ {s} \alpha^ {3}) 。'
leading, body, trailing = split_leading_prose(normalize_math(tex))
print('粘连正文: 前导 =', repr(leading), '尾部 =', repr(trailing))
print('          公式本体 =', repr(body))
# 拆出的正文（前导+尾部）应还原原句，公式本体括号配平
if not leading or not trailing or body.count('(') != body.count(')'):
    failures.append(('粘连正文', tex, '拆分并配平', str(leading) + str(trailing), body))

print()
print('paren_issues 检测:', paren_issues(source) or '无')
if not paren_issues(source):
    failures.append(('括号检测', source, '应检出问题', '', ''))

print()
if failures:
    print(f'失败 {len(failures)} 项：')
    for item in failures:
        print(' ', item)
    raise SystemExit(1)
print('全部归一化用例通过。')
