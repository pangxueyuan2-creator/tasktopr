import unittest

from calculator import divide


class DivideTests(unittest.TestCase):
    def test_divide_returns_quotient(self) -> None:
        self.assertEqual(divide(8, 2), 4)
