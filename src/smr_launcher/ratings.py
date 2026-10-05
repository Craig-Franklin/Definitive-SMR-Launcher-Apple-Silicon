"""Read public upstream discussion polls without signing in or posting votes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import re
import ssl

from .community import DISCUSSION_ROOT, _cache_document, _write_cache


MAX_POLL_BYTES = 2 * 1024 * 1024
_LABELS = ("⭐⭐⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐", "⭐⭐", "⭐", "Not Working")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class RatingError(ValueError):
    """A public poll could not be fetched safely."""


@dataclass(frozen=True)
class PollOption:
    label: str
    percentage: float


@dataclass(frozen=True)
class PollRating:
    discussion_url: str
    fetched_at: str
    status: str
    total_votes: Optional[int] = None
    options: tuple[PollOption, ...] = ()
    approximate_stars: Optional[float] = None
    detail: str = ""


def _discussion_path(url: str) -> str:
    if not isinstance(url, str) or not re.fullmatch(re.escape(DISCUSSION_ROOT) + r"[1-9][0-9]{0,9}", url):
        raise RatingError("Rating URL is not a canonical upstream discussion")
    return url.removeprefix("https://github.com")


class _PollParser(HTMLParser):
    """Recognize GitHub's server-rendered poll, excluding comments and controls."""

    def __init__(self, expected_path: str):
        super().__init__(convert_charrefs=True)
        self.expected_path = expected_path
        self.components = 0
        self.stack = []
        self.question = []
        self.text = []
        self.rows = []
        self.current_row = None
        self.results_visible = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        root = ("js-discussion-poll-component" in classes
                and attributes.get("data-poll-url") == self.expected_path + "/poll")
        if root:
            self.components += 1
        if not root and not self.stack:
            return
        parent = self.stack[-1] if self.stack else {}
        hidden = (parent.get("hidden", False) or "hidden" in attributes
                  or attributes.get("aria-hidden") == "true"
                  or bool(re.search(r"display\s*:\s*none", attributes.get("style", ""), re.I))
                  or tag in {"script", "style"})
        results = "js-discussion-poll-results" in classes or parent.get("results", False)
        if "js-discussion-poll-results" in classes and not hidden:
            self.results_visible = True
        row = None
        if results and re.fullmatch(r"result-row-[1-9][0-9]*", attributes.get("id", "")):
            row = []
            self.rows.append(row)
        if tag not in _VOID:
            self.stack.append({"tag": tag, "hidden": hidden, "results": results,
                               "question": attributes.get("id") == "poll-question" or parent.get("question", False),
                               "row": row if row is not None else parent.get("row")})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        if not self.stack or self.stack[-1]["hidden"]:
            return
        current = self.stack[-1]
        self.text.append(text)
        if current["question"]:
            self.question.append(text)
        if current["row"] is not None:
            current["row"].append(text)


def _rating(url: str, fetched_at: str, votes: int, options: tuple[PollOption, ...]) -> PollRating:
    if type(votes) is not int or not 0 <= votes <= 1_000_000_000:
        raise RatingError("Poll vote count is invalid")
    if (len(options) != 6 or {option.label for option in options} != set(_LABELS)
            or any(type(option.percentage) not in (int, float) or not 0 <= option.percentage <= 100 for option in options)):
        raise RatingError("Poll options are incomplete or invalid")
    total = sum(option.percentage for option in options)
    if votes == 0:
        if total != 0:
            raise RatingError("Empty poll has inconsistent percentages")
        return PollRating(url, fetched_at, "no_votes", 0, options, detail="No community votes yet.")
    if not 97 <= total <= 103:
        raise RatingError("Poll percentages are inconsistent")
    stars = [option for option in options if option.label != "Not Working"]
    weight = sum(option.percentage for option in stars)
    average = sum(len(option.label) * option.percentage for option in stars) / weight if weight else None
    return PollRating(url, fetched_at, "available", votes, options, average,
                      "Approximate average from GitHub's rounded percentages; Not Working votes are shown separately.")


