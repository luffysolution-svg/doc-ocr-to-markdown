<div align="center">

# wusen-skills

**吴森的 Agent Skills 合集**

把实际使用中沉淀的工作方法，整理成可安装、可复用的技能。

[![自动化测试](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml)
[![MIT 许可证](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**简体中文** · [English](README.en.md)

[快速安装](#快速安装) · [技能目录](#技能目录) · [使用说明](#使用说明) · [参与贡献](#参与贡献)

</div>

## 快速安装

### 让 AI 助手安装（推荐）

复制下面的指令，发给你正在使用的 AI 编码助手：

```text
请帮我安装并配置吴森的 wusen-skills 技能合集：
https://github.com/luffysolution-svg/wusen-skills

1. 检查 Git 和 Node.js，通过以下命令安装合集到当前 AI 助手的用户级技能目录：
   npx skills add luffysolution-svg/wusen-skills --skill '*' -g
   选择当前助手对应的安装目标；已有安装时先检查本地修改。
2. 阅读已安装技能的 README，按各自要求配置依赖。
3. 对 wusen-doc2md，进入包含 SKILL.md 的技能目录，优先使用独立 Python 虚拟环境，再执行：
   python -m pip install -r requirements.txt
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
   python scripts/doctor.py
   python -m unittest discover -s tests -v
4. 检查 MINERU_API_TOKEN 配置状态，不显示凭证值；未配置时指导我从 https://mineru.net 获取并在本地设置。
5. 对 wusen-paper-reader，使用 Python 3.10+ 从技能目录执行 python -m unittest discover -s tests -v；附带证据工具无第三方依赖，PDF/OCR/Zotero按实际输入检查可用性。
6. 汇报安装位置、验证结果和仍需配置的项目。
```

完整 AI 安装指令：[文档解析](skills/wusen-doc2md/README.md#方式一让-ai-助手安装配置推荐) · [文献解读](skills/wusen-paper-reader/README.md#让ai助手安装推荐)

### 命令行安装

安装整个合集到用户目录：

```bash
npx skills add luffysolution-svg/wusen-skills --skill '*' -g
```

安装器会引导你选择目标 AI 助手和安装方式。需要 Git 和满足 [skills 安装器要求](https://github.com/vercel-labs/skills) 的 Node.js。

只安装指定技能：

```bash
npx skills add luffysolution-svg/wusen-skills --skill wusen-doc2md -g
```

## 技能目录

| 技能 | 用途 | 文档 |
| --- | --- | --- |
| **wusen-doc2md** | 将论文、教材和课件整理成保留 LaTeX 公式、高清图表与 Zotero 元数据的 Markdown 知识库。 | [使用指南](skills/wusen-doc2md/README.md) |
| **wusen-paper-reader** | 单篇精读、跨文献对比、跨学科与跨主题解读及知识库问答，优先材料与化学，保留可追溯证据。 | [使用指南](skills/wusen-paper-reader/README.md) |

### wusen-doc2md

面向公式多、图表多、版面复杂的学术文档，覆盖从识别、校对到知识库装配与校验的完整流程。

- **按文档组织**：教材分章、课件按讲次整理、论文独立成篇。
- **保留科学内容**：公式全量编译校验，图表回源提取与高清裁切。
- **连接文献管理**：支持 Zotero 元数据、批注同步与主文/补充材料双链。
- **便于长期维护**：相对路径、独立图片目录、外置构建状态与受保护的增量更新。

[查看完整说明与案例 →](skills/wusen-doc2md/README.md)

## 使用说明

安装后，在支持 Agent Skills 的 AI 助手中描述任务即可，例如：

> 把这份扫描教材整理成 Markdown 知识库，按章节组织，保留公式和高清图表，并完成全量校验。

每个技能的运行环境和输入要求以其使用指南为准。`wusen-doc2md` 需要 Python 和 KaTeX；云端 OCR 另需配置 MinerU 凭证。先按 [安装与依赖](skills/wusen-doc2md/README.md#安装与依赖) 配置，再运行 `scripts/doctor.py` 检查环境。

### 查看与更新

```bash
# 查看合集中的技能
npx skills add luffysolution-svg/wusen-skills --list

# 更新已安装的技能
npx skills update
```

### 手动安装

```bash
git clone https://github.com/luffysolution-svg/wusen-skills.git
```

将 `skills/` 下需要的技能目录完整复制或链接到 AI 助手的技能目录，例如 `~/.agents/skills/wusen-doc2md`。保留其中的脚本、参考文档与示例。

## 参与贡献

欢迎通过 [问题反馈](https://github.com/luffysolution-svg/wusen-skills/issues) 提交使用问题、可复现案例和改进建议，也可以提交拉取请求。

每个技能放在 `skills/<技能名>/` 下，`SKILL.md` 中的名称与目录名保持一致。修改已有技能时保留实战经验，只调整与问题直接相关的内容。

验证 `wusen-doc2md`：

```bash
python -m pip install -r skills/wusen-doc2md/requirements.txt
npm --prefix skills/wusen-doc2md/scripts install --ignore-scripts --no-audit --no-fund
python -m unittest discover -s skills/wusen-doc2md/tests -v
python skills/wusen-doc2md/scripts/doctor.py
```

验证 `wusen-paper-reader`（Python 3.10+，无第三方依赖）：

```bash
python -m unittest discover -s skills/wusen-paper-reader/tests -v
```

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
