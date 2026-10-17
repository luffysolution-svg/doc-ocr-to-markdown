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

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
