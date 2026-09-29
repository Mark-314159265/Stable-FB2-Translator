import tempfile
import os
import shutil
from unittest.mock import MagicMock, patch
import telebot
import bot

# 1. Create a dummy FB2 file
test_dir = tempfile.mkdtemp()
dummy_fb2_path = os.path.join(test_dir, "test_book.fb2")
with open(dummy_fb2_path, "w", encoding="utf-8") as f:
    f.write("""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <body><p>Hello world</p></body>
</FictionBook>""")

try:
    # 2. Mock telebot Message
    mock_msg = MagicMock()
    mock_msg.chat.id = 123456
    mock_msg.document.file_name = "test_book.fb2"
    mock_msg.document.file_size = 1024
    mock_msg.document.file_id = "test_file_id"

    # Mock bot methods
    bot.bot.get_file = MagicMock(return_value=MagicMock(file_path="remote/path/test_book.fb2"))
    with open(dummy_fb2_path, "rb") as f:
        content_bytes = f.read()
    bot.bot.download_file = MagicMock(return_value=content_bytes)
    bot.bot.reply_to = MagicMock(return_value=MagicMock(message_id=999))
    bot.bot.edit_message_text = MagicMock()
    bot.bot.send_document = MagicMock()

    # Mock GEMINI_API_KEY
    with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy_key"}):
        with patch("bot.translate_fb2") as mock_translate:
            # Simulate translator creating the output file
            def fake_translate(input_path, output_path, progress_callback=None):
                with open(output_path, "w", encoding="utf-8") as out:
                    out.write("<FictionBook><body><p>Привіт світ</p></body></FictionBook>")
                if progress_callback:
                    progress_callback(1, 1, "Completed")
                return output_path

            mock_translate.side_effect = fake_translate

            # Run document handler
            bot.handle_document(mock_msg)

            # Assertions
            assert bot.bot.download_file.called, "download_file should be called"
            assert mock_translate.called, "translate_fb2 should be called"
            assert bot.bot.send_document.called, "send_document should be called"

            call_args = bot.bot.send_document.call_args
            assert call_args.kwargs["chat_id"] == 123456, "Should send to the correct chat ID"
            assert "test_book_uk.fb2" in call_args.kwargs["visible_file_name"], "Output file name should be test_book_uk.fb2"

            print("HANDLE_DOCUMENT TEST PASSED SUCCESSFULLY!")

finally:
    shutil.rmtree(test_dir, ignore_errors=True)
