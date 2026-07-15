"""DDInter 2.0 drug-drug interaction database.

Loads DDInter CSV files into SQLite for fast interaction checking.
Drug names are stored as-is; RxCUI mapping happens at query time via RxNorm.
"""

import csv
import os
import sqlite3
from typing import Any, Dict, Optional


class DDInterDatabase:
    """SQLite-backed drug interaction checker using DDInter 2.0 data."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Default: in-memory if no data files, else ship with container
            db_path = os.environ.get("DDINTER_DB_PATH", ":memory:")

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

        # If in-memory, load from CSVs automatically
        if db_path == ":memory:":
            self._load_from_csvs()

    def _create_tables(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS drug_interactions (
                drug_a TEXT NOT NULL,
                drug_b TEXT NOT NULL,
                severity TEXT NOT NULL,
                ddinter_id_a TEXT,
                ddinter_id_b TEXT,
                PRIMARY KEY (drug_a, drug_b)
            );

            CREATE INDEX IF NOT EXISTS idx_drug_a ON drug_interactions(drug_a);
            CREATE INDEX IF NOT EXISTS idx_drug_b ON drug_interactions(drug_b);
            """
        )
        self._conn.commit()

    def _load_from_csvs(self):
        """Load all DDInter CSV files from data/ddinter/ directory."""
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ddinter")
        if not os.path.exists(data_dir):
            return

        count = 0
        for filename in sorted(os.listdir(data_dir)):
            if not filename.endswith(".csv"):
                continue
            filepath = os.path.join(data_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    drug_a = row.get("Drug_A", "").strip().lower()
                    drug_b = row.get("Drug_B", "").strip().lower()
                    severity = row.get("Level", "").strip()
                    if not drug_a or not drug_b or not severity:
                        continue
                    # Store both directions for easy lookup
                    self._conn.execute(
                        "INSERT OR REPLACE INTO drug_interactions VALUES (?, ?, ?, ?, ?)",
                        (drug_a, drug_b, severity, row.get("DDInterID_A", ""), row.get("DDInterID_B", "")),
                    )
                    self._conn.execute(
                        "INSERT OR REPLACE INTO drug_interactions VALUES (?, ?, ?, ?, ?)",
                        (drug_b, drug_a, severity, row.get("DDInterID_B", ""), row.get("DDInterID_A", "")),
                    )
                    count += 1
        self._conn.commit()

    def check_interaction(self, drug_a: str, drug_b: str) -> Optional[Dict[str, Any]]:
        """Check if two drugs interact.

        Args:
            drug_a: Drug name (generic or brand, case-insensitive)
            drug_b: Drug name (generic or brand, case-insensitive)

        Returns:
            Dict with severity and drug names, or None if no interaction found.
        """
        a = drug_a.strip().lower()
        b = drug_b.strip().lower()

        # Direct name match
        row = self._conn.execute(
            "SELECT * FROM drug_interactions WHERE drug_a = ? AND drug_b = ?",
            (a, b),
        ).fetchone()

        if row:
            return {
                "drug_a": drug_a,
                "drug_b": drug_b,
                "severity": row["severity"],
                "ddinter_id_a": row["ddinter_id_a"],
                "ddinter_id_b": row["ddinter_id_b"],
                "description": f"{drug_a} and {drug_b} may interact. Severity: {row['severity']}.",
            }

        return None

    def close(self):
        self._conn.close()
