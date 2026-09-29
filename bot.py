import os
import time
import shutil
import logging
import tempfile
import threading
from flask import Flask, jsonify
import telebot
from dotenv import load_dotenv

from translator_core import translate_fb2, DEFAULT_MODEL

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("FB2Bot")

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))

# Initialize Flask server for Render Health Check
server = Flask(__name__)

@server.route("/")
def index():
    return "Stable-FB2-Translator Telegram Bot is running! Health check OK.", 200

@server.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "fb2-translator-bot",
        "gemini_api_configured": bool(os.getenv("GEMINI_API_KEY")),
        "model": os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    }), 200


def run_web_server():
    """Runs the Flask health check server."""
    port = int(os.getenv("PORT", "10000"))
    logger.info(f"Starting Flask health check server on 0.0.0.0:{port}...")
    # Suppress verbose werkzeug request logs
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.WARNING)
    server.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# Initialize Telebot (use dummy placeholder with valid structure if unset)
DUMMY_TOKEN = "0000000000:AAABBBCCCDDDEEEFFF"
valid_token = BOT_TOKEN if (BOT_TOKEN and ":" in BOT_TOKEN) else DUMMY_TOKEN
bot = telebot.TeleBot(valid_token, threaded=True)


def sanitize_filename(filename: str) -> str:
    """Removes path separators and dangerous characters from filename."""
    base = os.path.basename(filename).strip()
    return "".join(c for c in base if c.isalnum() or c in " ._-()[]")


@bot.message_handler(commands=["start"])
def handle_start(message: telebot.types.Message):
    """Sends greeting and usage instructions."""
    text = (
        "📚 **Вітаю у Stable FB2 Translator Bot!**\n\n"
        "Я допоможу вам якісно перекласти книги у форматі **.fb2** на українську мову "
        "за допомогою штучного інтелекту Google Gemini.\n\n"
        "✨ **Особливості:**\n"
        "• Повне збереження структури книги (розділи, підзаголовки, вірші, авторська розмітка).\n"
        "• Розумне пакетне розбиття тексту для швидкого й надійного перекладу.\n"
        "• Відображення прогресу в реальному часі.\n\n"
        "📖 **Як користуватися:**\n"
        "1. Просто надішліть мені файл книги з розширенням `.fb2` як документ.\n"
        "2. Зачекайте завершення перекладу.\n"
        "3. Отримайте готовий перекладений файл!\n\n"
        "Корисні команди:\n"
        "/help — Докладна інструкція\n"
        "/status — Стан сервісу та конфігурація"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["help"])
def handle_help(message: telebot.types.Message):
    """Sends help information."""
    text = (
        "ℹ️ **Довідка щодо роботи бота:**\n\n"
        "• **Підтримуваний формат:** Тільки `.fb2` (FictionBook).\n"
        "• **Розмір файлу:** До 20 МБ (обмеження завантаження Telegram Bot API).\n"
        "• **Мова перекладу:** Українська.\n"
        "• **Модель AI:** Google Gemini (налаштовується через `GEMINI_MODEL`).\n\n"
        "Якщо у вас виникли помилки:\n"
        "1. Переконайтеся, що файл має правильну структуру FB2 XML.\n"
        "2. У разі великих книг переклад може зайняти кілька хвилин через ліміти API.\n"
        "3. Бот автоматично зберігає проміжний прогрес і повторює спроби у разі збоїв."
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["status"])
def handle_status(message: telebot.types.Message):
    """Reports system and configuration status."""
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    status_text = (
        "⚙️ **Стан системи:**\n\n"
        f"• Сервер: Онлайн (порт {PORT})\n"
        f"• Ключ Gemini API: {'✅ Налаштовано' if has_gemini else '❌ Відсутній'}\n"
        f"• Модель Gemini: `{model_name}`\n"
    )
    bot.reply_to(message, status_text, parse_mode="Markdown")


