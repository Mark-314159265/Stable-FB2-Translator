import unittest
import tempfile
import os
import threading
import time
from unittest.mock import patch, MagicMock
from lxml import etree
import translator_core
from translator_core import (
    TranslationConfig,
    TranslationCancelled,
    StatsTracker,
    smart_wait
)


class TestTranslatorCore(unittest.TestCase):
    def setUp(self):
        self.xml_content = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description>
    <title-info>
      <book-title>Test Book</book-title>
    </title-info>
  </description>
  <body>
    <title><p>Chapter 1: The Beginning</p></title>
    <p>This is the first paragraph with <emphasis>emphasis</emphasis> inside.</p>
    <p>This is the second paragraph.</p>
    <poem>
      <stanza>
        <v>Verse line 1</v>
        <v>Verse line 2</v>
      </stanza>
    </poem>
  </body>
</FictionBook>"""

    def test_parse_and_translate_fb2_with_config(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".fb2", delete=False) as f:
            f.write(self.xml_content)
            input_fb2 = f.name

        output_fb2 = input_fb2.replace(".fb2", "_out.fb2")

        try:
            tree, elements = translator_core.parse_fb2(input_fb2)
            self.assertEqual(len(elements), 5)

            cfg = TranslationConfig(
                delay_req=0.1,
                char_limit=4000,
                temperature=0.2
            )

            req_count = [0]
            def on_req():
                req_count[0] += 1

            with patch("translator_core.get_genai_client") as mock_client, \
                 patch("translator_core.translate_batch") as mock_batch:

                mock_batch.side_effect = lambda client, batch_texts, **kwargs: [
                    f"[UA] {t}" for t in batch_texts
                ]

                res = translator_core.translate_fb2(
                    input_path=input_fb2,
                    output_path=output_fb2,
                    api_key="mock_key",
                    config=cfg,
                    on_request=on_req
                )

                self.assertEqual(res, output_fb2)
                self.assertTrue(os.path.exists(output_fb2))
                self.assertGreater(req_count[0], 0)

                out_tree, out_elements = translator_core.parse_fb2(output_fb2)
                self.assertEqual(len(out_elements), 5)
                for el in out_elements:
                    self.assertIsNone(el.get("translated"))
                    self.assertTrue(el.text.startswith("[UA]"))

        finally:
            if os.path.exists(input_fb2):
                os.remove(input_fb2)
            if os.path.exists(output_fb2):
                os.remove(output_fb2)

    def test_cancellation(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".fb2", delete=False) as f:
            f.write(self.xml_content)
            input_fb2 = f.name

        output_fb2 = input_fb2.replace(".fb2", "_cancelled.fb2")
        cancel_event = threading.Event()
        cancel_event.set()  # Cancel immediately

        try:
            with patch("translator_core.get_genai_client"), \
                 patch("translator_core.translate_batch"):

                with self.assertRaises(TranslationCancelled):
                    translator_core.translate_fb2(
                        input_path=input_fb2,
                        output_path=output_fb2,
                        api_key="mock_key",
                        cancel_event=cancel_event
                    )

        finally:
            if os.path.exists(input_fb2):
                os.remove(input_fb2)
            if os.path.exists(output_fb2):
                os.remove(output_fb2)

    def test_smart_wait(self):
        # 1. Normal wait completes
        t0 = time.time()
        res = smart_wait(0.2)
        self.assertTrue(res)
        self.assertGreaterEqual(time.time() - t0, 0.15)

        # 2. Cancelled wait aborts early
        cancel_event = threading.Event()
        cancel_event.set()
        t0 = time.time()
        res = smart_wait(5.0, cancel_event=cancel_event)
        self.assertFalse(res)
        self.assertLess(time.time() - t0, 0.5)

    def test_stats_tracker(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
            stats_file = f.name

        try:
            tracker = StatsTracker(filepath=stats_file, daily_limit=500)
            st = tracker.get_stats()
            self.assertEqual(st["requests_today"], 0)
            self.assertEqual(st["daily_limit"], 500)
            self.assertIn(":", st["time_until_reset"])

            c1 = tracker.increment()
            self.assertEqual(c1, 1)
            c2 = tracker.increment()
            self.assertEqual(c2, 2)

            st2 = tracker.get_stats()
            self.assertEqual(st2["requests_today"], 2)

        finally:
            if os.path.exists(stats_file):
                os.remove(stats_file)


if __name__ == "__main__":
    unittest.main()

