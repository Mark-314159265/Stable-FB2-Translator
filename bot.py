import os
import time
import json
import shutil
import logging
import tempfile
import threading
from typing import Dict, Any, Optional, Tuple
from flask import Flask, jsonify
import telebot
from dotenv import load_dotenv

from translator_core import (
    translate_fb2,
    TranslationConfig,
    TranslationCancelled,
    global_stats,
    DEFAULT_MODEL,
    DEFAULT_CHAR_LIMIT,
    DEFAULT_TEMPERATURE,
    DEFAULT_DELAY_REQ,
    DEFAULT_DELAY_PROTECT,
    DEFAULT_DELAY_ERROR
)

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
USER_SETTINGS_FILE = os.getenv("USER_SETTINGS_FILE", "user_settings.json")

# Initialize Flask server for Render Health Check
server = Flask(__name__)

@server.route("/")
def index():
    return "Stable-FB2-Translator Telegram Bot is running! Health check OK.", 200

@server.route("/health")
def health():
    stats = global_stats.get_stats()
    return jsonify({
        "status": "healthy",
        "service": "fb2-translator-bot",
        "gemini_api_configured": bool(os.getenv("GEMINI_API_KEY")),
        "model": os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        "requests_today": stats["requests_today"],
        "daily_limit": stats["daily_limit"],
        "time_until_reset": stats["time_until_reset"]
    }), 200


def run_web_server():
    """Runs the Flask health check server."""
    port = int(os.getenv("PORT", "10000"))
    logger.info(f"Starting Flask health check server on 0.0.0.0:{port}...")
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.WARNING)
    server.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# Initialize Telebot
DUMMY_TOKEN = "0000000000:AAABBBCCCDDDEEEFFF"
valid_token = BOT_TOKEN if (BOT_TOKEN and ":" in BOT_TOKEN) else DUMMY_TOKEN
bot = telebot.TeleBot(valid_token, threaded=True)


# --- User Settings Persistence ---
class SettingsStorage:
    def __init__(self, filepath: str = USER_SETTINGS_FILE):
        self.filepath = filepath
        self._lock = threading.Lock()
        self.settings: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read settings from {self.filepath}: {e}")
        return {}

    def _save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save settings to {self.filepath}: {e}")

    def get_config(self, user_id: int) -> TranslationConfig:
        with self._lock:
            user_data = self.settings.get(str(user_id), {})
            return TranslationConfig.from_dict(user_data)

    def set_value(self, user_id: int, key: str, value: Any):
        with self._lock:
            uid = str(user_id)
            if uid not in self.settings:
                self.settings[uid] = {}
            self.settings[uid][key] = value
            self._save()

    def reset(self, user_id: int):
        with self._lock:
            uid = str(user_id)
            if uid in self.settings:
                del self.settings[uid]
                self._save()


user_settings_storage = SettingsStorage()

# State for custom manual number input from user: user_id -> parameter name
user_input_state: Dict[int, str] = {}
user_input_lock = threading.Lock()


def apply_user_setting(user_id: int, param: str, raw_val: str) -> Tuple[bool, str]:
    """Validates and sets a custom parameter value for a user."""
    cleaned = raw_val.replace(",", ".").strip()
    try:
        if param == "char_limit":
            val = int(cleaned)
            if not (500 <= val <= 30000):
                return False, "❌ **Ліміт символів** має бути цілим числом від `500` до `30000`."
        elif param in ["delay_req", "delay_protect", "delay_error"]:
            val = float(cleaned)
            min_v = 0.5 if param == "delay_req" else 1.0
            max_v = 120.0 if param == "delay_error" else 60.0
            if not (min_v <= val <= max_v):
                return False, f"❌ **Затримка** має бути в межах від `{min_v}` до `{max_v}` секунд."
        elif param == "temperature":
            val = float(cleaned)
            if not (0.0 <= val <= 1.0):
                return False, "❌ **Температура** має бути числом від `0.0` до `1.0`."
        else:
            return False, "❌ Невідомий параметр."

        user_settings_storage.set_value(user_id, param, val)
        names = {
            "delay_req": f"⏱ Базова затримка: `{val} с`",
            "delay_protect": f"🛡 Пауза захисту: `{val} с`",
            "delay_error": f"⚠️ Пауза помилок: `{val} с`",
            "char_limit": f"🔤 Ліміт символів: `{val}`",
            "temperature": f"🌡 Температура: `{val}`"
        }
        return True, f"✅ **Налаштування успішно збережено:**\n{names.get(param, str(val))}"
    except ValueError:
        return False, "❌ Будь ласка, введіть дійсне число (наприклад: `2.5` або `5000`)."


