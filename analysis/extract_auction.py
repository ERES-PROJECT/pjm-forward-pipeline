
#!/usr/bin/env python3
"""
Evidence-first extractor for Pennsylvania default-service auction PDFs.

Project structure:
    pjm-forward-pipeline/
        analysis/
            extract_auction.py
        data/
            Auction Date/
                DUQ/
                FE/
                PECO/
                PPL/

Run from project root:
    python3 analysis/extract_auction.py

Optional:
    python3 analysis/extract_auction.py --render

Requires:
    python3 -m pip install pymupdf

This program extracts PDF text, tables, dates, and source information.
It does NOT automatically treat every detected number as a winning price.
"""

import argparse
import csv
import re
import json
import hashlib
from pathlib import Path

import fitz


# --------------------------------------------------
# Project paths
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INPUT_DIR = PROJECT_ROOT / "data" / "Auction Date"

OUTPUT_DIR = PROJECT_ROOT / "analysis" / "output" / "auction"


# --------------------------------------------------
# Output fields
# --------------------------------------------------

FIELDS = [
    "Source_PDF",
    "SHA256",
    "EDC_Group",
    "Page",
    "Table",
    "Row",
    "Cells_JSON",
    "Page_Text_File",
    "Needs_Manual_Verification"
]


# --------------------------------------------------
# Patterns
# --------------------------------------------------

DATE_PAT = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+20\d{2}\b",
    re.I
)

PRICE_PAT = re.compile(
    r"(?:winning bid price|average price|weighted average|"
    r"fixed.price|price \(\$/mwh\)|\$/mwh|\$ / mwh)",
    re.I
)


# --------------------------------------------------
# Save CSV
# --------------------------------------------------

def save_csv(path, fields, rows):

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(
        path,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore"
        )

        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------
# Identify EDC group
# --------------------------------------------------

def group(name):

    parts = [p.upper() for p in Path(name).parts]

    for g in ["PECO", "PPL", "DUQ", "FE"]:
        if g in parts:
            return g

    return "OTHER"


# --------------------------------------------------
# Find all PDF files
# --------------------------------------------------

def find_pdf_files(input_dir):

    pdf_files = []

    for path in input_dir.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() != ".pdf":
            continue

        if "__MACOSX" in path.parts:
            continue

        if path.name.startswith("._"):
            continue

        pdf_files.append(path)

    return sorted(pdf_files)


