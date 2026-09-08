#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import mimetypes
import os
import posixpath
import re
import sys
from collections import deque
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_AGENT = "Mozilla/5.0 (compatible; CodexSiteMirror/1.0; +https://openai.com)"
PAGE_LIMIT = 40
ASSET_LIMIT = 2500
SAME_HOST_PAGE_SCHEMES = {"http", "https"}
ASSET_EXTENSIONS = {
    ".avif",
    ".css",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".svg",
    ".ttf",
    ".txt",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
}
TEXT_ASSET_EXTENSIONS = {".css", ".js", ".json", ".svg", ".txt", ".xml"}
PRIORITY_ASSET_EXTENSIONS = {".css", ".js", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".svg"}
SKIP_PAGE_PREFIXES = ("/wp-admin", "/wp-login", "/wp-json", "/xmlrpc.php")
SKIP_PAGE_SUFFIXES = ("/feed/", "/comments/feed/")
SKIP_QUERY_KEYS = {"replytocom", "add-to-cart", "wc-ajax"}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: mirror_site.py <base_url> <output_dir>", file=sys.stderr)
        return 1

    base_url = normalize_url(sys.argv[1], sys.argv[1], "")
    if not base_url:
        print("invalid base_url", file=sys.stderr)
        return 1

    output_dir = Path(sys.argv[2]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    mirror = SiteMirror(base_url=base_url, output_dir=output_dir)
    mirror.run()
    print(
        f"Mirrored {len(mirror.seen_pages)} pages and {len(mirror.seen_assets)} assets into {output_dir}"
    )
    return 0


class SiteMirror:
    def __init__(self, base_url: str, output_dir: Path) -> None:
        self.base_url = base_url
        self.base_host = urlsplit(base_url).netloc.lower()
        self.output_dir = output_dir
        self.page_queue: deque[str] = deque([base_url])
        self.asset_queue: deque[str] = deque()
        self.seen_pages: set[str] = set()
        self.seen_assets: set[str] = set()
        self.queued_pages: set[str] = {base_url}
        self.queued_assets: set[str] = set()
        self.failures: list[str] = []

    def run(self) -> None:
        while (self.page_queue or self.asset_queue) and (
            len(self.seen_pages) < PAGE_LIMIT or len(self.seen_assets) < ASSET_LIMIT
        ):
            if self.asset_queue and len(self.seen_assets) < ASSET_LIMIT:
                asset_url = self.asset_queue.popleft()
                self.fetch_asset(asset_url)
                continue

            if self.page_queue and len(self.seen_pages) < PAGE_LIMIT:
                page_url = self.page_queue.popleft()
                self.fetch_page(page_url)
                continue

            break

        if self.failures:
            failures_path = self.output_dir / "_mirror_failures.txt"
            failures_path.write_text("\n".join(self.failures) + "\n", encoding="utf-8")

    def fetch_page(self, page_url: str) -> None:
        if page_url in self.seen_pages:
            return

        response = fetch_url(page_url)
        if not response:
            self.failures.append(page_url)
            return

        final_url, content_type, data = response
        if "text/html" not in content_type and not final_url.endswith("/"):
            self.failures.append(f"{page_url} -> unexpected content-type {content_type}")
            return

        html = decode_bytes(data, content_type)
        canonical_url = normalize_url(final_url, final_url, "")
        if not canonical_url:
            canonical_url = page_url

        local_path = local_path_for_url(self.output_dir, self.base_host, canonical_url, is_page=True)
        rewritten_html = rewrite_html(
            html=html,
            current_url=canonical_url,
            current_local_path=local_path,
            mirror=self,
        )
        ensure_parent(local_path)
        local_path.write_text(rewritten_html, encoding="utf-8")
        self.seen_pages.add(canonical_url)

    def fetch_asset(self, asset_url: str) -> None:
        if asset_url in self.seen_assets:
            return

        response = fetch_url(asset_url)
        if not response:
            self.failures.append(asset_url)
            return

        final_url, content_type, data = response
        canonical_url = normalize_url(final_url, final_url, "")
        if not canonical_url:
            canonical_url = asset_url

        local_path = local_path_for_url(self.output_dir, self.base_host, canonical_url, is_page=False)
        ensure_parent(local_path)

        if is_text_asset(canonical_url, content_type):
            text = decode_bytes(data, content_type)
            if canonical_url.lower().endswith(".css") or "text/css" in content_type:
                rewritten = rewrite_css(
                    css=text,
                    current_url=canonical_url,
                    current_local_path=local_path,
                    mirror=self,
                )
            else:
                rewritten = text
            local_path.write_text(rewritten, encoding="utf-8")
        else:
            local_path.write_bytes(data)

        self.seen_assets.add(canonical_url)

    def queue_page(self, page_url: str) -> None:
        normalized = normalize_url(page_url, page_url, self.base_host)
        if not normalized or normalized in self.seen_pages or normalized in self.queued_pages:
            return
        if not should_crawl_page(normalized, self.base_host):
            return
        self.page_queue.append(normalized)
        self.queued_pages.add(normalized)

    def queue_asset(self, asset_url: str) -> None:
        normalized = normalize_url(asset_url, asset_url, self.base_host)
        if not normalized or normalized in self.seen_assets or normalized in self.queued_assets:
            return

        suffix = Path(unquote(urlsplit(normalized).path)).suffix.lower()
        if suffix in PRIORITY_ASSET_EXTENSIONS:
            self.asset_queue.appendleft(normalized)
        else:
            self.asset_queue.append(normalized)
        self.queued_assets.add(normalized)


def fetch_url(url: str) -> tuple[str, str, bytes] | None:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=30) as response:
            return response.geturl(), response.headers.get("Content-Type", ""), response.read()
    except Exception as exc:  # noqa: BLE001
        print(f"warn: failed to fetch {url}: {exc}", file=sys.stderr)
        return None