# --- Active Translation Tasks Management ---
class ActiveTask:
    def __init__(self, chat_id: int, status_msg_id: int, file_name: str, config: TranslationConfig):
        self.chat_id = chat_id
        self.status_msg_id = status_msg_id
        self.file_name = file_name
        self.config = config
        self.pause_event = threading.Event()
        self.pause_event.set()  # running initially
        self.cancel_event = threading.Event()
        self.is_paused = False
        self.file_requests = 0
        self.current_idx = 0
        self.total_elements = 0
        self.last_status_text = "Початок роботи..."


# Keyed by chat_id
active_tasks: Dict[int, ActiveTask] = {}
tasks_lock = threading.Lock()


def sanitize_filename(filename: str) -> str:
    """Removes path separators and dangerous characters from filename."""
    base = os.path.basename(filename).strip()
    return "".join(c for c in base if c.isalnum() or c in " ._-()[]")


def get_task_keyboard(is_paused: bool) -> telebot.types.InlineKeyboardMarkup:
    """Creates Pause/Resume and Stop inline buttons for active translation."""
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    if is_paused:
        btn_toggle = telebot.types.InlineKeyboardButton("▶️ Продовжити", callback_data="task_resume")
    else:
        btn_toggle = telebot.types.InlineKeyboardButton("⏸ Пауза", callback_data="task_pause")
    btn_stop = telebot.types.InlineKeyboardButton("🛑 Зупинити", callback_data="task_stop")
    markup.add(btn_toggle, btn_stop)
    return markup


# --- Bot Command Handlers ---
@bot.message_handler(commands=["start"])
def handle_start(message: telebot.types.Message):
    """Sends greeting and usage instructions."""
    text = (
        "📚 **Stable FB2 Translator Bot**\n\n"
        "Я допоможу вам якісно перекласти книги у форматі **.fb2** на українську мову "
        "за допомогою Google Gemini API.\n\n"
        "✨ **Можливості програми:**\n"
        "• **Пауза та відновлення**: ставте переклад на паузу та відновлюйте у будь-який момент.\n"
        "• **Лічильник запитів**: моніторинг щоденного використання та часу скидання ліміту.\n"
        "• **Гнучкі налаштування**: кнопки та ручне введення чисел для будь-яких затримок через /settings.\n"
        "• **Збереження структури**: книга зберігає всі розділи, розмітку та вірші.\n\n"
        "📖 **Як користуватися:**\n"
        "1. Надішліть файл книги з розширенням `.fb2` як документ.\n"
        "2. Під час перекладу використовуйте кнопки **⏸ Пауза** чи **🛑 Зупинити**.\n"
        "3. Отримайте готовий перекладений файл!\n\n"
        "⚙️ **Команди:**\n"
        "/settings — Меню налаштувань (затримки, ліміти, модель)\n"
        "/set <параметр> <значення> — Швидке встановлення значення числом\n"
        "/status — Лічильник запитів та стан сервісу\n"
        "/help — Докладна довідка"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["help"])
def handle_help(message: telebot.types.Message):
    """Sends detailed help information."""
    text = (
        "ℹ️ **Довідка щодо роботи бота:**\n\n"
        "• **Формат:** Тільки `.fb2` (FictionBook 2.0).\n"
        "• **Розмір файлу:** До 20 МБ (ліміт Telegram Bot API).\n\n"
        "• **Керування перекладом:**\n"
        "  - `⏸ Пауза` — тимчасово призупиняє надсилання запитів.\n"
        "  - `▶️ Продовжити` — продовжує роботу з того ж місця.\n"
        "  - `🛑 Зупинити` — перериває процес та повертає частково перекладений файл.\n\n"
        "• **Налаштування затримок:**\n"
        "  Ви можете використовувати зручне меню /settings або пряму команду `/set`:\n"
        "  - `/set base 2.5` — базова затримка між запитами (с)\n"
        "  - `/set protect 5.0` — пауза при спрацюванні фільтрів (с)\n"
        "  - `/set error 10.0` — пауза при збої або ліміті 429 (с)\n"
        "  - `/set limit 6000` — ліміт символів на пакет\n"
        "  - `/set temp 0.3` — температура моделі (від 0.0 до 1.0)"
    )
    bot.reply_to(message, text, parse_mode="Markdown")


