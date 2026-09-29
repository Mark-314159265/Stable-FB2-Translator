# Stable FB2 AI Translator

[English](#english) | [Українська](#українська)

## English

program for automatic translation of electronic books in fb2 format. it uses google gemini api for text processing. the original file structure remains fully intact. the application breaks the text into optimal batches and bypasses server restrictions automatically.

### features
* preservation of native xml markup of fb2 files.
* automatic recovery from server errors and api limits.
* saving sessions, file queues, and local settings.
* dynamic batch splitting during complex translations.
* modern gui with drag-and-drop support.

### how to use
1. get a free api key from google ai studio.
2. run the application.
3. enter your key in the api key section.
4. drag and drop your fb2 files into the queue.
5. click the start button.

### settings explanation
* **Character limit**: maximum length of a text batch sent to the server per request.
* **Temperature**: determines the model's creativity level, where a lower value provides a more strict and accurate translation.
* **Base delay (s)**: pause time between successful requests.
* **Protection delay (s)**: waiting time if google's safety filters block the content.
* **JSON error delay (s)**: delay duration when the server returns a broken data format.
* **Mismatch delay (s)**: waiting time if the translated paragraph count differs from the original text.
* **Server error delay (s)**: pause triggered by api quota exhaustion or internal server crashes.

---

## Українська

програма для автоматичного перекладу електронних книг у форматі fb2. вона використовує google gemini api для обробки тексту. оригінальна структура файлу зберігається повністю. додаток розбиває текст на оптимальні пакети та автоматично обходить серверні обмеження.

### функціонал
* збереження оригінальної xml-розмітки файлів fb2.
* автоматичне відновлення після помилок сервера та лімітів api.
* збереження сесій, черги файлів та локальних налаштувань.
* динамічне дроблення пакетів під час складних перекладів.
* сучасний графічний інтерфейс із підтримкою перетягування файлів.

### як користуватися
1. отримайте безкоштовний ключ в google ai studio.
2. запустіть програму.
3. вкажіть ваш ключ у відповідному полі.
4. перетягніть файли fb2 у вікно черги.
5. натисніть кнопку старту.

### пояснення налаштувань
* **Ліміт символів**: максимальна довжина текстового пакета, який відправляється на сервер за один запит.
* **Температура**: визначає рівень креативності моделі, нижче значення забезпечує суворіший і точніший переклад.
* **Базова пауза (с)**: час очікування між успішними запитами.
* **Пауза захисту (с)**: затримка, якщо фільтри безпеки google заблокували контент.
* **Пауза помилки JSON (с)**: час очікування, коли сервер повертає пошкоджений формат даних.
* **Пауза розбіжності (с)**: затримка, якщо кількість перекладених абзаців відрізняється від оригінального тексту.
* **Пауза збою сервера (с)**: пауза при вичерпанні квоти запитів api або внутрішніх падіннях сервера.