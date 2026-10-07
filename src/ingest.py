"""Download the raw PEP source files listed in data/pep_index.json.

Each PEP is fetched from the official python/peps GitHub repository (the same
source that peps.python.org is rendered from) and saved verbatim into
data/raw/peps/. Files that already exist are skipped, so the command is cheap
to re-run and the build works offline once the files are present.

Usage:
    python -m src.ingest                 # fetch every PEP in data/pep_index.json
    python -m src.ingest 484 544         # fetch specific PEP numbers
    python -m src.ingest --verify        # compare index entries with PEP headers
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

# --- Locations -------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "data" / "pep_index.json"
RAW_DIR = REPO_ROOT / "data" / "raw" / "peps"

# Newer PEPs are reStructuredText (.rst); a few very old ones were plain text.
URL_TEMPLATE = "https://raw.githubusercontent.com/python/peps/main/peps/pep-{num:04d}.{ext}"
EXTENSIONS = ("rst", "txt")

TIMEOUT_SECONDS = 20


class IngestError(Exception):
    """Raised when a PEP cannot be downloaded."""


def local_path(number: int) -> Path | None:
    """Return the path of an already-downloaded PEP file, or None."""
    for ext in EXTENSIONS:
        path = RAW_DIR / f"pep-{number:04d}.{ext}"
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def fetch_pep(number: int, session: requests.Session) -> tuple[Path, bool]:
    """Download one PEP. Returns (path, downloaded_now).

    Tries .rst first, then .txt. Raises IngestError with a readable message on
    network failures or when neither file exists upstream.
    """
    existing = local_path(number)
    if existing is not None:
        return existing, False

    tried = []
    for ext in EXTENSIONS:
        url = URL_TEMPLATE.format(num=number, ext=ext)
        tried.append(url)
        try:
            resp = session.get(url, timeout=TIMEOUT_SECONDS)
        except requests.Timeout:
            raise IngestError(f"PEP {number}: timed out after {TIMEOUT_SECONDS}s fetching {url}")
        except requests.RequestException as exc:
            raise IngestError(f"PEP {number}: network error fetching {url}: {exc}")

        if resp.status_code == 404:
            continue  # try the next extension
        if resp.status_code != 200:
            raise IngestError(f"PEP {number}: HTTP {resp.status_code} from {url}")

        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path = RAW_DIR / f"pep-{number:04d}.{ext}"
        path.write_text(resp.text, encoding="utf-8")
        return path, True

    raise IngestError(f"PEP {number}: not found upstream (tried {', '.join(tried)})")


def load_index_numbers() -> list[int]:
    """Read the list of PEP numbers from data/pep_index.json."""
    if not INDEX_PATH.exists():
        raise IngestError(f"Index file missing: {INDEX_PATH}")
    entries = json.loads(INDEX_PATH.read_text(encoding="utf-8"))["peps"]
    return [int(e["number"]) for e in entries]


def ingest(numbers: list[int]) -> list[str]:
    """Fetch every PEP in `numbers`. Returns a list of error messages (empty = success)."""
    errors = []
    with requests.Session() as session:
        for number in numbers:
            try:
                path, fresh = fetch_pep(number, session)
                status = "downloaded" if fresh else "already present"
                print(f"  PEP {number:>4}: {status:16} {path.relative_to(REPO_ROOT)}")
            except IngestError as exc:
                print(f"  ERROR {exc}", file=sys.stderr)
                errors.append(str(exc))
    return errors


# --- Verification ----------------------------------------------------------
# A tiny header reader used only to cross-check the index against the source.
# The full header parser lives in src/extract.py.

_HEADER_LINE = re.compile(r"^([A-Za-z][A-Za-z-]*):\s*(.*)$")


def read_header(path: Path) -> dict[str, str]:
    """Return the `Field: value` header block (stops at the first blank line)."""
    fields: dict[str, str] = {}
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            break
        m = _HEADER_LINE.match(line)
        if m:
            last = m.group(1)
            fields[last] = m.group(2).strip()
        elif last and line[:1].isspace():  # continuation line
            fields[last] += " " + line.strip()
    return fields


def verify() -> int:
    """Compare title/status/type/python_version in the index with each PEP header."""
    entries = json.loads(INDEX_PATH.read_text(encoding="utf-8"))["peps"]
    mismatches = 0
    checks = [("title", "Title"), ("status", "Status"), ("type", "Type"),
              ("python_version", "Python-Version")]
    for e in entries:
        path = local_path(int(e["number"]))
        if path is None:
            print(f"  PEP {e['number']}: not downloaded yet")
            mismatches += 1
            continue
        header = read_header(path)
        for key, field in checks:
            # Titles in headers can carry RST markup (``X | Y``, \*\*kwargs);
            # the index stores the plain-text form, so strip it before comparing.
            want = e.get(key) or ""
            got = re.sub(r"``|\\", "", header.get(field, ""))
            if want != got:
                mismatches += 1
                print(f"  PEP {e['number']}: {key} index={want!r} header={got!r}")
    print(f"Verified {len(entries)} entries, {mismatches} mismatch(es).")
    return mismatches


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return 1 if verify() else 0
    try:
        numbers = [int(a) for a in argv] if argv else load_index_numbers()
    except (IngestError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    print(f"Fetching {len(numbers)} PEP(s) into {RAW_DIR.relative_to(REPO_ROOT)}/")
    errors = ingest(numbers)
    if errors:
        print(f"{len(errors)} PEP(s) failed.", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
