import unittest
import tempfile
import os
import time
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

    def test_apply_user_setting(self):
        # Valid settings
        ok, msg = bot.apply_user_setting(222, "delay_req", "2.5")
        self.assertTrue(ok)
        self.assertEqual(bot.user_settings_storage.get_config(222).delay_req, 2.5)

        ok, msg = bot.apply_user_setting(222, "char_limit", "4500")
        self.assertTrue(ok)
        self.assertEqual(bot.user_settings_storage.get_config(222).char_limit, 4500)

        ok, msg = bot.apply_user_setting(222, "temperature", "0.65")
        self.assertTrue(ok)
        self.assertEqual(bot.user_settings_storage.get_config(222).temperature, 0.65)

        # Invalid bounds or non-numbers
        ok, msg = bot.apply_user_setting(222, "temperature", "1.5")
        self.assertFalse(ok)

        ok, msg = bot.apply_user_setting(222, "char_limit", "abc")
        self.assertFalse(ok)

        # Auto pause setting
        ok, msg = bot.apply_user_setting(222, "auto_pause", "true")
        self.assertTrue(ok)
        self.assertTrue(bot.user_settings_storage.get_config(222).auto_pause)

        bot.user_settings_storage.reset(222)

    def test_default_settings_match_photo(self):
        cfg = bot.TranslationConfig()
        self.assertEqual(cfg.char_limit, 12000)
        self.assertEqual(cfg.temperature, 1.0)
        self.assertEqual(cfg.delay_req, 2.0)
        self.assertEqual(cfg.delay_protect, 2.0)
        self.assertEqual(cfg.delay_json, 2.0)
        self.assertEqual(cfg.delay_mismatch, 2.0)
        self.assertEqual(cfg.delay_error, 2.0)
        self.assertTrue(cfg.auto_pause)

    def test_task_keyboard_has_download_button(self):
        kb_running = bot.get_task_keyboard(is_paused=False)
        buttons = [btn.callback_data for row in kb_running.keyboard for btn in row]
        self.assertIn("task_download", buttons)
        self.assertIn("task_pause", buttons)
        self.assertIn("task_stop", buttons)

        kb_paused = bot.get_task_keyboard(is_paused=True)
        buttons_paused = [btn.callback_data for row in kb_paused.keyboard for btn in row]
        self.assertIn("task_download", buttons_paused)
        self.assertIn("task_resume", buttons_paused)

    def test_task_download_callback(self):
        chat_id = 998877
        output_file = os.path.join(self.test_dir, "test_snapshot.fb2")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("<FictionBook><body><p>Current translation snapshot</p></body></FictionBook>")

        mock_call = MagicMock()
        mock_call.id = "call_123"
        mock_call.message.chat.id = chat_id
        mock_call.from_user.id = 111222
        mock_call.data = "task_download"

        task = bot.ActiveTask(
            chat_id=chat_id,
            status_msg_id=123,
            file_name="my_book.fb2",
            config=bot.TranslationConfig(),
            output_path=output_file
        )
        task.current_idx = 10
        task.total_elements = 50

        with bot.tasks_lock:
            bot.active_tasks[chat_id] = task

        bot.bot.answer_callback_query = MagicMock()
        bot.bot.send_document = MagicMock()

        try:
            bot.handle_callbacks(mock_call)
            # Give daemon thread a brief moment to run send_document
            time.sleep(0.1)

            self.assertTrue(bot.bot.answer_callback_query.called)
            self.assertTrue(bot.bot.send_document.called)
            call_kwargs = bot.bot.send_document.call_args.kwargs
            self.assertEqual(call_kwargs["chat_id"], chat_id)
            self.assertIn("my_book_поточний_10_з_50.fb2", call_kwargs["visible_file_name"])
        finally:
            with bot.tasks_lock:
                bot.active_tasks.pop(chat_id, None)


if __name__ == "__main__":
    unittest.main()


