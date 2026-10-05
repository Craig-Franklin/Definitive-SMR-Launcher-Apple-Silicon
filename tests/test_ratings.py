"""Synthetic GitHub poll markup; no credentials or network required."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smr_launcher import ratings


URL = ratings.DISCUSSION_ROOT + "42"
WHEN = "2026-10-04T20:00:00+00:00"


def poll(percentages=(50, 25, 0, 0, 0, 25), votes=4, hidden=False):
    rows = "".join(f'<div id="result-row-{i}"><div>{label}<svg hidden="hidden">hidden text</svg></div><div>{percentage}%</div></div>'
                   for i, (label, percentage) in enumerate(zip(ratings._LABELS, percentages), 1))
    hidden_attr = ' hidden="hidden"' if hidden else ""
    return (f'<html><div>999 votes, unrelated text</div><div class="js-discussion-poll-component Box" '
            f'data-poll-url="/ageekhere/Definitive-SMR-Launcher/discussions/42/poll">'
            f'<div id="poll-question">Map Rating</div><div class="js-discussion-poll-options" hidden>fake controls</div>'
            f'<div class="js-discussion-poll-results"{hidden_attr}>{rows}</div>'
            f'<p>{votes} votes <button hidden>Show Results</button></p><input type="hidden" value="never-cache-this"></div></html>').encode()


class Response(BytesIO):
    def geturl(self):
        return URL


class FakeOpener:
    def open(self, request, timeout):
        assert request.full_url == URL and request.get_method() == "GET" and timeout == 15
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        return Response(poll())


class RatingTests(unittest.TestCase):
    def test_actual_markup_shape_reports_votes_percentages_and_approximate_mean(self):
        result = ratings.parse_rating(poll(), URL, fetched_at=WHEN)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.total_votes, 4)
        self.assertAlmostEqual(result.approximate_stars, 14 / 3)
        self.assertEqual(result.options[-1], ratings.PollOption("Not Working", 25.0))
        self.assertIn("rounded percentages", result.detail)
        result = ratings.parse_rating(poll((0, 0, 0, 0, 0, 100), 1), URL)
        self.assertIsNone(result.approximate_stars)
        self.assertEqual(result.status, "available")

    def test_zero_votes_distinct_from_hidden_or_missing_results(self):
        zero = ratings.parse_rating(poll((0, 0, 0, 0, 0, 0), 0), URL)
        self.assertEqual((zero.status, zero.total_votes, zero.approximate_stars), ("no_votes", 0, None))
        for body in (poll(hidden=True), b"<html>Sign in to view results</html>",
                     poll().replace(b"Map Rating", b"Unrelated poll"),
                     poll().replace(b"result-row-6", b"changed-markup"),
                     poll().replace(b"/42/poll", b"/43/poll"),
                     poll((99, 99, 99, 99, 99, 99)), poll() + poll(), b"\xff"):
            with self.subTest(body=body[:50]):
                result = ratings.parse_rating(body, URL)
                self.assertEqual(result.status, "unavailable")
                self.assertIsNone(result.total_votes)
                self.assertIsNone(result.approximate_stars)
                self.assertEqual(result.options, ())

    def test_source_boundaries_limits_and_redirects(self):
        for url in (URL + "?query=1", URL.replace("https", "http"), URL + "/../43",
                    URL.replace("github.com", "evil.example"), URL.replace("github.com", "user:secret@github.com")):
            with self.assertRaises(ratings.RatingError):
                ratings.parse_rating(poll(), url)
        with self.assertRaises(ratings.RatingError):
            ratings.parse_rating(b"x" * (ratings.MAX_POLL_BYTES + 1), URL)
        with self.assertRaises(ratings.RatingError):
            ratings._NoRedirects().redirect_request(None, None, 302, "", {}, "https://github.com/login")

    def test_explicit_get_caches_only_extracted_data_and_preserves_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp).resolve() / "ratings/42.json"
            with patch.object(ratings, "_opener", return_value=FakeOpener()):
                rating = ratings.fetch_rating(URL, cache)
            self.assertEqual(ratings.read_cached_rating(URL, cache), rating)
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
            before = cache.read_bytes()
            self.assertNotIn(b"never-cache-this", before)
            with patch.object(ratings, "_opener", side_effect=OSError("offline")):
                with self.assertRaises(ratings.RatingError):
                    ratings.fetch_rating(URL, cache)
            self.assertEqual(cache.read_bytes(), before)
            with patch.object(ratings, "_opener", side_effect=AssertionError("must not fetch")):
                self.assertEqual(ratings.read_cached_rating(URL, cache), rating)
                self.assertIsNone(ratings.read_cached_rating(URL + "1", cache))
                value = json.loads(before)
                value["options"][0]["percentage"] = 500
                cache.write_text(json.dumps(value))
                self.assertIsNone(ratings.read_cached_rating(URL, cache))
                cache.write_text("corrupt")
                self.assertIsNone(ratings.read_cached_rating(URL, cache))
                self.assertIsNone(ratings.read_cached_rating(URL, cache.parent / "absent"))


if __name__ == "__main__":
    unittest.main()
