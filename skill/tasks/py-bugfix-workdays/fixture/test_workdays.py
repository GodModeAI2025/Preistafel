import unittest
from datetime import date
from workdays import count_workdays


class T(unittest.TestCase):
    def test_same_weekday(self):
        self.assertEqual(count_workdays(date(2026, 9, 21), date(2026, 9, 21)), 1)


if __name__ == "__main__":
    unittest.main()
