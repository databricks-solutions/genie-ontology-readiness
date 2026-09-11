import unittest

from server.routes._shared import _model_record, _classify_family


class ModelClassificationTest(unittest.TestCase):
    def test_families_and_licensing(self):
        cases = {
            "databricks-claude-sonnet-5": ("Claude", False),
            "databricks-gpt-5-6": ("GPT", False),
            "databricks-gemini-2-5-pro": ("Gemini", False),
            "databricks-gemma-2-9b": ("Gemma", True),
            "databricks-meta-llama-3-70b": ("Llama", True),
            "databricks-qwen2-5": ("Qwen", True),
            "databricks-mixtral-8x7b": ("Mistral", True),
            "databricks-dbrx-instruct": ("DBRX", True),
            # OpenAI open-weight: grouped under GPT but marked open source.
            "databricks-gpt-oss-120b": ("GPT", True),
            "databricks-gpt-oss-20b": ("GPT", True),
            "databricks-deepseek-v3": ("DeepSeek", True),
            "databricks-phi-4": ("Phi", True),
        }
        for model_id, (family, open_source) in cases.items():
            rec = _model_record(model_id)
            self.assertEqual(rec["family"], family, model_id)
            self.assertEqual(rec["open_source"], open_source, model_id)
            # Every record carries the full picker shape.
            self.assertEqual(set(rec), {"id", "label", "provider", "family", "open_source"})

    def test_gemma_checked_before_gemini(self):
        # "gemma" must not be misread as the proprietary Gemini family.
        self.assertEqual(_classify_family("databricks-gemma-7b", "Google"), ("Gemma", True))

    def test_gpt_oss_checked_before_gpt(self):
        # "gpt-oss" (open weight) must not be misread as the proprietary GPT family.
        self.assertEqual(_classify_family("databricks-gpt-oss-120b", "OpenAI"), ("GPT", True))
        # A plain GPT model stays proprietary.
        self.assertEqual(_classify_family("databricks-gpt-5-6", "OpenAI"), ("GPT", False))

    def test_unknown_family_falls_back_to_provider_and_proprietary(self):
        family, open_source = _classify_family("some-custom-endpoint", "Databricks")
        self.assertEqual(family, "Databricks")
        self.assertFalse(open_source)


if __name__ == "__main__":
    unittest.main()