def parse_rating(payload: bytes, discussion_url: str, *, fetched_at: Optional[str] = None) -> PollRating:
    """Parse visible results only; unavailable or changed markup never means zero stars."""
    expected_path = _discussion_path(discussion_url)
    when = fetched_at or datetime.now(timezone.utc).isoformat()
    unavailable = PollRating(discussion_url, when, "unavailable", detail="Public poll results are unavailable. Open the discussion to view or vote.")
    if len(payload) > MAX_POLL_BYTES:
        raise RatingError("Discussion page exceeds its size limit")
    try:
        parser = _PollParser(expected_path)
        parser.feed(payload.decode("utf-8"))
        parser.close()
        if (parser.components != 1 or not parser.results_visible
                or " ".join("".join(parser.question).split()) != "Map Rating"):
            return unavailable
        counts = re.findall(r"\b([0-9][0-9,]*) votes?\b", " ".join(parser.text))
        if len(counts) != 1 or not re.fullmatch(r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)", counts[0]):
            return unavailable
        options = []
        for row in parser.rows:
            text = " ".join(" ".join(row).replace("\ufe0f", "").split())
            match = re.fullmatch(r"(⭐{1,5}|Not Working) ([0-9]{1,3}(?:\.[0-9])?)%", text)
            if not match:
                return unavailable
            options.append(PollOption(match[1], float(match[2])))
        return _rating(discussion_url, when, int(counts[0].replace(",", "")), tuple(options))
    except (UnicodeError, ValueError, RecursionError):
        return unavailable


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, newurl):
        raise RatingError("Discussion redirected away from its fixed URL")


def _opener():
    return build_opener(_NoRedirects(), HTTPSHandler(context=ssl.create_default_context()))


def read_cached_rating(discussion_url: str, cache_path: Path) -> Optional[PollRating]:
    """Read one private, extracted poll snapshot without accessing the network."""
    try:
        _discussion_path(discussion_url)
        value = _cache_document(cache_path)
        if (type(value.get("schema")) is not int or value["schema"] != 1
                or value.get("source") != discussion_url or not isinstance(value.get("fetched_at"), str)
                or datetime.fromisoformat(value["fetched_at"]).tzinfo is None):
            return None
        if value.get("status") == "unavailable":
            return PollRating(discussion_url, value["fetched_at"], "unavailable", detail="Public poll results are unavailable. Open the discussion to view or vote.")
        if value.get("status") not in {"available", "no_votes"} or not isinstance(value.get("options"), list):
            return None
        options = tuple(PollOption(item["label"], item["percentage"]) for item in value["options"])
        rating = _rating(discussion_url, value["fetched_at"], value.get("total_votes"), options)
        return rating if rating.status == value["status"] else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def fetch_rating(discussion_url: str, cache_path: Path) -> PollRating:
    """Explicit read-only GET with normal TLS; never supplies credentials or votes."""
    _discussion_path(discussion_url)
    request = Request(discussion_url, headers={"User-Agent": "Definitive-SMR-Launcher-Apple-Silicon", "Accept": "text/html"})
    try:
        with _opener().open(request, timeout=15) as response:
            if response.geturl() != discussion_url:
                raise RatingError("Discussion response URL differs")
            payload = response.read(MAX_POLL_BYTES + 1)
        rating = parse_rating(payload, discussion_url)
        _write_cache(cache_path, {"schema": 1, "source": discussion_url, "fetched_at": rating.fetched_at,
                                 "status": rating.status, "total_votes": rating.total_votes,
                                 "options": [{"label": option.label, "percentage": option.percentage} for option in rating.options]})
        return rating
    except (OSError, ValueError) as exc:
        if isinstance(exc, RatingError):
            raise
        raise RatingError("Could not refresh this map's community rating") from exc