@bot.message_handler(commands=["status", "stats"])
def handle_status(message: telebot.types.Message):
    """Reports system status, daily request counter, and reset countdown."""
    stats = global_stats.get_stats()
    cfg = user_settings_storage.get_config(message.from_user.id)
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))

    with tasks_lock:
        active_count = len(active_tasks)

    status_text = (
        "📊 **Стан системи та статистика запитів:**\n\n"
        f"• **Сервер:** Онлайн (порт {PORT})\n"
        f"• **Ключ Gemini API:** {'✅ Налаштовано' if has_gemini else '❌ Відсутній'}\n"
        f"• **Модель:** `{cfg.model}`\n"
        f"• **Активних перекладів:** {active_count}\n\n"
        f"📈 **Лічильник запитів сьогодні:**\n"
        f"• **Використано:** `{stats['requests_today']} / {stats['daily_limit']}` запитів\n"
        f"• **Скидання квоти через:** `{stats['time_until_reset']}` (о {stats['reset_time_local']})\n\n"
        f"⚙️ **Ваші персональні налаштування:**\n"
        f"• Базова пауза: `{cfg.delay_req} с`\n"
        f"• Пауза захисту: `{cfg.delay_protect} с`\n"
        f"• Пауза помилок: `{cfg.delay_error} с`\n"
        f"• Ліміт символів: `{cfg.char_limit}`\n"
        f"• Температура: `{cfg.temperature}`\n\n"
        "Змінити параметри: /settings"
    )
    bot.reply_to(message, status_text, parse_mode="Markdown")


@bot.message_handler(commands=["cancel"])
def handle_cancel(message: telebot.types.Message):
    """Cancels active text input mode."""
    user_id = message.from_user.id
    with user_input_lock:
        was_waiting = user_input_state.pop(user_id, None)

    if was_waiting:
        bot.reply_to(message, "❌ Введення значення скасовано.")
        text, markup = build_settings_menu(user_id)
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.reply_to(message, "Немає активного очікування введення.")


@bot.message_handler(commands=["set"])
def handle_set_command(message: telebot.types.Message):
    """Sets a configuration parameter directly via command: /set <param> <value>."""
    parts = message.text.strip().split()
    if len(parts) != 3:
        bot.reply_to(
            message,
            "ℹ️ **Формат команди:** `/set <параметр> <значення>`\n\n"
            "Приклади:\n"
            "• `/set base 2.5` — базова пауза (с)\n"
            "• `/set protect 7.0` — пауза захисту (с)\n"
            "• `/set error 15.0` — пауза збоїв (с)\n"
            "• `/set limit 5000` — ліміт символів\n"
            "• `/set temp 0.4` — температура",
            parse_mode="Markdown"
        )
        return

    param_raw, val_raw = parts[1].lower(), parts[2]
    param_map = {
        "base": "delay_req",
        "delay": "delay_req",
        "delay_req": "delay_req",
        "protect": "delay_protect",
        "delay_protect": "delay_protect",
        "error": "delay_error",
        "delay_error": "delay_error",
        "limit": "char_limit",
        "char_limit": "char_limit",
        "temp": "temperature",
        "temperature": "temperature"
    }
    param = param_map.get(param_raw)
    if not param:
        bot.reply_to(message, f"❌ Невідомий параметр `{param_raw}`. Доступні: `base`, `protect`, `error`, `limit`, `temp`.", parse_mode="Markdown")
        return

    success, msg = apply_user_setting(message.from_user.id, param, val_raw)
    bot.reply_to(message, msg, parse_mode="Markdown")


