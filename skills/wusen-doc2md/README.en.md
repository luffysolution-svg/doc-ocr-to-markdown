<div align="center">

# wusen-doc2md

**Turn papers, textbooks, and slides into a maintainable Markdown knowledge base.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![KaTeX validation](https://img.shields.io/badge/KaTeX-100%25%20Strict-brightgreen.svg)](https://katex.org/)
[![Tests](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/wusen-skills/actions/workflows/tests.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-Claude%20%7C%20ZCode%20%7C%20Codex-orange.svg)](https://agentskills.io)

[简体中文](README.md) · **English**

[Installation](#installation-and-dependencies) · [Document workflows](#three-document-workflows) · [Case studies](#case-studies) · [Commands](#command-reference)

</div>

Convert scanned textbooks, PDF books, technical slides, journal articles, and theses into a Markdown knowledge base with high-resolution images, tables, and renderable LaTeX formulas. Designed for AI assistants that support [Agent Skills](https://agentskills.io).

The workflow covers full KaTeX compilation, source-based figure recovery, multi-panel figure repairs, QR codes inside tables, OCR correction, assembly of long documents, safe cleanup of generated image assets, and Zotero metadata. Successful compilation does not establish content completeness: figures and scientific symbols still need to be checked against the source and verified in the target client.

## Installation and dependencies

### Option 1: Ask your AI assistant to install and configure it (recommended)

Copy this prompt into Claude Code, Codex, ZCode, Cursor, or another coding assistant to install the skill, configure dependencies, and validate the environment:

```text
Please install and configure the wusen-doc2md skill from:
https://github.com/luffysolution-svg/wusen-skills

1. Check Git, Node.js, and Python, then install the skill for the current AI assistant at user scope:
   npx skills add luffysolution-svg/wusen-skills --skill wusen-doc2md -g
   Select the target matching the current assistant. If already installed, check for local edits before updating.
2. Locate the installed wusen-doc2md directory reported by the installer and confirm that it contains SKILL.md, requirements.txt, and scripts/.
   Run the following commands from that skill directory, not the collection repository root.
3. Prefer a dedicated Python virtual environment and install the Python dependencies:
   python -m pip install -r requirements.txt
4. Install the Node.js dependencies for formula validation:
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
5. Use the configured Python environment to run checks and tests:
   python scripts/doctor.py
   python -m unittest discover -s tests -v
6. Check whether MINERU_API_TOKEN is configured without displaying its value.
   If missing, direct me to https://mineru.net and explain how to set it locally.
   If configured, run python scripts/doctor.py --require-ocr to check configuration status.
7. Report the installation path, validation results, and any remaining configuration steps.
```

### Option 2: Install from the command line

```bash
# Install this skill globally
npx skills add luffysolution-svg/wusen-skills --skill wusen-doc2md -g

# Install the entire collection globally
npx skills add luffysolution-svg/wusen-skills --skill '*' -g
```

Enter the installed `wusen-doc2md` directory containing `SKILL.md`. When working from the repository, enter `skills/wusen-doc2md`. All dependency and usage commands below run from that directory.

```bash
python -m pip install -r requirements.txt
npm --prefix scripts install --ignore-scripts --no-audit --no-fund
python scripts/doctor.py
python -m unittest discover -s tests -v
```

You can create a Python virtual environment before installing dependencies. `doctor.py` checks the runtime and credential configuration without installing dependencies or printing secret values. Use `--json` for structured output.

Cloud OCR requires `MINERU_API_TOKEN`, available from [mineru.net](https://mineru.net). Run `python scripts/doctor.py --require-ocr` to treat a missing token as a failure. Local assembly and validation remain available without a token when their dependencies are installed.

## Problems it addresses

Academic document conversion often encounters:

- **Broken formulas**: misplaced subscripts, superscripts, fractions, and Greek letters.
- **Incorrect reading order**: slide-style image placement applied to textbooks, or a page's opening sentence promoted to a heading.
- **Rendering conflicts**: explanatory brackets parsed as links, leaving raw formulas visible in Obsidian.
- **Damaged heading formulas**: removing math delimiters while leaving raw underscores, such as `CO_2`.
- **Unused image assets**: older crops accumulate after figure boundaries are adjusted.
- **Missing resources**: QR codes and complex table content disappear during text extraction.
- **OCR substitutions**: similar-looking characters, such as `ν` and `v` or `N₀` and `No`, are confused.
- **Model artifacts**: phrases such as “The Ground Truth image displays...” leak into the document.

The skill provides a workflow with full formula compilation and target-client verification. Its rules come from practical work on textbooks and academic literature.

## Three document workflows

| Aspect | Textbooks and monographs (`--mode book`) | Slides and lecture notes (`--mode slides`) | Research papers and theses |
| --- | --- | --- | --- |
| Organization | Chapter files, navigation, and images | Lectures or topics | One main note, navigation, and images per document |
| Reading order | Geometric flow; figures remain in context | Images placed prominently with supporting text | Source-checked OCR flow for two-column papers |
| Headings | Distinguish section numbers from exercises | Preserve each slide's main topic | Academic naming: `{{firstCreator}} - {{year}} - {{title}}.md` |
| Figures | Source PDF crops at least 3×; multi-panel grouping | Prefer native PPTX images | High-confidence panel merging and supporting information |
| Metadata | Preserve substantive footnotes | Compare against native slide text | YAML metadata including DOI, authors, abstract, and Zotero URI |

## Included files

```text
SKILL.md                        Skill definition, workflow, and constraints
references/
  mineru-api.md                 API contract, coordinates, and download fallback
  math-normalization.md        Formula rules and before/after examples
  book-workflow.md             Splitting and assembling long textbooks
  research-documents.md        Academic naming, metadata, and two-column workflow
  zotero-sync.md               Read-only API access and MCP-based synchronization
scripts/
  doctor.py                    Runtime and credential configuration checks
  mineru_ocr.py                Split, submit, poll, and download OCR tasks
  build_markdown.py            Textbook and slide assembly
  build_research_document.py   Research-document assembly
  normalize_math.py            Conservative LaTeX normalization
  extract_formulas.py          Formula extraction
  validate_output.py           Structure, resource, and artifact checks
  validate_math.cjs            Full KaTeX compilation
  preview_server.py            Markdown and KaTeX preview
examples/
  RESULTS.md                   Textbook and slide case studies
  RESEARCH-VALIDATION.md       Academic document case studies
  book-demo/                   Original two-page reproducible example
```

## Workflow

1. **Inspect the source**: check native text and embedded images, then choose the textbook, slide, or research workflow.
2. **Split and recognize**: split long PDFs and submit batches to MinerU when new OCR is needed.
3. **Cross-check text**: compare OCR with reliable native text and review substitutions.
4. **Recover figures**: extract native images or recrop the source PDF; verify panel and caption boundaries.
5. **Normalize formulas**: address delimiter conflicts, command boundaries, heading formulas, and chemical notation.
6. **Assemble the knowledge base**: write navigation, notes, and relative image references; optionally clean owned orphan assets.
7. **Validate the result**: compile every formula and verify rendering in the target client.

## Case studies

The repository documents the following practical cases:

| Case | Documents | Formulas | Images / figures | Reported results |
| --- | --- | ---: | ---: | --- |
| Materials characterization course | 10 chapters, 694 slide pages | 2,118 | 199 | All formulas compiled; 7 native-text corrections; image deduplication |
| Physical chemistry textbook | 556 scanned pages | 7,523 | 332 | 7,523/7,523 formulas compiled; 28 QR codes retained |
| Chinese research papers and theses | 5 documents, 611 pages, including a 240-page dissertation | 5,797 | 398 | Bracket conflicts and heading formulas repaired; 89 old image assets safely removed |
| English research literature | 7 papers, 87 pages | 876 | 114 | Two-column flow, long Windows paths, and YAML metadata |
| **Total** | **Nearly 2,000 pages** | **16,314** | **1,043** | **All formulas compiled in these reported cases** |

See [textbook and slide results](examples/RESULTS.md) and [research validation](examples/RESEARCH-VALIDATION.md). These detailed reports are currently in Chinese.

## Command reference

Run these commands from the skill directory. Replace input and output paths with the paths for your task.

```bash
# 1. Split a long PDF into 96-page chunks
python scripts/mineru_ocr.py split path/to/document.pdf --out-root ./ocr-work --chunk-size 96

# 2. Submit a batch to MinerU
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv

# 3. Assemble a textbook, retain table images, and clean owned orphan crops
python scripts/build_markdown.py \
    --raw ./ocr-work/raw \
    --out ./knowledge-base \
    --mode book \
    --manifest ./ocr-work/manifest.json \
    --chapters ./chapters.json \
    --pdf path/to/document.pdf \
    --keep-table-images \
    --prune-orphans

# 4. Assemble slides using native PPTX text and source figures
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./knowledge-base \
    --mode slides --source-dir ./slides --pdf-dir ./slide-pdfs

# 5. Assemble a research document with academic naming and metadata
python scripts/build_research_document.py --pdf ./source.pdf --raw ./ocr-work/raw/article \
    --metadata ./metadata.json --out "./research/First Author - Year - Title"

# 6. Validate structure and resources
python scripts/validate_output.py ./knowledge-base --images-dir images

# 7. Extract and compile every formula
python scripts/extract_formulas.py ./knowledge-base --out formulas.json
node scripts/validate_math.cjs formulas.json

# 8. Preview with local KaTeX assets
python scripts/preview_server.py ./knowledge-base --port 8766 --katex-dir scripts/node_modules/katex/dist
```

## Output conventions

- **Clean delivery**: navigation, Markdown, and images; build reports and incremental state stay outside the reading directory in `.<directory-name>.build/`.
- **Relative paths**: image references use `![](images/...)`, so the knowledge base can be moved between devices.
- **Scientific fidelity**: LaTeX formulas remain searchable; complex tables and chemical structures can retain source crops for verification.
- **Bracket handling**: explanatory brackets use full-width `［...］` where required to avoid Markdown parsing conflicts.
- **Navigation**: `00-目录.md` uses URI-encoded links compatible with Obsidian.

## License

[MIT](LICENSE)
