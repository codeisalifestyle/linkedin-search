import unittest

from linkedin_search.cli import build_parser


class CliParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_standard_search_default_output_optional(self) -> None:
        args = self.parser.parse_args(
            ["standard-search", "--query", "Financial Advisor", "--location", "Austin"]
        )
        self.assertEqual(args.query, "Financial Advisor")
        self.assertEqual(args.location, "Austin")
        self.assertIsNone(args.output)

    def test_standard_search_accepts_single_dash_query_alias(self) -> None:
        args = self.parser.parse_args(["standard-search", "-query", "Financial Advisor"])
        self.assertEqual(args.query, "Financial Advisor")

    def test_query_supports_quotes_and_parentheses(self) -> None:
        args = self.parser.parse_args(
            ['standard-search', '--query', 'Financial "Advisor" (Austin)']
        )
        self.assertEqual(args.query, 'Financial "Advisor" (Austin)')


if __name__ == "__main__":
    unittest.main()