def rewrite_html(html: str, current_url: str, current_local_path: Path, mirror: SiteMirror) -> str:
    def replace_srcset(match: re.Match[str]) -> str:
        values = []
        for part in match.group("value").split(","):
            piece = part.strip()
            if not piece:
                continue
            segments = piece.split()
            rewritten = rewrite_reference(
                raw_url=segments[0],
                current_url=current_url,
                current_local_path=current_local_path,
                mirror=mirror,
                attribute="src",
            )
            segments[0] = rewritten
            values.append(" ".join(segments))
        return f'{match.group("prefix")}{", ".join(values)}{match.group("suffix")}'

    def replace_attribute(match: re.Match[str]) -> str:
        rewritten = rewrite_reference(
            raw_url=unescape(match.group("value")),
            current_url=current_url,
            current_local_path=current_local_path,
            mirror=mirror,
            attribute=match.group("attr").lower(),
        )
        return f'{match.group("prefix")}{rewritten}{match.group("suffix")}'

    rewritten = SRCSET_RE.sub(replace_srcset, html)
    rewritten = ATTRIBUTE_RE.sub(replace_attribute, rewritten)
    rewritten = CSS_URL_RE.sub(
        lambda match: replace_css_url(match, current_url, current_local_path, mirror),
        rewritten,
    )
    return rewritten


def rewrite_css(css: str, current_url: str, current_local_path: Path, mirror: SiteMirror) -> str:
    return CSS_URL_RE.sub(
        lambda match: replace_css_url(match, current_url, current_local_path, mirror),
        css,
    )


def replace_css_url(
    match: re.Match[str],
    current_url: str,
    current_local_path: Path,
    mirror: SiteMirror,
) -> str:
    quote = match.group("quote") or ""
    raw_url = unescape(match.group("value"))
    rewritten = rewrite_reference(
        raw_url=raw_url,
        current_url=current_url,
        current_local_path=current_local_path,
        mirror=mirror,
        attribute="src",
    )
    return f"url({quote}{rewritten}{quote})"


def rewrite_reference(
    raw_url: str,
    current_url: str,
    current_local_path: Path,
    mirror: SiteMirror,
    attribute: str,
) -> str:
    stripped = raw_url.strip()
    if not stripped or stripped.startswith(("#", "data:", "mailto:", "tel:", "javascript:")):
        return raw_url

    normalized = normalize_url(stripped, current_url, mirror.base_host)
    if not normalized:
        return raw_url

    split = urlsplit(normalized)
    is_same_host = split.netloc.lower() == mirror.base_host
    is_asset = attribute != "href" or is_probably_asset(normalized)
    if attribute == "href" and is_same_host and not is_asset and should_crawl_page(normalized, mirror.base_host):
        mirror.queue_page(normalized)
        target_local = local_path_for_url(
            mirror.output_dir,
            mirror.base_host,
            normalized,
            is_page=True,
        )
        return relative_link(current_local_path, target_local)

    if attribute == "href" and is_same_host and not is_asset:
        return raw_url

    if is_asset or (attribute == "href" and is_external_asset(normalized, mirror.base_host)):
        mirror.queue_asset(normalized)
        target_local = local_path_for_url(
            mirror.output_dir,
            mirror.base_host,
            normalized,
            is_page=False,
        )
        return relative_link(current_local_path, target_local)

    return raw_url