# --- Settings UI Handlers ---
def build_settings_menu(user_id: int):
    """Builds the main settings text and inline markup."""
    cfg = user_settings_storage.get_config(user_id)
    stats = global_stats.get_stats()

    text = (
        "⚙️ **Налаштування перекладу:**\n\n"
        f"⏱ **Базова пауза між запитами:** `{cfg.delay_req}` с\n"
        f"🛡 **Пауза захисту (фільтри):** `{cfg.delay_protect}` с\n"
        f"⚠️ **Пауза при помилках / 429:** `{cfg.delay_error}` с\n"
        f"🔤 **Ліміт символів у пакеті:** `{cfg.char_limit}`\n"
        f"🌡 **Температура генерації:** `{cfg.temperature}`\n"
        f"🤖 **Модель AI:** `{cfg.model}`\n\n"
        f"📊 **Запитів сьогодні:** `{stats['requests_today']} / {stats['daily_limit']}` "
        f"(скидання через `{stats['time_until_reset']}`)\n\n"
        "Оберіть параметр для налаштування (кнопкою або введенням числа):"
    )

    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    b1 = telebot.types.InlineKeyboardButton("⏱ Базова пауза", callback_data="cfg_menu_delay_req")
    b2 = telebot.types.InlineKeyboardButton("🛡 Пауза захисту", callback_data="cfg_menu_delay_protect")
    b3 = telebot.types.InlineKeyboardButton("⚠️ Пауза помилок", callback_data="cfg_menu_delay_error")
    b4 = telebot.types.InlineKeyboardButton("🔤 Ліміт символів", callback_data="cfg_menu_char_limit")
    b5 = telebot.types.InlineKeyboardButton("🌡 Температура", callback_data="cfg_menu_temperature")
    b6 = telebot.types.InlineKeyboardButton("🤖 Модель AI", callback_data="cfg_menu_model")
    b_reset = telebot.types.InlineKeyboardButton("🔄 Скинути до стандартних", callback_data="cfg_reset")
    b_close = telebot.types.InlineKeyboardButton("❌ Закрити", callback_data="cfg_close")

    markup.add(b1, b2, b3, b4, b5, b6)
    markup.row(b_reset)
    markup.row(b_close)
    return text, markup


@bot.message_handler(commands=["settings"])
def handle_settings(message: telebot.types.Message):
    """Displays user settings menu with interactive buttons."""
    text, markup = build_settings_menu(message.from_user.id)
    bot.reply_to(message, text, reply_markup=markup, parse_mode="Markdown")


