#!/usr/bin/env node
/**
 * KaTeX 全量公式编译校验。
 *
 * 用法：
 *    1. 先安装 KaTeX（只做一次）：npm install katex
 *    2. 从 Markdown 抽取公式：
 *         python extract_formulas.py <目录> --out formulas.json
 *    3. 校验：
 *         node validate_math.cjs formulas.json
 *
 * formulas.json 格式：[{"file": "...", "tex": "...", "line": 12}, ...]
 * 退出码 0 表示全部通过，1 表示有编译失败。
 *
 * 说明：必须把 $$ / $ 外壳剥掉再交给 KaTeX —— 它只接受纯公式内容，
 * 连着定界符传会报 "Can't use function '$' in math mode"。
 */
const fs = require('fs');
const path = require('path');

function loadKatex() {
  const candidates = [
    'katex',
    path.join(__dirname, 'node_modules', 'katex'),
    path.join(process.cwd(), 'node_modules', 'katex'),
  ];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (error) {
      /* 继续尝试下一个位置 */
    }
  }
  console.error('找不到 katex。请先运行：npm install katex');
  process.exit(2);
}

const katex = loadKatex();

const target = process.argv[2];
if (!target) {
  console.error('用法：node validate_math.cjs formulas.json');
  process.exit(2);
}

const records = JSON.parse(fs.readFileSync(target, 'utf8'));
const failures = [];
const normalized = [];

for (const record of records) {
  let tex = String(record.tex || '').trim();
  const display = tex.startsWith('$$');
  if (display) {
    tex = tex.replace(/^\$\$/, '').replace(/\$\$$/, '');
  } else {
    tex = tex.replace(/^\$/, '').replace(/\$$/, '');
  }
  tex = tex.trim();
  const entry = { ...record, tex, display };
  normalized.push(entry);
  try {
    katex.renderToString(tex, {
      displayMode: display,
      throwOnError: true,
      strict: false,
      trust: false,
    });
  } catch (error) {
    failures.push({ file: record.file, line: record.line, tex, error: error.message });
  }
}

const outPath = path.join(path.dirname(target), 'math-validation.json');
fs.writeFileSync(outPath, JSON.stringify({
  total: records.length,
  pass: records.length - failures.length,
  failed: failures.length,
  failures,
}, null, 2));

console.log(`公式总数 ${records.length}，通过 ${records.length - failures.length}，失败 ${failures.length}`);
if (failures.length) {
  console.log('\n失败详情：');
  for (const item of failures.slice(0, 30)) {
    console.log(`  ${item.file || '?'}:${item.line || '?'}`);
    console.log(`    ${item.tex.slice(0, 120)}`);
    console.log(`    ${item.error}`);
  }
  if (failures.length > 30) {
    console.log(`  ...还有 ${failures.length - 30} 项，见 ${outPath}`);
  }
  process.exit(1);
}
console.log('全部公式编译通过。');
