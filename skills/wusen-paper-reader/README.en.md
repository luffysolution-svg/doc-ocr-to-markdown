<div align="center">

# wusen-paper-reader

**Read papers, trace evidence, and build reusable research knowledge in materials science and chemistry.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.md) · **English**

[Installation](#installation) · [Scenarios](#scenarios) · [Domain checks](#materials-and-chemistry) · [Validation](#validation-and-limits)

</div>

Interpret papers, supporting information, Zotero attachments, and existing Markdown through single-paper reading, cross-paper comparison, interdisciplinary transfer, cross-topic synthesis, and evidence-grounded library questions. Preserve experimental conditions, uncertainties, and the distinction between reported findings and new hypotheses.

## Installation

### Ask your AI assistant to install it (recommended)

Copy this prompt:

```text
Please install and configure the wusen-paper-reader skill:
https://github.com/luffysolution-svg/wusen-skills

1. Check Git and Node.js, then install at user scope for the current assistant:
   npx skills add luffysolution-svg/wusen-skills --skill wusen-paper-reader -g
   Select the correct assistant target and check existing local edits before updating.
2. Read SKILL.md and README.en.md in the installed skill directory.
3. The current assistant performs the reading. No separate model API or deep-research skill is required. Optional offline evidence tools need Python 3.10+ and no third-party packages.
4. From the directory containing SKILL.md, run:
   python -m unittest discover -s tests -v
   python scripts/evidence_tools.py --help
5. Read provided Markdown directly. Check the host's PDF reader for PDFs and its Zotero connection for library items. Configure an OCR tool only when scanned documents need it.
6. Do not display credentials. Report the installation path, test results, and available optional reading integrations.
```

### Command-line installation

```bash
npx skills add luffysolution-svg/wusen-skills --skill wusen-paper-reader -g
```

The reading instructions require no Python packages. Bundled tools use the Python 3.10+ standard library. PDF readers, OCR, and Zotero are optional host integrations. For an unpublished local checkout, copy or link the complete `skills/wusen-paper-reader` directory into your assistant's skill directory; remote installation only retrieves published versions.

## Scenarios

| Scenario | Example request | Result |
| --- | --- | --- |
| Single paper | Explain the evidence in Figures 4 and 5 of this photocatalysis paper; distinguish observations from mechanisms. | Claims, evidence, methodological gaps, research implications |
| Cross-paper | Compare MatterGen and GNoME by stability definitions, reference hulls, and experimental verification. | Condition-aware comparison and disagreements |
| Cross-discipline | Transfer crystal generation methods to catalyst design and identify assumptions that do not transfer. | Concept mapping, limitations, validation plan |
| Cross-topic | Connect crystal generation, defect engineering, and photothermal catalysis around a research question. | Supported connections, gaps, reading priorities |
| Library questions | Which of my papers support or qualify the claim that defects improve hydrogen production? | Traceable answer with search coverage |

A focused question can receive a focused answer. Full reading notes and evidence ledgers are appropriate for deeper work and ongoing knowledge accumulation.

## Materials and chemistry

Five domain references are loaded only when relevant:

- **Synthesis and characterization:** composition, phases, processing, sample identity, and the limits of structural evidence.
- **Catalysis and electrochemistry:** illumination, wavelength, medium, temperature, loading, electrode reference, normalization, controls, and thermal effects.
- **DFT and computation:** functional, spin/U, k-points, convergence, defects/surfaces, energy references, and free-energy corrections. Computed stability does not establish synthesizability.
- **Spectroscopy and mechanisms:** assignments, fitting assumptions, controls, timescales, operando conditions, and alternative explanations.
- **Performance comparison:** metric definitions, units, conditions, uncertainty, and validation levels. Materials ML also requires checking dataset splits, leakage, reference datasets, and denominators.

Instructions and references are currently written in Chinese; the assistant can apply them in the requested output language. Scenarios and domain checks are separate layers rather than one fixed summary template.

## Offline evidence tools

From the skill directory:

```bash
python scripts/evidence_tools.py inventory ./sources --out ./work/sources.json
python scripts/evidence_tools.py check ./work/evidence.json --root ./sources --out ./work/check.json
python -m unittest discover -s tests -v
```

`inventory` records Markdown paths, hashes, titles, and headings. After reading, populate the evidence ledger using the [evidence contract](references/evidence-contract.md). `check` validates structure, path boundaries, source hashes, and quoted text. It makes no network or model calls. Conflicting existing outputs are rejected; use new filenames for revisions.

## Document and library integration

Existing [wusen-doc2md](https://github.com/luffysolution-svg/wusen-skills/blob/main/skills/wusen-doc2md/README.en.md) output can be reused, but that skill is not required. Native PDF text is only a reading aid: formulas, tables, column order, and figure content may require source-page inspection. Record unavailable supporting information instead of inventing missing parameters.

Use supported Zotero integrations or verified read-only export interfaces. Do not access the database or scan storage directories. Reading tests do not modify the library; authorized writes require verification and read-back.

## Validation and limits

See [evaluation cases and coverage](evals/README.md). Initial domain development focuses on materials science and chemistry; it does not imply comprehensive validation across subfields.

Mechanical checks do not validate page numbers, figure identifiers, semantic support, or scientific correctness. Separate author-reported findings, assistant interpretations, and hypotheses. Answers from a personal library are not automatically systematic reviews.

## License

[MIT](LICENSE). Source PDFs, private libraries, and personal annotations are not distributed with the skill.
