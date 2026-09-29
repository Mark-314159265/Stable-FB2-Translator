import tempfile
import os
from unittest.mock import MagicMock, patch
from lxml import etree
import translator_core

xml_content = """<?xml version="1.0" encoding="utf-8"?>
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

with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".fb2", delete=False) as f:
    f.write(xml_content)
    input_fb2 = f.name

output_fb2 = input_fb2.replace(".fb2", "_out.fb2")

try:
    tree, elements = translator_core.parse_fb2(input_fb2)
    print(f"Total elements detected: {len(elements)}")
    for i, el in enumerate(elements):
        tag_local = etree.QName(el).localname
        print(f"  [{i}] <{tag_local}>: {''.join(el.itertext()).strip()}")

    # Mock the Gemini client and translate_batch to verify translate_fb2 end-to-end
    with patch("translator_core.get_genai_client") as mock_get_client, \
         patch("translator_core.translate_batch") as mock_batch:

        mock_batch.side_effect = lambda client, batch_texts, **kwargs: [
            f"[UA] {t}" for t in batch_texts
        ]

        progress_log = []
        def on_prog(cur, tot, msg):
            progress_log.append((cur, tot, msg))

        translator_core.translate_fb2(
            input_path=input_fb2,
            output_path=output_fb2,
            api_key="mock_key",
            progress_callback=on_prog
        )

        out_tree, out_elements = translator_core.parse_fb2(output_fb2)
        print(f"\nTranslated elements in output ({len(out_elements)}):")
        for i, el in enumerate(out_elements):
            tag_local = etree.QName(el).localname
            print(f"  [{i}] <{tag_local}>: {el.text}")
            assert el.get("translated") is None, "Helper attribute 'translated' should be cleaned up"
            assert "[UA]" in (el.text or ""), f"Element {i} was not translated: {el.text}"

        print(f"\nProgress calls recorded: {len(progress_log)}")
        print("TEST PASSED SUCCESSFULLY!")

finally:
    if os.path.exists(input_fb2):
        os.remove(input_fb2)
    if os.path.exists(output_fb2):
        os.remove(output_fb2)
