"""SAAK product-image crawler engine (GUI-independent).

Crawls the public product pages of a site, downloads product images at
original quality, writes image_index.csv and an optional ZIP archive.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
import threading
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import bs4
import requests
from PIL import Image

DEFAULT_START = "https://www.saak.com.sa/ar/منتجاتنا"
SKIP_WORDS = ("logo", "favicon", "icon", "social", "footer", "spinner", "placeholder")
IMG_RE = re.compile(r"\.(jpe?g|png|webp|avif|gif)$", re.I)
FIELDS = ["page_title", "image_alt", "category", "image_url", "page_url",
          "file", "width", "height", "bytes", "sha1"]


@dataclass
class Settings:
    start_url: str = DEFAULT_START
    out_dir: Path = Path("SAAK_Product_Images")
    path_filter: str = "/منتجاتنا"      # only crawl/collect pages whose path contains this
    max_pages: int = 500
    max_depth: int = 5
    min_bytes: int = 5000
    min_px: int = 180
    delay: float = 0.15
    timeout: int = 30
    retries: int = 2
    respect_robots: bool = True
    make_zip: bool = True
    extra_skip: list[str] = field(default_factory=list)


@dataclass
class Stats:
    pages: int = 0
    images: int = 0
    skipped: int = 0
    failed: int = 0


def safe(s: str, n: int = 110) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s or "").strip(" .")
    return s[:n] or "product"


class Crawler:
    def __init__(self, settings: Settings,
                 log: Callable[[str], None] = print,
                 progress: Callable[[Stats], None] | None = None):
        self.s = settings
        self.log = log
        self.progress = progress or (lambda st: None)
        self.stop_event = threading.Event()
        self.stats = Stats()
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 (compatible; ProductCatalogArchiver/2.0)"})
        p = urllib.parse.urlparse(settings.start_url)
        host = p.netloc.lower()
        bare = host[4:] if host.startswith("www.") else host
        self.hosts = {bare, "www." + bare}
        self.robots = None
        if settings.respect_robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=15)
                rp.parse(r.text.splitlines() if r.ok else [])
                self.robots = rp
            except Exception:
                self.robots = None

    # ---------------------------------------------------------------- helpers
    def stop(self):
        self.stop_event.set()

    def allowed(self, url: str) -> bool:
        if not self.robots:
            return True
        try:
            return self.robots.can_fetch(self.session.headers["User-Agent"], url)
        except Exception:
            return True

    def normalize(self, url, source):
        if not url:
            return None
        url = urllib.parse.urljoin(source, url.strip())
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https") or p.netloc.lower() not in self.hosts:
            return None
        return urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))

    def in_scope(self, url: str) -> bool:
        return self.s.path_filter in urllib.parse.unquote(url)

    def get(self, url):
        last = None
        for attempt in range(self.s.retries + 1):
            if self.stop_event.is_set():
                raise RuntimeError("stopped")
            try:
                r = self.session.get(url, timeout=self.s.timeout)
                r.raise_for_status()
                return r
            except Exception as e:
                last = e
                time.sleep(0.6 * (attempt + 1))
        raise last

    # ------------------------------------------------------------------- run
    def run(self) -> dict:
        s = self.s
        out = Path(s.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        skip_words = tuple(w.lower() for w in (*SKIP_WORDS, *s.extra_skip) if w)

        # resume: load existing index so re-runs don't duplicate
        index_path = out / "image_index.csv"
        records, seen_src, seen_hash = [], set(), set()
        if index_path.exists():
            with open(index_path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row.get("file") and Path(row["file"]).exists():
                        records.append(row)
                        seen_src.add(row["image_url"])
                        if row.get("sha1"):
                            seen_hash.add(row["sha1"])
            if records:
                self.log(f"استكمال: {len(records)} صورة موجودة مسبقًا")

        queue = deque([(s.start_url, 0)])
        seen = set()
        while queue and len(seen) < s.max_pages and not self.stop_event.is_set():
            url, depth = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            if not self.allowed(url):
                self.log(f"robots.txt يمنع: {url}")
                continue
            try:
                resp = self.get(url)
                if "html" not in resp.headers.get("Content-Type", ""):
                    continue
                if "charset" in resp.headers.get("Content-Type", "").lower():
                    soup = bs4.BeautifulSoup(resp.text, "html.parser")
                else:  # no declared charset: let bs4 sniff <meta>/bytes (UTF-8 Arabic)
                    soup = bs4.BeautifulSoup(resp.content, "html.parser")
            except Exception as e:
                if self.stop_event.is_set():
                    break
                self.stats.failed += 1
                self.log(f"فشل الصفحة: {urllib.parse.unquote(url)} — {e}")
                continue
            self.stats.pages += 1
            self.progress(self.stats)
            if not self.in_scope(url):
                continue
            self.log(f"صفحة [{depth}]: {urllib.parse.unquote(url)}")
            h = soup.find("h1") or soup.title
            title = safe(h.get_text(" ", strip=True) if h else "SAAK")
            parts = [x for x in urllib.parse.unquote(urllib.parse.urlparse(url).path).split("/") if x]
            key = s.path_filter.strip("/")
            if key in parts and parts.index(key) + 1 < len(parts):
                category = safe(parts[parts.index(key) + 1])      # first segment after /منتجاتنا
            else:
                category = safe(parts[-2] if len(parts) >= 2 else "منتجات")
            if key and category == key:
                category = "عام"

            for tag in soup.find_all(["img", "source"]):
                if self.stop_event.is_set():
                    break
                cands = [tag.get("data-src"), tag.get("data-lazy-src"), tag.get("data-original"), tag.get("src")]
                for attr in ("srcset", "data-srcset"):
                    if tag.get(attr):
                        cands.extend(re.sub(r"\s+\d+(\.\d+)?[wx]$", "", x.strip())
                                     for x in tag[attr].split(","))
                for cand in cands:
                    src = self.normalize(cand, url)
                    if not src or src in seen_src:
                        continue
                    p = urllib.parse.urlparse(src)
                    if not IMG_RE.search(p.path):
                        continue
                    alt = safe(tag.get("alt", "") or "")
                    if any(k in (p.path + " " + alt).lower() for k in skip_words):
                        continue
                    seen_src.add(src)
                    try:
                        r = self.get(src)
                        if not r.headers.get("Content-Type", "").lower().startswith("image/"):
                            self.stats.skipped += 1
                            continue
                        data = r.content
                        if len(data) < s.min_bytes:
                            self.stats.skipped += 1
                            continue
                        digest_c = hashlib.sha1(data).hexdigest()
                        if digest_c in seen_hash:
                            self.stats.skipped += 1
                            continue
                        try:
                            w, hgt = Image.open(io.BytesIO(data)).size
                        except Exception:
                            w = hgt = 0
                            if not p.path.lower().endswith(".avif"):
                                self.stats.skipped += 1
                                continue
                        if w and (w < s.min_px or hgt < s.min_px):
                            self.stats.skipped += 1
                            continue
                        seen_hash.add(digest_c)
                        folder = out / category
                        folder.mkdir(parents=True, exist_ok=True)
                        ext = Path(p.path).suffix.lower() or ".jpg"
                        name = safe(alt or urllib.parse.unquote(Path(p.path).stem)) + "_" + digest_c[:10] + ext
                        path = folder / name
                        path.write_bytes(data)
                        records.append({"page_title": title, "image_alt": alt, "category": category,
                                        "image_url": src, "page_url": url, "file": str(path),
                                        "width": w, "height": hgt, "bytes": len(data), "sha1": digest_c})
                        self.stats.images += 1
                        self.log(f"  ✔ {category}/{name}  ({w}×{hgt})")
                    except Exception as e:
                        if self.stop_event.is_set():
                            break
                        self.stats.failed += 1
                        self.log(f"  ✘ فشل تنزيل الصورة: {src} — {e}")
                    self.progress(self.stats)

            if depth < s.max_depth:
                for a in soup.select("a[href]"):
                    link = self.normalize(a.get("href"), url)
                    if link and self.in_scope(link) and link not in seen:
                        queue.append((link, depth + 1))
            time.sleep(s.delay)

        with open(index_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(records)

        archive = None
        if s.make_zip and records:
            archive = shutil.make_archive(str(out.parent / out.name), "zip",
                                          root_dir=str(out.parent), base_dir=out.name)
        status = "أُوقف" if self.stop_event.is_set() else "اكتمل"
        self.log(f"{status}: {len(records)} صورة إجمالًا ({self.stats.images} جديدة) — "
                 f"{self.stats.pages} صفحة — فشل {self.stats.failed}")
        if archive:
            self.log(f"الأرشيف: {archive}")
        return {"records": len(records), "new": self.stats.images, "pages": self.stats.pages,
                "failed": self.stats.failed, "index": str(index_path), "archive": archive,
                "stopped": self.stop_event.is_set()}


if __name__ == "__main__":  # CLI mode
    import argparse
    ap = argparse.ArgumentParser(description="SAAK product image downloader")
    ap.add_argument("--url", default=DEFAULT_START)
    ap.add_argument("--out", default="SAAK_Product_Images")
    ap.add_argument("--filter", default="/منتجاتنا")
    ap.add_argument("--max-pages", type=int, default=500)
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--no-robots", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    Crawler(Settings(start_url=a.url, out_dir=Path(a.out), path_filter=a.filter,
                     max_pages=a.max_pages, max_depth=a.depth,
                     respect_robots=not a.no_robots, make_zip=not a.no_zip)).run()
