"""Supporting Information (SI) High-Resolution Figure Extractor.

Extracts all supplementary figures (e.g. Figure S1 to Figure S36) from multi-page
Supporting Information PDFs, saving them as high-fidelity individual images with
an accompanying manifest mapping captions and pages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


class FigureExtractError(Exception):
    pass


def extract_si_figures(
    pdf_path: Path,
    out_dir: Path,
    figure_numbers: Optional[List[int]] = None,
    dpi: int = 300
) -> List[Dict[str, Any]]:
    """Scan the SI PDF, locate Figure S captions, and extract individual high-res figures."""
    if not HAS_PYMUPDF:
        raise FigureExtractError("PyMuPDF (fitz) is required for SI figure extraction.")

    pdf_file = Path(pdf_path).resolve()
    if not pdf_file.exists():
        raise FigureExtractError(f"SI PDF not found: {pdf_file}")

    target_dir = Path(out_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_file)
    extracted: List[Dict[str, Any]] = []

    # 1. Map figure numbers to pages and captions
    fig_map: Dict[int, Dict[str, Any]] = {}
    pattern = re.compile(r"Figure\s+S(\d+)[.:\s\-]+([^\n]+)", re.IGNORECASE)

    for p_no in range(len(doc)):
        page = doc[p_no]
        text = page.get_text("text")
        matches = pattern.findall(text)
        for m in matches:
            f_num = int(m[0])
            caption = m[1].strip()
            if f_num not in fig_map:
                fig_map[f_num] = {
                    "page_idx": p_no,
                    "page_num": p_no + 1,
                    "caption": caption
                }

    target_nums = figure_numbers if figure_numbers else sorted(fig_map.keys())

    for f_num in target_nums:
        if f_num not in fig_map:
            continue
        meta = fig_map[f_num]
        p_idx = meta["page_idx"]
        page = doc[p_idx]

        images = page.get_images()
        filename = f"Figure_S{f_num:02d}.png"
        out_path = target_dir / filename

        saved = False
        width, height = 0, 0

        # Preference 1: Extract embedded image stream if available
        if images:
            # find largest image on the page
            best_img = None
            max_size = 0
            for img_info in images:
                xref = img_info[0]
                base_img = doc.extract_image(xref)
                if len(base_img["image"]) > max_size:
                    max_size = len(base_img["image"])
                    best_img = base_img

            if best_img and max_size > 5000:
                width = best_img["width"]
                height = best_img["height"]
                ext = best_img["ext"]
                # Save image bytes directly
                if ext.lower() == "png":
                    out_path.write_bytes(best_img["image"])
                else:
                    # convert/normalize to PNG using fitz Pixmap
                    pix = fitz.Pixmap(doc, img_info[0])
                    if pix.n >= 5:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(str(out_path))
                saved = True

        # Preference 2: Render high-resolution page crop if no embedded image stream
        if not saved:
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            pix.save(str(out_path))
            width = pix.width
            height = pix.height
            saved = True

        extracted.append({
            "figure_id": f"Figure S{f_num}",
            "figure_number": f_num,
            "filename": filename,
            "path": str(out_path),
            "relative_path": out_path.name,
            "page": meta["page_num"],
            "caption": meta["caption"],
            "width": width,
            "height": height
        })

    doc.close()

    # Save manifest
    manifest_path = target_dir / "si_figures_manifest.json"
    manifest_path.write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")

    return extracted


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract SI Figures to standalone high-res images")
    parser.add_argument("pdf_path", help="Path to Supporting Information PDF")
    parser.add_argument("--out-dir", required=True, help="Directory to save extracted figures")
    parser.add_argument("--figures", help="Comma-separated figure numbers (e.g. 1,2,4,10)")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for fallback rendering (default: 300)")

    args = parser.parse_args(argv)

    try:
        f_nums = [int(n.strip()) for n in args.figures.split(",") if n.strip()] if args.figures else None
        res = extract_si_figures(
            pdf_path=Path(args.pdf_path),
            out_dir=Path(args.out_dir),
            figure_numbers=f_nums,
            dpi=args.dpi
        )
        print(f"Successfully extracted {len(res)} figures to {args.out_dir}")
        for r in res[:5]:
            print(f"- {r['figure_id']} (p.{r['page']}): {r['filename']} ({r['width']}x{r['height']})")
        if len(res) > 5:
            print(f"... and {len(res) - 5} more figures.")
        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
