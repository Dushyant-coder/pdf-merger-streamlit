# PDF toolkit

A Streamlit dashboard for merging PDFs/images into one PDF, and splitting a PDF back apart.

## Features

- **Merge files** — select multiple PDFs and images (PNG/JPEG), reorder them, give each a display name (used as a bookmark in the output), and merge into one PDF. Pages are copied directly with no re-encoding, so quality is preserved exactly.
- **Split & convert** — upload one PDF and either:
  - convert selected pages to images (PNG/JPEG, adjustable DPI and quality), or
  - split it into separate PDFs (one file per page, or keep custom ranges grouped together).

## Setup

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Then open the URL Streamlit prints (usually `http://localhost:8501`).
