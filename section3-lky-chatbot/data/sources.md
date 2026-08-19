# Corpus and provenance

The index is built from publicly available Lee Kuan Yew material. Each file in
`corpus/` carries a header recording where it came from, so every answer the
chatbot gives can be traced back to a document and a date.

## Where the material comes from

| Source | What it holds | Access |
|---|---|---|
| National Archives of Singapore — Speeches | Transcripts of speeches and press conferences, 1955–1990 | https://www.nas.gov.sg/archivesonline/speeches/ |
| Prime Minister's Office speech archive | Later speeches and eulogies | https://www.pmo.gov.sg/Newsroom |
| NUS / ISEAS published interview transcripts | Long-form interviews | Library or publisher |
| *The Singapore Story* / *From Third World to First* | Memoirs | Purchased copies — excerpt only |

## Copyright

Speech transcripts held by the National Archives are public records and are
used here with attribution. The memoirs are in copyright: this project indexes
short excerpts for a non-commercial technical assessment and does not
redistribute the books. `corpus/` is therefore **not** committed to the
repository — the loader reads whatever documents you place there locally, and
`ingest.py` reports what it indexed.

If you are reproducing this project, download the speeches you want from the
archive links above and save them in the format below.

## File format

One document per file, `.txt` or `.md`, with a header:

```
---
title: Speech at the opening of the Political Study Centre
year: 1966
type: speech
source: National Archives of Singapore
url: https://www.nas.gov.sg/archivesonline/speeches/record-details/...
---

My friends, we meet at a moment when...
```

`type` is one of `speech`, `memoir`, `interview`, `article`. It is carried into
chunk metadata and shown in the citations, because a 1966 speech and a 1998
memoir passage carry different weight on the same question.

## Current corpus

Run `python ingest.py` and it prints the per-document chunk counts. Record the
totals here once you have indexed:

| Documents | Chunks | Date range |
|---|---|---|
| _to fill_ | _to fill_ | _to fill_ |
