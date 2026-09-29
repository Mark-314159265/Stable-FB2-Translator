# Stable FB2 AI Translator (Desktop GUI & Telegram Bot)

[English](#english) | [Українська](#українська)

---

## Українська

Проєкт для автоматичного та якісного перекладу електронних книг у форматі **FB2** на українську мову за допомогою Google Gemini API.

Оригінальна структура книги, включаючи розділи, параграфи, вірші, заголовки та розмітку, зберігається повністю.

Проєкт підтримує два формати роботи:
1. **Telegram-бот** із вебсервером для деплою на **Render** (з підтримкою паузи, лічильника запитів та налаштуванням затримок).
2. **Десктопний GUI** додаток (`main_ver3-4.py`).

---

### ✨ Можливості Telegram-бота (аналогічно десктопній версії)

1. **⏸ Пауза та ▶️ Відновлення**:
   * Під час перекладу під повідомленням прогресу доступна інлайн-кнопка **⏸ Пауза**.
   * Натискання призупиняє запити до API у реальному часі. Кнопка змінюється на **▶️ Продовжити**.
   * Також є кнопка **🛑 Зупинити** — зупиняє переклад і відправляє частково перекладений файл.

2. **📊 Лічильник запитів та скидання ліміту**:
   * Відстеження кількості запитів за поточну добу (`Запитів сьогодні: X / 500`).
   * Розрахунок часу до скидання щоденної квоти Google API (скидання о 17:00 UTC / 20:00 за Києвом).
   * Інформація відображається у реальному часі під час перекладу та за командою `/status`.

3. **⚙️ Налаштування часових затримок (/settings)**:
   * **⏱ Базова затримка** (`delay_req`): пауза між успішними пакетами (1.0, 2.0, 3.0, 5.0, 10.0 с).
   * **🛡 Пауза захисту** (`delay_protect`): очікування при блокуванні фільтрами безпеки (2.0 - 15.0 с).
   * **⚠️ Пауза помилок** (`delay_error`): затримка при збої сервера або ліміті 429 (5.0 - 20.0 с).
   * **🔤 Ліміт символів** (`char_limit`): розмір пакета тексту (3000 - 10000 символів).
   * **🌡 Температура** (`temperature`): рівень креативності моделі (0.0 - 1.0).
   * **🤖 Модель AI** (`model`): вибір моделі Google Gemini.
   * Налаштування зберігаються індивідуально для кожного користувача.

---

### 🚀 Розгортання Telegram-бота на Render

#### 1. Швидке налаштування Web Service:
* **Environment**: `Python`
* **Build Command**:
  ```bash
  pip install -r requirements.txt
  ```
* **Start Command**:
  ```bash
  python bot.py
  ```

#### 2. Змінні середовища (Environment Variables):
Додайте наступні змінні у панелі керування Render (`Environment`):
* `BOT_TOKEN` — токен Telegram-бота (отримайте у [@BotFather](https://t.me/BotFather)).
* `GEMINI_API_KEY` — API-ключ від Google ([Google AI Studio](https://aistudio.google.com/app/apikey)).
* `PORT` — `10000` (порт для HTTP health check сервера).
* *(Опціонально)* `GEMINI_MODEL` — за замовчуванням `models/gemini-3.1-flash-lite`.

---

### 💻 Локальний запуск Telegram-бота

1. Склонуйте репозиторій:
   ```bash
   git clone https://github.com/Mark-314159265/translator.git
   cd translator
   ```
2. Встановіть залежності:
   ```bash
   pip install -r requirements.txt
   ```
3. Створіть файл `.env` на основі `.env.example`:
   ```env
   BOT_TOKEN=123456789:AAABBBCCCDDDEEEFFF
   GEMINI_API_KEY=AIzaSy...
   PORT=10000
   ```
4. Запустіть бота:
   ```bash
   python bot.py
   ```

---

## English

A tool for high-quality automatic translation of **FB2** format e-books into Ukrainian using Google Gemini API.

Preserves native XML structure, titles, paragraphs, and stanzas. Available as a **Telegram Bot** (with interactive pause/resume, request counters, delay customization, and Render web service support) and as a desktop GUI app.

### Deployment on Render
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `python bot.py`
* **Required Env Vars**: `BOT_TOKEN`, `GEMINI_API_KEY`, `PORT`