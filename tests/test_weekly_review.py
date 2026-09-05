import os
import unittest
from datetime import date
from unittest.mock import patch

from algo_trading.core.pricing import next_expiry
from algo_trading.reporting.weekly_review import _email_recipients, completed_week_window


class WeeklyReviewTests(unittest.TestCase):
    def test_completed_week_on_saturday_reviews_ended_friday(self):
        start, end = completed_week_window(date(2026, 9, 5))

        self.assertEqual(start, date(2026, 8, 31))
        self.assertEqual(end, date(2026, 9, 4))

    def test_completed_week_on_weekday_reviews_week_to_date(self):
        start, end = completed_week_window(date(2026, 9, 2))

        self.assertEqual(start, date(2026, 8, 31))
        self.assertEqual(end, date(2026, 9, 2))

    def test_index_weekly_expiry_uses_next_tuesday(self):
        self.assertEqual(next_expiry(date(2026, 9, 4), weekly=True), date(2026, 9, 8))

    def test_index_monthly_expiry_uses_last_tuesday(self):
        self.assertEqual(next_expiry(date(2026, 9, 4), weekly=False), date(2026, 9, 29))

    def test_email_recipients_accept_comma_and_semicolon(self):
        env = {
            "WEEKLY_EMAIL_TO": "one@example.com, two@example.com;three@example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                _email_recipients(),
                ["one@example.com", "two@example.com", "three@example.com"],
            )


if __name__ == "__main__":
    unittest.main()
