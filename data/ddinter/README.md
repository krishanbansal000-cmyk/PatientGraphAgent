# DDInter Data

CSV files placed in this directory are used by `assistant/ddinter.py` for the
prototype drug-interaction lookup. They are intentionally excluded from this
public repository and must be acquired and provisioned separately.

- Source: DDInter 2.0, https://ddinter.scbdd.com/
- Scope: interaction pairs grouped by the downloaded DDInter code files
- Runtime behavior: the files are loaded into an in-memory SQLite database

Confirm DDInter's current access, redistribution, and attribution terms before
downloading the data or distributing a container that includes it. Do not
treat a missing row as proof that two drugs do not interact.