@bot.message_handler(content_types=["document"])
def handle_document(message: telebot.types.Message):
    """Handles incoming document files, validates .fb2, translates, and returns the result."""
    doc = message.document
    original_name = doc.file_name or "book.fb2"
    clean_name = sanitize_filename(original_name)

    # 1. Validate file extension
    if not clean_name.lower().endswith(".fb2"):
        bot.reply_to(
            message,
            "⚠️ **Помилка:** Підтримуються лише файли формату **.fb2**.\n"
            "Будь ласка, надішліть файл з розширенням `.fb2` як документ.",
            parse_mode="Markdown"
        )
        return

    # 2. Check file size (Telegram Bot API limit is 20MB for downloads)
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(
            message,
            "❌ **Помилка:** Розмір файлу перевищує 20 МБ.\n"
            "Telegram Bot API не дозволяє завантажувати файли більші за 20 МБ.",
            parse_mode="Markdown"
        )
        return

    # 3. Check Gemini API key configuration
    if not os.getenv("GEMINI_API_KEY"):
        bot.reply_to(
            message,
            "⚠️ **Помилка конфігурації:** На сервері не налаштовано `GEMINI_API_KEY`.\n"
            "Зверніться до адміністратора або додайте змінну середовища.",
            parse_mode="Markdown"
        )
        return

    status_msg = bot.reply_to(message, f"📥 Завантажую файл `{clean_name}`...", parse_mode="Markdown")

    # 4. Create isolated temporary directory
    temp_dir = tempfile.mkdtemp(prefix="fb2_trans_")
    input_path = os.path.join(temp_dir, clean_name)

    base_name, ext = os.path.splitext(clean_name)
    output_filename = f"{base_name}_uk{ext}"
    output_path = os.path.join(temp_dir, output_filename)

    try:
        # Download document
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        with open(input_path, "wb") as f:
            f.write(downloaded)

        logger.info(f"Downloaded '{clean_name}' ({len(downloaded)} bytes) to {input_path}")

        # Throttled progress updater to respect Telegram API rate limits
        last_update_time = [0.0]
        last_rendered_text = [""]

        def on_progress(current: int, total: int, status_info: str):
            now = time.time()
            # Update at most once every 4 seconds or when completed
            if (now - last_update_time[0] >= 4.0) or (total > 0 and current >= total):
                last_update_time[0] = now
                pct = int((current / total) * 100) if total > 0 else 0
                filled_bar = "▓" * (pct // 10)
                empty_bar = "░" * (10 - (pct // 10))
                msg_body = (
                    f"📖 **Переклад книги:** `{clean_name}`\n\n"
                    f"Прогрес: `[{filled_bar}{empty_bar}]` **{pct}%**\n"
                    f"Абзаців: {current} / {total}\n\n"
                    f"_{status_info}_"
                )
                if msg_body != last_rendered_text[0]:
                    last_rendered_text[0] = msg_body
                    try:
                        bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=status_msg.message_id,
                            text=msg_body,
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

        # Update status before starting translation
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=f"⚙️ Файл `{clean_name}` отримано. Починаю обробку та переклад...",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # 5. Translate using translator_core
        translate_fb2(
            input_path=input_path,
            output_path=output_path,
            progress_callback=on_progress
        )

        # 6. Send translated document back to user
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                text=f"✅ Переклад файлу `{clean_name}` завершено! Надсилаю результат...",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        with open(output_path, "rb") as out_f:
            bot.send_document(
                chat_id=message.chat.id,
                document=out_f,
                visible_file_name=output_filename,
                caption=f"🎉 **Готово!**\n📄 Перекладений файл: `{output_filename}`",
                parse_mode="Markdown"
            )
        logger.info(f"Successfully sent translated file {output_filename} to chat {message.chat.id}")

    except Exception as e:
        logger.exception(f"Error occurred while translating {clean_name}")
        try:
            bot.reply_to(
                message,
                f"❌ **Помилка під час перекладу:**\n`{str(e)[:400]}`",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    finally:
        # 7. Guaranteed deletion of input and output files and temp directory
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Cleaned up temporary directory: {temp_dir}")
        except Exception as cleanup_err:
            logger.warning(f"Failed to remove temp dir {temp_dir}: {cleanup_err}")


@bot.message_handler(func=lambda msg: True)
def handle_other_messages(message: telebot.types.Message):
    """Catches text and other media messages and prompts the user to send an FB2 file."""
    bot.reply_to(
        message,
        "👋 Будь ласка, надішліть файл книги у форматі **.fb2** як документ для початку перекладу.\n"
        "Для перегляду довідки введіть /help.",
        parse_mode="Markdown"
    )


def main():
    """Main entrypoint: starts Flask web server and begins Telegram bot polling."""
    # Start background HTTP server for Render health checks
    flask_thread = threading.Thread(target=run_web_server, daemon=True)
    flask_thread.start()

    # Check if BOT_TOKEN is configured
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or ":" not in token or token.startswith("your_") or token.startswith("0000000000:"):
        logger.error(
            "BOT_TOKEN is not set or contains default placeholder! "
            "Please configure BOT_TOKEN in your environment variables or .env file. "
            "Web server will remain active to pass health checks."
        )
        # Keep process alive so Render health check can pass while awaiting token config
        while True:
            time.sleep(3600)

    logger.info("Starting Telegram bot polling...")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