# --------------------------------------------------
# Main extraction
# --------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Extract auction PDF evidence"
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=INPUT_DIR,
        help="Folder containing auction PDFs"
    )

    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT_DIR,
        help="Output directory"
    )

    parser.add_argument(
        "--render",
        action="store_true",
        help="Render relevant PDF pages as PNG"
    )

    args = parser.parse_args()

    input_dir = args.input.resolve()
    out = args.out.resolve()

    if not input_dir.exists():
        print(f"ERROR: Input folder not found: {input_dir}")
        return

    out.mkdir(parents=True, exist_ok=True)

    texts = out / "pdf_text"
    texts.mkdir(exist_ok=True)

    imgs = out / "page_images"

    if args.render:
        imgs.mkdir(exist_ok=True)

    inventory = []
    tables = []
    evidence = []
    issues = []

    pdf_files = find_pdf_files(input_dir)

    print(f"Found {len(pdf_files)} PDF files")
    print(f"Input: {input_dir}")
    print(f"Output: {out}")
    print("-" * 60)

    if not pdf_files:
        print("No PDF files found.")
        return

    # ----------------------------------------------
    # Process every PDF
    # ----------------------------------------------

    for i, pdf_path in enumerate(pdf_files, 1):

        name = str(pdf_path.relative_to(input_dir))

        g = group(name)

        count = 0
        found = 0
        status = "OK"
        note = ""
        sha = ""
        pages = 0

        textfile = None

        try:

            raw = pdf_path.read_bytes()

            sha = hashlib.sha256(raw).hexdigest()

            label = (
                f"{i:03d}_"
                f"{pdf_path.stem[:75]}_"
                f"{sha[:8]}"
            )

            textfile = texts / (label + ".txt")

            with fitz.open(
                stream=raw,
                filetype="pdf"
            ) as doc:

                pages = len(doc)

                with open(
                    textfile,
                    "w",
                    encoding="utf-8"
                ) as f:

                    for pno, page in enumerate(doc, 1):

                        # Extract original page text
                        t = page.get_text(
                            "text",
                            sort=True
                        )

                        f.write(
                            f"\n===== PAGE {pno} =====\n"
                        )

                        f.write(t)
                        f.write("\n")

                        dates = DATE_PAT.findall(t)

                        has_price = bool(
                            PRICE_PAT.search(t)
                        )

                        has_bid_date = (
                            "bid date" in t.lower()
                        )

                        has_completed = (
                            "completed" in t.lower()
                        )

                        # Save relevant page evidence
                        if (
                            has_price
                            or has_bid_date
                            or has_completed
                        ):

                            found += 1

                            evidence.append({
                                "Source_PDF": name,
                                "Page": pno,
                                "Date_Mentions": " | ".join(dates),
                                "Has_Price_Keywords": has_price,
                                "Has_Bid_Date": has_bid_date,
                                "Has_Completed": has_completed,
                                "Page_Text_File": str(textfile)
                            })

                            # Optional page rendering
                            if args.render:

                                pix = page.get_pixmap(
                                    matrix=fitz.Matrix(1.5, 1.5),
                                    alpha=False
                                )

                                image_path = (
                                    imgs /
                                    f"{label}_p{pno}.png"
                                )

                                pix.save(str(image_path))

                        # Extract tables
                        try:

                            found_tables = (
                                page.find_tables().tables
                            )

                        except Exception:

                            found_tables = []

                        for ti, tab in enumerate(
                            found_tables, 1
                        ):

                            try:
                                extracted_rows = tab.extract()
                            except Exception:
                                continue

                            for ri, cells in enumerate(
                                extracted_rows, 1
                            ):

                                cells = [
                                    str(c).strip()
                                    if c is not None
                                    else ""
                                    for c in cells
                                ]

                                if not any(cells):
                                    continue

                                count += 1

                                tables.append({
                                    "Source_PDF": name,
                                    "SHA256": sha,
                                    "EDC_Group": g,
                                    "Page": pno,
                                    "Table": ti,
                                    "Row": ri,
                                    "Cells_JSON": json.dumps(
                                        cells,
                                        ensure_ascii=False
                                    ),
                                    "Page_Text_File": str(textfile),
                                    "Needs_Manual_Verification": "YES"
                                })

            if count == 0:

                note = (
                    "No machine-detected tables; "
                    "inspect saved PDF text"
                )

            if found == 0:

                note += (
                    "; " if note else ""
                ) + "No date/price keyword pages detected"

        except Exception as ex:

            status = "ERROR"
            note = str(ex)

            issues.append({
                "Source_PDF": name,
                "Issue": note
            })

        # ------------------------------------------
        # Inventory
        # ------------------------------------------

        inventory.append({
            "Source_PDF": name,
            "EDC_Group": g,
            "SHA256": sha,
            "Total_Pages": pages,
            "Pages_With_Evidence": found,
            "Detected_Table_Rows": count,
            "Status": status,
            "Note": note,
            "Text_File": str(textfile) if textfile else ""
        })

        print(
            f"[{i}/{len(pdf_files)}] "
            f"{g} "
            f"pages={pages} "
            f"evidence={found} "
            f"table_rows={count} "
            f"status={status}: "
            f"{pdf_path.name}"
        )

    # ----------------------------------------------
    # Save all results
    # ----------------------------------------------

    save_csv(
        out / "01_pdf_inventory.csv",
        [
            "Source_PDF",
            "EDC_Group",
            "SHA256",
            "Total_Pages",
            "Pages_With_Evidence",
            "Detected_Table_Rows",
            "Status",
            "Note",
            "Text_File"
        ],
        inventory
    )

    save_csv(
        out / "02_raw_table_rows_REVIEW.csv",
        FIELDS,
        tables
    )

    save_csv(
        out / "03_page_evidence.csv",
        [
            "Source_PDF",
            "Page",
            "Date_Mentions",
            "Has_Price_Keywords",
            "Has_Bid_Date",
            "Has_Completed",
            "Page_Text_File"
        ],
        evidence
    )

    save_csv(
        out / "04_errors.csv",
        [
            "Source_PDF",
            "Issue"
        ],
        issues
    )

    # ----------------------------------------------
    # Summary
    # ----------------------------------------------

    success = sum(
        1 for row in inventory
        if row["Status"] == "OK"
    )

    print("\n" + "=" * 60)

    print("EXTRACTION COMPLETE")

    print(f"Total PDFs: {len(pdf_files)}")
    print(f"Successfully read: {success}")
    print(f"Errors: {len(issues)}")
    print(f"Raw table rows: {len(tables)}")
    print(f"Pages with evidence: {len(evidence)}")

    print(f"\nOutput directory:\n{out}")

    print("\nIMPORTANT:")
    print(
        "Extracted table rows are NOT automatically "
        "verified auction winning prices."
    )

    print(
        "Auction dates, delivery periods, products, "
        "and prices must be validated against "
        "the original PDF evidence."
    )


if __name__ == "__main__":
    main()
