import tempfile
import unittest
from pathlib import Path

from notify_me.core import (
    Listing,
    canonicalize_url,
    extract_listings,
    find_new_listings,
    load_state,
    save_state,
    update_source_state,
)


class CoreTests(unittest.TestCase):
    def test_extract_listings_filters_and_deduplicates_urls(self) -> None:
        html = """
        <html>
          <body>
            <a href="/property-listing/apartment-a-123?utm_source=test">Apartment A</a>
            <a href="https://www.propertyguru.com.my/property-listing/apartment-a-123">Duplicate</a>
            <a href="/property-for-rent">Search page</a>
            <a href="mailto:agent@example.com">Email</a>
          </body>
        </html>
        """

        listings = extract_listings(
            html,
            base_url="https://www.propertyguru.com.my/property-for-rent",
            source_name="PropertyGuru",
            include_patterns=[r"propertyguru\.com\.my/property-listing/"],
        )

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].title, "Apartment A")
        self.assertEqual(
            listings[0].url,
            "https://www.propertyguru.com.my/property-listing/apartment-a-123",
        )

    def test_canonicalize_url_removes_tracking_values(self) -> None:
        self.assertEqual(
            canonicalize_url(
                "HTTPS://Example.COM/rental/home/?utm_campaign=x&sort=new&fbclid=abc#top"
            ),
            "https://example.com/rental/home?sort=new",
        )

    def test_first_run_does_not_notify_by_default(self) -> None:
        source = {"name": "Mudah"}
        state = {"version": 1, "sources": {}}
        listings = [
            Listing(
                source="Mudah",
                title="Condo",
                url="https://www.mudah.my/condo-1.htm",
                listing_id="https://www.mudah.my/condo-1.htm",
            )
        ]

        self.assertEqual(
            find_new_listings(
                state,
                source=source,
                listings=listings,
                notify_on_first_run=False,
            ),
            [],
        )
        self.assertEqual(
            find_new_listings(
                state,
                source=source,
                listings=listings,
                notify_on_first_run=True,
            ),
            listings,
        )

    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = load_state(state_path)
            update_source_state(
                state,
                source_name="iProperty",
                listings=[
                    Listing(
                        source="iProperty",
                        title="Studio",
                        url="https://www.iproperty.com.my/property/studio",
                        listing_id="https://www.iproperty.com.my/property/studio",
                    )
                ],
                checked_at="2026-06-12T00:00:00+00:00",
                max_seen_per_source=5000,
            )
            save_state(state_path, state)

            loaded = load_state(state_path)
            self.assertIn("iProperty", loaded["sources"])
            self.assertIn(
                "https://www.iproperty.com.my/property/studio",
                loaded["sources"]["iProperty"]["listings"],
            )


if __name__ == "__main__":
    unittest.main()
