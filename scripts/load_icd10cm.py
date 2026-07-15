"""Load the official current ICD-10-CM tabular release into BigQuery.

This is an explicit maintenance command, not part of the Cloud Run request
path. It replaces the target table atomically with one versioned CDC release.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from xml.etree import ElementTree

from google.cloud import bigquery


DEFAULT_SOURCE_URL = (
    "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/"
    "ICD10CM/2026-update/icd10cm-April-1-2026-XML.zip"
)
DEFAULT_CODES_URL = (
    "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/"
    "ICD10CM/2026/icd10cm-Code%20Descriptions-2026.zip"
)
SOURCE_PAGE = (
    "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/"
    "ICD10CM/2026-update/"
)


def _text(element: ElementTree.Element, name: str) -> str:
    node = element.find(name)
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _diagnoses(
    element: ElementTree.Element,
    *,
    parent_code: Optional[str],
    chapter: str,
    version: str,
    effective_date: str,
) -> Iterable[Dict[str, object]]:
    for diagnosis in element.findall("diag"):
        code = _text(diagnosis, "name")
        description = _text(diagnosis, "desc")
        children = diagnosis.findall("diag")
        if code and description:
            yield {
                "code": code,
                "normalized_code": re.sub(r"[^A-Za-z0-9]", "", code).upper(),
                "description": description,
                "parent_code": parent_code,
                "chapter": chapter,
                "is_billable": not children,
                "version": version,
                "effective_date": effective_date,
                "source_url": SOURCE_PAGE,
            }
        yield from _diagnoses(
            diagnosis,
            parent_code=code or parent_code,
            chapter=chapter,
            version=version,
            effective_date=effective_date,
        )


def _base_order_codes(
    archive: Path, *, version: str, effective_date: str
) -> Dict[str, Dict[str, object]]:
    with zipfile.ZipFile(archive) as bundle:
        order_name = next(
            name
            for name in bundle.namelist()
            if "order-2026.txt" in name.lower()
        )
        rows: Dict[str, Dict[str, object]] = {}
        with bundle.open(order_name) as source:
            for raw_line in source:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                match = re.match(
                    r"^\d{5}\s+(\S+)\s+([01])\s+(.+?)\s{2,}(.+)$", line
                )
                if not match:
                    continue
                code, billable, short_description, description = match.groups()
                normalized = re.sub(r"[^A-Za-z0-9]", "", code).upper()
                rows[normalized] = {
                    "code": code,
                    "normalized_code": normalized,
                    "description": description.strip() or short_description.strip(),
                    "parent_code": None,
                    "chapter": "",
                    "is_billable": billable == "1",
                    "version": version,
                    "effective_date": effective_date,
                    "source_url": SOURCE_PAGE,
                }
    return rows


def parse_release(
    archive: Path,
    base_codes_archive: Path,
    *,
    version: str,
    effective_date: str,
) -> List[Dict[str, object]]:
    with zipfile.ZipFile(archive) as bundle:
        tabular_name = next(
            name
            for name in bundle.namelist()
            if "tabular" in name.lower() and name.lower().endswith(".xml")
        )
        with bundle.open(tabular_name) as source:
            root = ElementTree.parse(source).getroot()

    rows = _base_order_codes(
        base_codes_archive,
        version=version,
        effective_date=effective_date,
    )
    for chapter in root.findall("chapter"):
        chapter_name = _text(chapter, "name")
        for section in chapter.findall("section"):
            for row in _diagnoses(
                section,
                parent_code=None,
                chapter=chapter_name,
                version=version,
                effective_date=effective_date,
            ):
                normalized = str(row["normalized_code"])
                if normalized in rows:
                    # Preserve the order file's authoritative billable flag,
                    # while applying April descriptions and hierarchy metadata.
                    rows[normalized].update(
                        {
                            "code": row["code"],
                            "description": row["description"],
                            "parent_code": row["parent_code"],
                            "chapter": row["chapter"],
                        }
                    )
                else:
                    rows[normalized] = row
    return sorted(rows.values(), key=lambda row: str(row["normalized_code"]))


def load_release(
    *,
    project: str,
    dataset: str,
    table: str,
    location: str,
    source_url: str,
    base_codes_url: str,
    version: str,
    effective_date: str,
) -> int:
    client = bigquery.Client(project=project, location=location)
    dataset_id = f"{project}.{dataset}"
    client.create_dataset(
        bigquery.Dataset(dataset_id), exists_ok=True, timeout=30
    )

    with tempfile.TemporaryDirectory(prefix="icd10cm-") as temp_dir:
        archive = Path(temp_dir) / "release.zip"
        base_codes_archive = Path(temp_dir) / "base-codes.zip"
        urllib.request.urlretrieve(source_url, archive)
        urllib.request.urlretrieve(base_codes_url, base_codes_archive)
        rows = parse_release(
            archive,
            base_codes_archive,
            version=version,
            effective_date=effective_date,
        )
        payload = Path(temp_dir) / "icd10cm.ndjson"
        with payload.open("w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, separators=(",", ":")) + "\n")

        schema = [
            bigquery.SchemaField("code", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("normalized_code", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("description", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("parent_code", "STRING"),
            bigquery.SchemaField("chapter", "STRING"),
            bigquery.SchemaField("is_billable", "BOOLEAN", mode="REQUIRED"),
            bigquery.SchemaField("version", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("effective_date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("source_url", "STRING", mode="REQUIRED"),
        ]
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        with payload.open("rb") as source:
            job = client.load_table_from_file(
                source,
                f"{dataset_id}.{table}",
                job_config=job_config,
                location=location,
            )
            job.result()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="avinia-app")
    parser.add_argument("--dataset", default="medical_terminology")
    parser.add_argument("--table", default="icd10cm_2026")
    parser.add_argument("--location", default="US")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--base-codes-url", default=DEFAULT_CODES_URL)
    parser.add_argument("--version", default="2026")
    parser.add_argument("--effective-date", default="2026-04-01")
    args = parser.parse_args()
    count = load_release(
        project=args.project,
        dataset=args.dataset,
        table=args.table,
        location=args.location,
        source_url=args.source_url,
        base_codes_url=args.base_codes_url,
        version=args.version,
        effective_date=args.effective_date,
    )
    print(f"Loaded {count} ICD-10-CM concepts")


if __name__ == "__main__":
    main()
