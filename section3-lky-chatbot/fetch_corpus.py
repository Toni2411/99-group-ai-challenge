"""Download speech transcripts from the National Archives of Singapore.

Run:  python fetch_corpus.py                      # ~24 speeches spread over 1965-1990
      python fetch_corpus.py --per-year 4 --years 1965 1966 1967
      python fetch_corpus.py --target 40

NAS publishes Lee Kuan Yew's speech transcripts as text PDFs at a predictable
path:

    https://www.nas.gov.sg/archivesonline/data/pdfdoc/lky{YYYYMMDD}{suffix}.pdf

where suffix is empty, or a/b/c when he spoke more than once that day. There is
no public index API, so this probes candidate dates and keeps what exists.

Two things the script does deliberately:

  Spread across years, not a sequential crawl. Taking the first N hits from
  1965 would produce a corpus that only knows about separation from Malaysia.
  Sampling a few speeches per year across the whole period gives the retriever
  something to say about housing, language policy, foreign relations and
  succession as well.

  Rate limiting. This is a public government archive being probed for records
  that mostly do not exist, so requests are serialised with a delay and the
  default target is small. Raise --target if you need more; do not remove the
  delay.

These transcripts are public records. Attribution travels with every document
into the header block that ingest.py reads.
"""

import argparse
import random
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from pypdf import PdfReader

import config

BASE = "https://www.nas.gov.sg/archivesonline/data/pdfdoc"
SUFFIXES = ["", "a", "b", "c"]

# Junk that survives PDF extraction: the original .doc path the typist used,
# and bare page numbers on their own line.
ARTIFACT_RE = re.compile(r"^\s*(lky[\\/][\d\\/a-z.]+|\d{1,3})\s*$", re.IGNORECASE)


def is_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def extract(pdf_bytes: bytes) -> tuple[str, str]:
    """Return (title, body) from a transcript PDF."""
    import io

    reader = PdfReader(io.BytesIO(pdf_bytes))
    raw = "\n".join((page.extract_text() or "") for page in reader.pages)

    lines = [ln.rstrip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if not ARTIFACT_RE.match(ln)]

    # The transcript opens with an all-caps header block naming the speech.
    title_parts: list[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        if not line.strip():
            if title_parts:
                body_start = i
                break
            continue
        if is_heading(line):
            title_parts.append(line.strip())
            body_start = i + 1
        elif title_parts:
            body_start = i
            break

    title = re.sub(r"\s+", " ", " ".join(title_parts)).strip(" .,")
    body = "\n".join(lines[body_start:]).strip()
    return title or "Untitled speech", body


def save(out_dir: Path, day: date, suffix: str, title: str, body: str) -> Path:
    slug = f"lky{day:%Y%m%d}{suffix}"
    url = f"{BASE}/{slug}.pdf"
    header = (
        "---\n"
        f"title: {title[:180]}\n"
        f"year: {day.year}\n"
        f"date: {day:%Y-%m-%d}\n"
        "type: speech\n"
        "source: National Archives of Singapore\n"
        f"url: {url}\n"
        "---\n\n"
    )
    path = out_dir / f"{slug}.txt"
    path.write_text(header + body, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+",
                        default=list(range(1965, 1991)))
    parser.add_argument("--per-year", type=int, default=1,
                        help="documents to keep per year")
    parser.add_argument("--target", type=int, default=24,
                        help="stop once this many documents exist on disk")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds between requests; do not set to 0")
    parser.add_argument("--min-chars", type=int, default=2000,
                        help="skip stubs and cover sheets")
    args = parser.parse_args()

    out_dir = config.CORPUS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    have = {p.stem for p in out_dir.glob("*.txt")}
    print(f"Corpus dir: {out_dir}  ({len(have)} documents already)\n")

    session = requests.Session()
    session.headers["User-Agent"] = (
        "99-challenge-research/1.0 (educational assessment; low volume)")

    rng = random.Random(42)  # reproducible corpus across runs
    kept_total = len(have)

    for year in args.years:
        if kept_total >= args.target:
            break

        start = date(year, 1, 1)
        days = [start + timedelta(days=i)
                for i in range((date(year, 12, 31) - start).days + 1)]
        rng.shuffle(days)

        kept_year, probed = 0, 0
        for day in days:
            if kept_year >= args.per_year or kept_total >= args.target:
                break
            if probed > 120:  # a lean year; move on rather than grind
                break

            for suffix in SUFFIXES:
                slug = f"lky{day:%Y%m%d}{suffix}"
                if slug in have:
                    continue

                probed += 1
                time.sleep(args.delay)
                try:
                    response = session.get(f"{BASE}/{slug}.pdf", timeout=45)
                except requests.RequestException:
                    continue

                if response.status_code != 200:
                    continue
                if "pdf" not in response.headers.get("content-type", ""):
                    continue

                try:
                    title, body = extract(response.content)
                except Exception:
                    continue

                if len(body) < args.min_chars:
                    continue

                path = save(out_dir, day, suffix, title, body)
                kept_year += 1
                kept_total += 1
                print(f"  {year}  {path.name}  {len(body):>6,} chars  "
                      f"{title[:60]}")
                break  # one speech per date is plenty of variety

        if kept_year == 0:
            print(f"  {year}  (nothing found in {probed} probes)")

    print(f"\n{kept_total} documents in {out_dir}")
    print("Next: python ingest.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
