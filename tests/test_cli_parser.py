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
        self.assertEqual(args.output_format, "csv")

    def test_standard_search_accepts_single_dash_query_alias(self) -> None:
        args = self.parser.parse_args(["standard-search", "-query", "Financial Advisor"])
        self.assertEqual(args.query, "Financial Advisor")

    def test_query_supports_quotes_and_parentheses(self) -> None:
        args = self.parser.parse_args(
            ['standard-search', '--query', 'Financial "Advisor" (Austin)']
        )
        self.assertEqual(args.query, 'Financial "Advisor" (Austin)')

    def test_company_search_accepts_json_output_format(self) -> None:
        args = self.parser.parse_args(
            [
                "company-search",
                "--company-url",
                "https://www.linkedin.com/company/microsoft/",
                "--output-format",
                "json",
            ]
        )
        self.assertEqual(args.command, "company-search")
        self.assertEqual(args.output_format, "json")

    def test_dev_browser_start_defaults(self) -> None:
        args = self.parser.parse_args(["dev-browser-start"])
        self.assertEqual(args.command, "dev-browser-start")
        self.assertFalse(args.skip_auth)
        self.assertFalse(args.reuse_existing)
        self.assertEqual(args.start_url, "https://www.linkedin.com/feed")

    def test_dev_browser_start_reuse_existing_flag(self) -> None:
        args = self.parser.parse_args(["dev-browser-start", "--reuse-existing"])
        self.assertEqual(args.command, "dev-browser-start")
        self.assertTrue(args.reuse_existing)

    def test_dev_browser_action_type_with_flags(self) -> None:
        args = self.parser.parse_args(
            [
                "dev-browser-action",
                "--action",
                "type",
                "--selector",
                "input[name='keywords']",
                "--text",
                "portfolio manager",
                "--clear",
                "--submit",
            ]
        )
        self.assertEqual(args.command, "dev-browser-action")
        self.assertEqual(args.action, "type")
        self.assertEqual(args.selector, "input[name='keywords']")
        self.assertEqual(args.text, "portfolio manager")
        self.assertTrue(args.clear)
        self.assertTrue(args.submit)


if __name__ == "__main__":
    unittest.main()
