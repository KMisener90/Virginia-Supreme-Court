"""Download Virginia Supreme Court opinions and write a JSONL index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


INDEX_URL = "https://webdev.vacourts.gov/dynamic/scndex.htm"
DEFAULT_PDF_ROOT = "https://www.vacourts.gov/"
DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


def fetch(url: str, *, timeout: int = 60) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "Virginia-SC-opinion-dataset/1.0 (research; contact site administrator)",
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_listings(html: str) -> list[dict[str, str]]:
    content_end_match = re.search(r"<!--+\s*END OF CONTENT\s*--+>", html)
    content_end = content_end_match.start() if content_end_match else len(html)
    pdf_matches = list(re.finditer(r"(?is)<a\b[^>]*href=[\"']([^\"']+\.pdf)[\"'][^>]*>(.*?)</a>", html))
    listings: list[dict[str, str]] = []
    for index, match in enumerate(pdf_matches):
        anchor_text = re.sub(r"<[^>]+>", " ", match.group(2))
        anchor_text = re.sub(r"\s+", " ", anchor_text).strip()
        end = pdf_matches[index + 1].start() if index + 1 < len(pdf_matches) else content_end
        following = re.sub(r"<[^>]+>", " ", html[match.end():end])
        following = re.sub(r"\s+", " ", following).strip()
        date_match = DATE_RE.search(following)
        record_match = re.match(r"\s*(?:\[)?(\d{6})\b", anchor_text)
        if not date_match or not record_match:
            continue
        title_text = following[: date_match.start()].strip()
        listings.append(
            {
                "record_number": record_match.group(1),
                "title": title_text,
                "date": datetime.strptime(date_match.group(1), "%m/%d/%Y").date().isoformat(),
                "summary": following[date_match.end():].strip(),
                "source_url": urljoin(DEFAULT_PDF_ROOT, match.group(1)),
            }
        )
    return listings


def safe_name(record_number: str, source_url: str) -> str:
    suffix = Path(urlparse(source_url).path).suffix.lower() or ".pdf"
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:8]
    return f"{record_number}_{digest}{suffix}"


def download(url: str, destination: Path, *, interval: float, last_request: list[float]) -> None:
    elapsed = time.monotonic() - last_request[0]
    if elapsed < interval:
        time.sleep(interval - elapsed)
    last_request[0] = time.monotonic()
    for attempt in range(1, 6):
        try:
            data = fetch(url)
            if not data.startswith(b"%PDF"):
                raise ValueError("response is not a PDF")
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(destination)
            return
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            if attempt == 5:
                raise RuntimeError(f"failed to download {url}: {error}") from error
            time.sleep(min(60.0, 2 ** attempt))


def main() -> None:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--output", type=Path, default=Path("va_sc_opinions"))
    argument_parser.add_argument("--index-file", type=Path, help="Use a locally saved HTML index instead of fetching it")
    argument_parser.add_argument("--min-year", type=int, default=1995)
    argument_parser.add_argument("--interval", type=float, default=1.0)
    argument_parser.add_argument("--refresh-index", action="store_true")
    arguments = argument_parser.parse_args()

    arguments.output.mkdir(parents=True, exist_ok=True)
    index_path = arguments.output / "scndex.htm"
    if arguments.index_file:
        index_path.write_bytes(arguments.index_file.read_bytes())
    elif arguments.refresh_index or not index_path.exists():
        index_path.write_bytes(fetch(INDEX_URL))
    html = index_path.read_text(encoding="utf-8", errors="replace")
    if "cf-chl-" in html or "Enable JavaScript and cookies" in html:
        raise RuntimeError(
            "The index request returned a Cloudflare challenge. Save the rendered page HTML "
            "and rerun with --index-file PATH."
        )
    listings = [
        listing
        for listing in parse_listings(html)
        if date.fromisoformat(listing["date"]).year >= arguments.min_year
    ]

    pdf_directory = arguments.output / "pdfs"
    pdf_directory.mkdir(exist_ok=True)
    unique_urls: dict[str, str] = {}
    last_request = [0.0]
    failures: list[str] = []
    for listing in listings:
        source_url = listing["source_url"]
        if source_url not in unique_urls:
            filename = safe_name(listing["record_number"], source_url)
            destination = pdf_directory / filename
            try:
                if not destination.exists():
                    download(source_url, destination, interval=arguments.interval, last_request=last_request)
                unique_urls[source_url] = str(destination.relative_to(arguments.output))
            except RuntimeError as error:
                failures.append(str(error))
                continue
        listing["pdf_path"] = unique_urls.get(source_url, "")

    jsonl_path = arguments.output / "virginia_supreme_court.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as output_file:
        for listing in listings:
            if listing.get("pdf_path"):
                output_file.write(json.dumps(listing, ensure_ascii=True) + "\n")
    (arguments.output / "download_errors.log").write_text("\n".join(failures), encoding="utf-8")
    print(f"listings={len(listings)} unique_pdfs={len(unique_urls)} jsonl={jsonl_path}")
    if failures:
        print(f"download_failures={len(failures)} see {arguments.output / 'download_errors.log'}")


if __name__ == "__main__":
    main()