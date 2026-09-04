# Virginia Supreme Court dataset

`scrape_va_sc.py` downloads the PDF-backed listings from the Supreme Court of
Virginia opinions index and writes one JSON object per listed record to
`va_sc_opinions/virginia_supreme_court.jsonl`.

The dataset includes opinions and published orders dated 1995 onward. Records
that share a consolidated PDF remain separate JSONL rows; the PDF is downloaded
only once and each row points to the same local `pdf_path`.

The site may return a Cloudflare challenge to command-line clients. The current
`scndex.htm` was captured from the rendered page and can be used directly:

```powershell
python .\scrape_va_sc.py --index-file .\scndex.htm
```

Downloads are resumable, use a one-second minimum interval by default, and retry
transient failures with backoff. Use `--interval` to increase the delay, not to
reduce it for production collection.