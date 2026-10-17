<div align="center">

# wusen-skills

**Wusen's Agent Skills collection**

Practical workflows, packaged as reusable skills for AI assistants.

[![Tests](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.md) · **English**

[Quick start](#quick-start) · [Skills](#skills) · [Usage](#usage) · [Contributing](#contributing)

</div>

## Quick start

Install the entire collection to your user directory:

```bash
npx skills add luffysolution-svg/wusen-skills --skill '*' -g
```

The installer guides you through selecting an AI assistant and an installation method. Git and a Node.js version supported by the [skills CLI](https://github.com/vercel-labs/skills) are required.

Install a specific skill:

```bash
npx skills add luffysolution-svg/wusen-skills --skill wusen-doc2md -g
```

## Skills

| Skill | Purpose | Documentation |
| --- | --- | --- |
| **wusen-doc2md** | Turn papers, textbooks, and slides into a Markdown knowledge base with LaTeX formulas, high-resolution figures, and Zotero metadata. | [Usage guide](skills/wusen-doc2md/README.en.md) |

### wusen-doc2md

A workflow for academic documents with dense formulas, complex figures, and challenging layouts, covering recognition, correction, knowledge-base assembly, and validation.

- **Document-specific organization**: chapters for textbooks, lectures for slides, and standalone notes for papers.
- **Scientific content preservation**: compile every formula and extract or recrop figures from the source.
- **Reference management**: Zotero metadata, annotation synchronization, and links between papers and supporting information.
- **Maintainable output**: relative paths, dedicated image directories, external build state, and guarded incremental updates.

[Explore the full guide and examples →](skills/wusen-doc2md/README.en.md)

## Usage

After installation, describe your task in an AI assistant that supports Agent Skills. For example:

> Turn this scanned textbook into a Markdown knowledge base, organized by chapter. Preserve formulas and high-resolution figures, and validate the complete output.

Each skill's guide documents its runtime and input requirements. `wusen-doc2md` requires Python and KaTeX; cloud OCR also requires MinerU credentials. Follow [Installation and dependencies](skills/wusen-doc2md/README.en.md#installation-and-dependencies), then run `scripts/doctor.py` to check your environment.

### Discover and update

```bash
# List the skills in this collection
npx skills add luffysolution-svg/wusen-skills --list

# Update installed skills
npx skills update
```

### Manual installation

```bash
git clone https://github.com/luffysolution-svg/wusen-skills.git
```

Copy or link the complete skill directory from `skills/` into your assistant's skill directory, such as `~/.agents/skills/wusen-doc2md`. Keep the scripts, references, and examples together.

## Contributing

Use [GitHub Issues](https://github.com/luffysolution-svg/wusen-skills/issues) to report problems, share reproducible cases, or suggest improvements. Pull requests are welcome.

Place each skill under `skills/<skill-name>/`, with a matching name in `SKILL.md`. Preserve the practical guidance in existing skills and keep changes focused on the issue being addressed.

Validate `wusen-doc2md`:

```bash
python -m pip install -r skills/wusen-doc2md/requirements.txt
npm --prefix skills/wusen-doc2md/scripts install --ignore-scripts --no-audit --no-fund
python -m unittest discover -s skills/wusen-doc2md/tests -v
python skills/wusen-doc2md/scripts/doctor.py
```

## License

This project is distributed under the [MIT License](LICENSE).