def normalize_url(raw_url: str, current_url: str, base_host: str) -> str | None:
    combined = urljoin(current_url, raw_url)
    split = urlsplit(combined)
    if split.scheme.lower() not in SAME_HOST_PAGE_SCHEMES:
        return None

    path = split.path or "/"
    if split.netloc.lower() == base_host and not posixpath.basename(path).count(".") and not path.endswith("/"):
        path = f"{path}/"

    return urlunsplit(
        (
            split.scheme.lower(),
            split.netloc.lower(),
            path,
            split.query,
            "",
        )
    )


def should_crawl_page(url: str, base_host: str) -> bool:
    split = urlsplit(url)
    if split.netloc.lower() != base_host:
        return False
    if split.path.startswith(SKIP_PAGE_PREFIXES):
        return False
    if split.path.endswith(SKIP_PAGE_SUFFIXES):
        return False
    if any(key in split.query for key in SKIP_QUERY_KEYS):
        return False
    if is_probably_asset(url):
        return False
    return True


def is_probably_asset(url: str) -> bool:
    path = unquote(urlsplit(url).path)
    suffix = Path(path).suffix.lower()
    return suffix in ASSET_EXTENSIONS


def is_external_asset(url: str, base_host: str) -> bool:
    split = urlsplit(url)
    return split.netloc.lower() != base_host and is_probably_asset(url)


def is_text_asset(url: str, content_type: str) -> bool:
    if content_type.startswith("text/") or "javascript" in content_type or "json" in content_type or "svg" in content_type:
        return True
    suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
    return suffix in TEXT_ASSET_EXTENSIONS


def local_path_for_url(output_dir: Path, base_host: str, url: str, is_page: bool) -> Path:
    split = urlsplit(url)
    path = unquote(split.path or "/")
    parts = [part for part in Path(path).parts if part not in {"", "/", ".", ".."}]

    if split.netloc.lower() == base_host:
        root = output_dir
    else:
        root = output_dir / "_external" / split.netloc.lower()

    if is_page:
        if not parts:
            return root / "index.html"
        return root.joinpath(*parts) / "index.html"

    if not parts:
        name = "index"
        suffix = guess_suffix_from_url(url)
        return root / f"{name}{suffix}"

    filename = parts[-1]
    if split.query and not split.query.startswith("ver="):
        file_path = Path(filename)
        digest = hashlib.sha1(split.query.encode("utf-8")).hexdigest()[:10]
        filename = f"{file_path.stem}--{digest}{file_path.suffix}"

    asset_path = root.joinpath(*parts[:-1], filename)
    suffix = asset_path.suffix
    if not suffix:
        asset_path = asset_path.with_suffix(guess_suffix_from_url(url))
    return asset_path


def guess_suffix_from_url(url: str) -> str:
    suffix = Path(unquote(urlsplit(url).path)).suffix
    return suffix or ".bin"


def relative_link(current_local_path: Path, target_local_path: Path) -> str:
    return os.path.relpath(target_local_path, current_local_path.parent).replace(os.sep, "/")


def decode_bytes(data: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([^\s;]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


ATTRIBUTE_RE = re.compile(
    r'(?P<prefix>\b(?P<attr>href|src|poster|data-src|data-lazy-src)=["\'])'
    r"(?P<value>[^\"']+)"
    r'(?P<suffix>["\'])',
    re.IGNORECASE,
)
SRCSET_RE = re.compile(
    r'(?P<prefix>\bsrcset=["\'])(?P<value>[^"\']+)(?P<suffix>["\'])',
    re.IGNORECASE,
)
CSS_URL_RE = re.compile(
    r"url\((?P<quote>['\"]?)(?P<value>[^)\"']+)(?P=quote)\)",
    re.IGNORECASE,
)


if __name__ == "__main__":
    raise SystemExit(main())