# --- Callback Query Handler (Tasks & Settings) ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call: telebot.types.CallbackQuery):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    data = call.data

    # 1. Active Task Controls (Pause / Resume / Stop)
    if data in ["task_pause", "task_resume", "task_stop"]:
        with tasks_lock:
            task = active_tasks.get(chat_id)

        if not task:
            bot.answer_callback_query(call.id, "Переклад уже завершено або не знайдено.")
            return

        if data == "task_pause":
            task.pause_event.clear()
            task.is_paused = True
            bot.answer_callback_query(call.id, "⏸ Переклад призупинено")
            try:
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=task.status_msg_id,
                    reply_markup=get_task_keyboard(is_paused=True)
                )
            except Exception:
                pass

        elif data == "task_resume":
            task.pause_event.set()
            task.is_paused = False
            bot.answer_callback_query(call.id, "▶️ Переклад відновлено")
            try:
                bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=task.status_msg_id,
                    reply_markup=get_task_keyboard(is_paused=False)
                )
            except Exception:
                pass

        elif data == "task_stop":
            task.cancel_event.set()
            task.pause_event.set()  # unpause if was waiting so it cancels promptly
            bot.answer_callback_query(call.id, "🛑 Зупиняю переклад...")
            try:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=task.status_msg_id,
                    text=f"🛑 Зупинка перекладу файлу `{task.file_name}`...",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        return

    # 2. Settings Menu Actions
    if data == "cfg_main":
        with user_input_lock:
            user_input_state.pop(user_id, None)
        text, markup = build_settings_menu(user_id)
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        return

    if data == "cfg_close":
        with user_input_lock:
            user_input_state.pop(user_id, None)
        try:
            bot.delete_message(chat_id=chat_id, message_id=call.message.message_id)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Налаштування закрито.")
        return

    if data == "cfg_reset":
        user_settings_storage.reset(user_id)
        bot.answer_callback_query(call.id, "Налаштування скинуто до початкових!")
        text, markup = build_settings_menu(user_id)
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        return

    # Submenus for parameters
    if data == "cfg_menu_delay_req":
        cfg = user_settings_storage.get_config(user_id)
        text = f"⏱ **Базова затримка між успішними запитами:**\nПоточне: `{cfg.delay_req}` с\n\nОберіть швидкий варіант або введіть своє число:"
        markup = telebot.types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            telebot.types.InlineKeyboardButton("1.0 с", callback_data="set_delay_req_1.0"),
            telebot.types.InlineKeyboardButton("2.0 с", callback_data="set_delay_req_2.0"),
            telebot.types.InlineKeyboardButton("3.0 с", callback_data="set_delay_req_3.0"),
            telebot.types.InlineKeyboardButton("5.0 с", callback_data="set_delay_req_5.0"),
            telebot.types.InlineKeyboardButton("10.0 с", callback_data="set_delay_req_10.0"),
        )
        markup.row(telebot.types.InlineKeyboardButton("✏️ Ввести власне число", callback_data="cfg_input_delay_req"))
        markup.row(telebot.types.InlineKeyboardButton("« Назад", callback_data="cfg_main"))
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "cfg_menu_delay_protect":
        cfg = user_settings_storage.get_config(user_id)
        text = f"🛡 **Пауза при спрацюванні фільтрів безпеки:**\nПоточне: `{cfg.delay_protect}` с\n\nОберіть швидкий варіант або введіть своє число:"
        markup = telebot.types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            telebot.types.InlineKeyboardButton("2.0 с", callback_data="set_delay_protect_2.0"),
            telebot.types.InlineKeyboardButton("5.0 с", callback_data="set_delay_protect_5.0"),
            telebot.types.InlineKeyboardButton("10.0 с", callback_data="set_delay_protect_10.0"),
            telebot.types.InlineKeyboardButton("15.0 с", callback_data="set_delay_protect_15.0"),
        )
        markup.row(telebot.types.InlineKeyboardButton("✏️ Ввести власне число", callback_data="cfg_input_delay_protect"))
        markup.row(telebot.types.InlineKeyboardButton("« Назад", callback_data="cfg_main"))
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "cfg_menu_delay_error":
        cfg = user_settings_storage.get_config(user_id)
        text = f"⚠️ **Пауза при збоях сервера або 429:**\nПоточне: `{cfg.delay_error}` с\n\nОберіть швидкий варіант або введіть своє число:"
        markup = telebot.types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            telebot.types.InlineKeyboardButton("5.0 с", callback_data="set_delay_error_5.0"),
            telebot.types.InlineKeyboardButton("10.0 с", callback_data="set_delay_error_10.0"),
            telebot.types.InlineKeyboardButton("15.0 с", callback_data="set_delay_error_15.0"),
            telebot.types.InlineKeyboardButton("20.0 с", callback_data="set_delay_error_20.0"),
        )
        markup.row(telebot.types.InlineKeyboardButton("✏️ Ввести власне число", callback_data="cfg_input_delay_error"))
        markup.row(telebot.types.InlineKeyboardButton("« Назад", callback_data="cfg_main"))
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "cfg_menu_char_limit":
        cfg = user_settings_storage.get_config(user_id)
        text = f"🔤 **Ліміт символів на один пакет:**\nПоточне: `{cfg.char_limit}`\n\nОберіть швидкий варіант або введіть своє число:"
        markup = telebot.types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            telebot.types.InlineKeyboardButton("3000", callback_data="set_char_limit_3000"),
            telebot.types.InlineKeyboardButton("4000", callback_data="set_char_limit_4000"),
            telebot.types.InlineKeyboardButton("6000", callback_data="set_char_limit_6000"),
            telebot.types.InlineKeyboardButton("8000", callback_data="set_char_limit_8000"),
            telebot.types.InlineKeyboardButton("10000", callback_data="set_char_limit_10000"),
        )
        markup.row(telebot.types.InlineKeyboardButton("✏️ Ввести власне число", callback_data="cfg_input_char_limit"))
        markup.row(telebot.types.InlineKeyboardButton("« Назад", callback_data="cfg_main"))
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "cfg_menu_temperature":
        cfg = user_settings_storage.get_config(user_id)
        text = f"🌡 **Температура генерації (0.0 — точний переклад, 1.0 — вільний):**\nПоточне: `{cfg.temperature}`\n\nОберіть швидкий варіант або введіть своє число:"
        markup = telebot.types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            telebot.types.InlineKeyboardButton("0.0", callback_data="set_temp_0.0"),
            telebot.types.InlineKeyboardButton("0.2", callback_data="set_temp_0.2"),
            telebot.types.InlineKeyboardButton("0.3", callback_data="set_temp_0.3"),
            telebot.types.InlineKeyboardButton("0.5", callback_data="set_temp_0.5"),
            telebot.types.InlineKeyboardButton("0.7", callback_data="set_temp_0.7"),
            telebot.types.InlineKeyboardButton("1.0", callback_data="set_temp_1.0"),
        )
        markup.row(telebot.types.InlineKeyboardButton("✏️ Ввести власне число", callback_data="cfg_input_temperature"))
        markup.row(telebot.types.InlineKeyboardButton("« Назад", callback_data="cfg_main"))
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "cfg_menu_model":
        text = "🤖 **Оберіть модель Google Gemini:**"
        markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            telebot.types.InlineKeyboardButton("models/gemini-3.1-flash-lite", callback_data="set_model_gemini-3.1-flash-lite"),
            telebot.types.InlineKeyboardButton("models/gemini-2.5-flash", callback_data="set_model_gemini-2.5-flash"),
            telebot.types.InlineKeyboardButton("models/gemini-2.0-flash", callback_data="set_model_gemini-2.0-flash"),
        )
        markup.row(telebot.types.InlineKeyboardButton("« Назад", callback_data="cfg_main"))
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    # User clicked "Enter custom number"
    if data.startswith("cfg_input_"):
        param = data.replace("cfg_input_", "")
        with user_input_lock:
            user_input_state[user_id] = param

        prompts = {
            "delay_req": "⏱ **Введіть бажану базову затримку в секундах**\n(наприклад: `2.5`, діапазон: від `0.5` до `60.0` с):",
            "delay_protect": "🛡 **Введіть паузу захисту в секундах**\n(наприклад: `7.0`, діапазон: від `1.0` до `60.0` с):",
            "delay_error": "⚠️ **Введіть паузу помилок в секундах**\n(наприклад: `12.0`, діапазон: від `1.0` до `120.0` с):",
            "char_limit": "🔤 **Введіть ліміт символів у пакеті**\n(наприклад: `5000`, діапазон: від `500` до `30000`):",
            "temperature": "🌡 **Введіть температуру генерації**\n(наприклад: `0.35`, діапазон: від `0.0` до `1.0`):"
        }
        prompt_text = prompts.get(param, "Введіть числове значення:")
        cancel_markup = telebot.types.InlineKeyboardMarkup()
        cancel_markup.row(telebot.types.InlineKeyboardButton("« Скасувати", callback_data="cfg_main"))

        bot.edit_message_text(
            f"{prompt_text}\n\n_Надішліть повідомлення з числом у цей чат (або натисніть «Скасувати»)._",
            chat_id=chat_id,
            message_id=call.message.message_id,
            reply_markup=cancel_markup,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
        return

    # Applying predefined button values
    if data.startswith("set_delay_req_"):
        val = float(data.replace("set_delay_req_", ""))
        user_settings_storage.set_value(user_id, "delay_req", val)
        bot.answer_callback_query(call.id, f"Базову паузу встановлено на {val} с")
    elif data.startswith("set_delay_protect_"):
        val = float(data.replace("set_delay_protect_", ""))
        user_settings_storage.set_value(user_id, "delay_protect", val)
        bot.answer_callback_query(call.id, f"Паузу захисту встановлено на {val} с")
    elif data.startswith("set_delay_error_"):
        val = float(data.replace("set_delay_error_", ""))
        user_settings_storage.set_value(user_id, "delay_error", val)
        bot.answer_callback_query(call.id, f"Паузу помилок встановлено на {val} с")
    elif data.startswith("set_char_limit_"):
        val = int(data.replace("set_char_limit_", ""))
        user_settings_storage.set_value(user_id, "char_limit", val)
        bot.answer_callback_query(call.id, f"Ліміт символів встановлено на {val}")
    elif data.startswith("set_temp_"):
        val = float(data.replace("set_temp_", ""))
        user_settings_storage.set_value(user_id, "temperature", val)
        bot.answer_callback_query(call.id, f"Температуру встановлено на {val}")
    elif data.startswith("set_model_"):
        val = f"models/{data.replace('set_model_', '')}"
        user_settings_storage.set_value(user_id, "model", val)
        bot.answer_callback_query(call.id, f"Модель змінено на {val}")

    # Return to main settings view
    text, markup = build_settings_menu(user_id)
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass


# --- Document Processing ---
@bot.message_handler(content_types=["document"])
def handle_document(message: telebot.types.Message):
    """Handles incoming document files, validates .fb2, translates, and returns the result."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    doc = message.document
    original_name = doc.file_name or "book.fb2"
    clean_name = sanitize_filename(original_name)

    # 1. Check if another translation is already running in this chat
    with tasks_lock:
        if chat_id in active_tasks:
            bot.reply_to(
                message,
                f"⚠️ У вас уже виконується переклад файлу `{active_tasks[chat_id].file_name}`.\n"
                "Будь ласка, зачекайте завершення або зупиніть поточний переклад кнопкою 🛑 під повідомленням прогресу.",
                parse_mode="Markdown"
            )
            return

    # 2. Validate file extension
    if not clean_name.lower().endswith(".fb2"):
        bot.reply_to(
            message,
            "⚠️ **Помилка:** Підтримуються лише файли формату **.fb2**.\n"
            "Будь ласка, надішліть файл з розширенням `.fb2` як документ.",
            parse_mode="Markdown"
        )
        return

    # 3. Check file size (Telegram Bot API limit is 20MB for downloads)
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(
            message,
            "❌ **Помилка:** Розмір файлу перевищує 20 МБ.\n"
            "Telegram Bot API не підтримує завантаження файлів більших за 20 МБ.",
            parse_mode="Markdown"
        )
        return

    # 4. Check Gemini API key configuration
    if not os.getenv("GEMINI_API_KEY"):
        bot.reply_to(
            message,
            "⚠️ **Помилка конфігурації:** На сервері не налаштовано `GEMINI_API_KEY`.\n"
            "Зверніться до адміністратора або додайте змінну середовища.",
            parse_mode="Markdown"
        )
        return

    # Fetch user specific settings
    user_cfg = user_settings_storage.get_config(user_id)

    # Initial status message with controls
    status_msg = bot.reply_to(
        message,
        f"📥 Завантажую файл `{clean_name}`...\nЗачекайте...",
        parse_mode="Markdown",
        reply_markup=get_task_keyboard(is_paused=False)
    )

    # Register active task
    task = ActiveTask(chat_id, status_msg.message_id, clean_name, user_cfg)
    with tasks_lock:
        active_tasks[chat_id] = task

    # Dedicated temp directory
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

        # Throttled progress updater with live request counters and reset timer
        last_update_time = [0.0]
        last_rendered_text = [""]

        def on_request():
            task.file_requests += 1

        def on_progress(current: int, total: int, status_info: str):
            task.current_idx = current
            task.total_elements = total
            task.last_status_text = status_info

            now = time.time()
            if (now - last_update_time[0] >= 3.5) or (total > 0 and current >= total):
                last_update_time[0] = now
                pct = int((current / total) * 100) if total > 0 else 0
                filled_bar = "▓" * (pct // 10)
                empty_bar = "░" * (10 - (pct // 10))

                stats = global_stats.get_stats()
                pause_label = "⏸ **(Призупинено)**\n" if task.is_paused else ""

                msg_body = (
                    f"{pause_label}📖 **Переклад книги:** `{clean_name}`\n\n"
                    f"Прогрес: `[{filled_bar}{empty_bar}]` **{pct}%**\n"
                    f"Абзаців: {current} / {total}\n"
                    f"Запитів: {task.file_requests} (сьогодні: {stats['requests_today']}/{stats['daily_limit']})\n"
                    f"Скидання квоти: `{stats['time_until_reset']}` (о {stats['reset_time_local']})\n\n"
                    f"_{status_info}_"
                )
                if msg_body != last_rendered_text[0]:
                    last_rendered_text[0] = msg_body
                    try:
                        bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_msg.message_id,
                            text=msg_body,
                            parse_mode="Markdown",
                            reply_markup=get_task_keyboard(is_paused=task.is_paused)
                        )
                    except Exception:
                        pass

        # 5. Translate using translator_core with user configuration, pause_event, cancel_event, and request counter
        translate_fb2(
            input_path=input_path,
            output_path=output_path,
            config=user_cfg,
            progress_callback=on_progress,
            pause_event=task.pause_event,
            cancel_event=task.cancel_event,
            on_request=on_request
        )

        # 6. Send translated document back to user
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"✅ **Переклад файлу `{clean_name}` завершено!**\nВикористано запитів: {task.file_requests}\nНадсилаю результат...",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        with open(output_path, "rb") as out_f:
            bot.send_document(
                chat_id=chat_id,
                document=out_f,
                visible_file_name=output_filename,
                caption=f"🎉 **Готово!**\n📄 Перекладений файл: `{output_filename}`\nВикористано запитів: {task.file_requests}",
                parse_mode="Markdown"
            )
        logger.info(f"Successfully sent translated file {output_filename} to chat {chat_id}")

    except TranslationCancelled:
        logger.info(f"Translation of {clean_name} was cancelled by user {chat_id}")
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"🛑 **Переклад файлу `{clean_name}` зупинено користувачем.**\nОпрацьовано абзаців: {task.current_idx}/{task.total_elements}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # Send partial file if elements were translated
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0 and task.current_idx > 0:
            try:
                partial_name = f"{base_name}_partial{ext}"
                with open(output_path, "rb") as out_f:
                    bot.send_document(
                        chat_id=chat_id,
                        document=out_f,
                        visible_file_name=partial_name,
                        caption=f"📄 Частково перекладений файл: `{partial_name}` ({task.current_idx}/{task.total_elements} абзаців)",
                        parse_mode="Markdown"
                    )
            except Exception as send_err:
                logger.warning(f"Failed to send partial file: {send_err}")

    except Exception as e:
        logger.exception(f"Error occurred while translating {clean_name}")
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"❌ **Помилка під час перекладу:**\n`{str(e)[:400]}`",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    finally:
        # Unregister active task
        with tasks_lock:
            active_tasks.pop(chat_id, None)

        # 7. Guaranteed deletion of input and output files and temp directory
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Cleaned up temporary directory: {temp_dir}")
        except Exception as cleanup_err:
            logger.warning(f"Failed to remove temp dir {temp_dir}: {cleanup_err}")


@bot.message_handler(func=lambda msg: True)
def handle_other_messages(message: telebot.types.Message):
    """Handles text input: custom numerical parameter values or general prompts."""
    user_id = message.from_user.id
    with user_input_lock:
        active_param = user_input_state.get(user_id)

    if active_param:
        success, msg = apply_user_setting(user_id, active_param, message.text.strip())
        if success:
            with user_input_lock:
                user_input_state.pop(user_id, None)
            bot.reply_to(message, msg, parse_mode="Markdown")
            text, markup = build_settings_menu(user_id)
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.reply_to(message, f"{msg}\n_Спробуйте ще раз або введіть /cancel для скасування._", parse_mode="Markdown")
        return

    bot.reply_to(
        message,
        "👋 Надішліть файл книги у форматі **.fb2** як документ для початку перекладу.\n"
        "Налаштування затримок: /settings (або команда `/set <параметр> <значення>`)\n"
        "Лічильник запитів: /status",
        parse_mode="Markdown"
    )


def main():
    """Main entrypoint: starts Flask web server and begins Telegram bot polling."""
    flask_thread = threading.Thread(target=run_web_server, daemon=True)
    flask_thread.start()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or ":" not in token or token.startswith("your_") or token.startswith("0000000000:"):
        logger.error(
            "BOT_TOKEN is not set or contains default placeholder! "
            "Please configure BOT_TOKEN in your environment variables or .env file. "
            "Web server will remain active to pass health checks."
        )
        while True:
            time.sleep(3600)

    logger.info("Starting Telegram bot polling...")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
