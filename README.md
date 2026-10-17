# wusen-skills

吴森（Wusen）的 Agent Skills 合集。为 Claude Code、Codex、ZCode 等支持 Agent Skills 的工具提供可复用的工作方法与配套脚本。

Wusen's collection of Agent Skills: reusable workflows and tools for document conversion and knowledge management.

[![Tests](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 技能目录 / Skills

| Skill | 用途 |
| --- | --- |
| [wusen-doc2md](skills/wusen-doc2md/README.md) | 将论文、教材、学位论文和课件整理成保留 LaTeX 公式、高清图表与 Zotero 元数据的 Markdown 知识库。Convert academic documents into a Markdown knowledge base with formulas and high-resolution figures. |

## 安装 / Install

需要 Git 和满足 [skills 安装器要求](https://github.com/vercel-labs/skills) 的 Node.js。安装器会让你选择目标 Agent 和安装方式。

```bash
# 查看技能列表 / List available skills
npx skills add luffysolution-svg/wusen-skills --list

# 安装单个技能 / Install one skill
npx skills add luffysolution-svg/wusen-skills --skill wusen-doc2md

# 全局安装整个合集 / Install all skills globally
npx skills add luffysolution-svg/wusen-skills --skill '*' -g
```

安装器负责分发技能文件。Python、KaTeX 和云端 OCR 凭证的配置见 [wusen-doc2md 安装说明](skills/wusen-doc2md/README.md#安装与依赖)。安装后先运行该技能的 `scripts/doctor.py` 检查当前环境。

The installer distributes skill files. Follow the skill README to configure its runtime, and run `scripts/doctor.py` before first use.

```bash
# 更新已安装的技能 / Update installed skills
npx skills update
```

## 手动安装 / Manual installation

```bash
git clone https://github.com/luffysolution-svg/wusen-skills.git
```

将 `skills/wusen-doc2md` 整个目录复制或链接到目标工具的技能目录，例如 `~/.agents/skills/wusen-doc2md`。保持目录内的脚本、参考文档和示例完整。仅复制 `SKILL.md` 无法运行配套工具。

Copy or link the whole `skills/wusen-doc2md` directory into your agent's skills directory. Keep its scripts and references together.

## 从旧版迁移 / Migration

本仓库由 `doc-ocr-to-markdown` 更名而来，Git 历史保留。旧技能改名为 `wusen-doc2md`，文件移动至 `skills/wusen-doc2md/`。

- Git 克隆更新远程地址：`git remote set-url origin https://github.com/luffysolution-svg/wusen-skills.git`。
- 已安装旧技能的用户先保留自行修改的文件，再移除旧入口并安装 `wusen-doc2md`，避免重复加载。
- 从仓库运行命令前进入 `skills/wusen-doc2md`；脚本参数与文档输出格式保持兼容。

The repository was renamed from `doc-ocr-to-markdown`. Update your Git remote and replace the old skill entry with `wusen-doc2md` after preserving any local edits.

## 开发与验证 / Development

本轮规范检查、文档修订与验证记录见 [Skill 质检记录](docs/skill-quality-review.md)。

每个技能放在 `skills/<name>/` 下，`SKILL.md` 的 `name` 与目录名一致。合集根目录不放 `SKILL.md`。

```bash
python -m pip install -r skills/wusen-doc2md/requirements.txt
npm --prefix skills/wusen-doc2md/scripts install --ignore-scripts --no-audit --no-fund
python -m unittest discover -s skills/wusen-doc2md/tests -v
python skills/wusen-doc2md/scripts/doctor.py --require-ocr
```

`--require-ocr` 仅在需要提交云端 OCR 时使用；离线开发环境可省略该选项。

## License

MIT
