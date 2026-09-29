import unittest
import tempfile
import os
import shutil
from unittest.mock import MagicMock, patch
import bot
from translator_core import TranslationCancelled


class TestTelegramBot(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.dummy_fb2 = os.path.join(self.test_dir, "test_book.fb2")
        with open(self.dummy_fb2, "w", encoding="utf-8") as f:
            f.write("""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <body><p>Hello world</p></body>
</FictionBook>""")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_handle_document_success(self):
        mock_msg = MagicMock()
        mock_msg.chat.id = 123456
        mock_msg.from_user.id = 987654
        mock_msg.document.file_name = "test_book.fb2"
        mock_msg.document.file_size = 1024
        mock_msg.document.file_id = "test_file_id"

        bot.bot.get_file = MagicMock(return_value=MagicMock(file_path="remote/path/test_book.fb2"))
        with open(self.dummy_fb2, "rb") as f:
            content_bytes = f.read()
        bot.bot.download_file = MagicMock(return_value=content_bytes)
        bot.bot.reply_to = MagicMock(return_value=MagicMock(message_id=999))
        bot.bot.edit_message_text = MagicMock()
        bot.bot.send_document = MagicMock()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy_key"}):
            with patch("bot.translate_fb2") as mock_translate:
                def fake_translate(input_path, output_path, progress_callback=None, **kwargs):
                    with open(output_path, "w", encoding="utf-8") as out:
                        out.write("<FictionBook><body><p>Привіт світ</p></body></FictionBook>")
                    if progress_callback:
                        progress_callback(1, 1, "Completed")
                    if "on_request" in kwargs and kwargs["on_request"]:
                        kwargs["on_request"]()
                    return output_path

                mock_translate.side_effect = fake_translate

                bot.handle_document(mock_msg)

                self.assertTrue(bot.bot.download_file.called)
                self.assertTrue(mock_translate.called)
                self.assertTrue(bot.bot.send_document.called)

                call_args = bot.bot.send_document.call_args
                self.assertEqual(call_args.kwargs["chat_id"], 123456)
                self.assertEqual(call_args.kwargs["visible_file_name"], "test_book_uk.fb2")

    def test_handle_document_cancellation(self):
        mock_msg = MagicMock()
        mock_msg.chat.id = 123456
        mock_msg.from_user.id = 987654
        mock_msg.document.file_name = "test_cancel.fb2"
        mock_msg.document.file_size = 1024
        mock_msg.document.file_id = "test_file_id"

        bot.bot.get_file = MagicMock(return_value=MagicMock(file_path="remote/test_cancel.fb2"))
        with open(self.dummy_fb2, "rb") as f:
            content_bytes = f.read()
        bot.bot.download_file = MagicMock(return_value=content_bytes)
        bot.bot.reply_to = MagicMock(return_value=MagicMock(message_id=888))
        bot.bot.edit_message_text = MagicMock()
        bot.bot.send_document = MagicMock()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy_key"}):
            with patch("bot.translate_fb2") as mock_translate:
                mock_translate.side_effect = TranslationCancelled("Cancelled by user")

                bot.handle_document(mock_msg)

                # Ensure active_tasks is cleaned up
                with bot.tasks_lock:
                    self.assertNotIn(123456, bot.active_tasks)

    def test_user_settings_storage(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
            temp_settings_file = f.name

        try:
            storage = bot.SettingsStorage(filepath=temp_settings_file)
            cfg = storage.get_config(111)
            self.assertEqual(cfg.delay_req, 2.0)

            storage.set_value(111, "delay_req", 5.0)
            storage.set_value(111, "char_limit", 8000)

            cfg2 = storage.get_config(111)
            self.assertEqual(cfg2.delay_req, 5.0)
            self.assertEqual(cfg2.char_limit, 8000)

            storage.reset(111)
            cfg3 = storage.get_config(111)
            self.assertEqual(cfg3.delay_req, 2.0)

        finally:
            if os.path.exists(temp_settings_file):
                os.remove(temp_settings_file)

    def test_health_check_endpoint(self):
        with bot.server.test_client() as client:
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)

            resp_health = client.get("/health")
            self.assertEqual(resp_health.status_code, 200)
            data = resp_health.get_json()
            self.assertEqual(data["status"], "healthy")
            self.assertIn("requests_today", data)
            self.assertIn("time_until_reset", data)


if __name__ == "__main__":
    unittest.main()

