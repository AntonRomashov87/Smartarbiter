# 🚀 Налаштування Smart Арбітр Бота — покрокова інструкція

## Крок 1: Збери всі дані

### 1.1 Токен Telegram бота
✅ **У тебе вже є!** Використовуй той самий що для chess-results бота.

Якщо немає — створи нового:
```
@BotFather → /newbot → введи назву
```

### 1.2 Твій Telegram ID (ADMIN_ID)
**Як знайти:**
1. Напиши своєму боту `/start`
2. У відповіді бот напише: "Твій ID: 123456789"
3. Або використай бота @userinfobot

### 1.3 Firebase Service Account

**Де взяти:**
1. Відкрий [Firebase Console](https://console.firebase.google.com)
2. Вибери свій проект (Smart Арбітр)
3. Клацни **шестерінку** ⚙️ → **Project settings**
4. Вкладка **Service accounts**
5. Натисни **Generate new private key**
6. Збережи файл `firebase-service-account.json`

**ВАЖЛИВО:** Цей файл містить секретні ключі — НЕ викладай в Git!

### 1.4 Firebase Database URL

Знаходиться там само в Project Settings:
```
https://твій-проект-default-rtdb.firebaseio.com
```

Або подивись в коді Smart Арбітра (рядок з `databaseURL`)

### 1.5 Tournament ID

**Як знайти:**
- Відкрий турнір у Smart Арбітрі
- URL виглядає: `https://swiss-chess.netlify.app/#tournament_2025_03_18_abc123`
- **ID** = все після `#` → `tournament_2025_03_18_abc123`

Або в Firebase Console:
- Realtime Database → tournaments → **ID турніру**

---

## Крок 2: Вибери де запускати

### Варіант А: Локально (для тестування)

**Переваги:** швидко налаштувати, можна дебажити  
**Мінуси:** комп'ютер має працювати постійно

**Як:**
1. Створи файл `.env` в папці з ботом:
```env
TELEGRAM_TOKEN=твій_токен_від_botfather
ADMIN_ID=твій_telegram_id
FIREBASE_DATABASE_URL=https://твій-проект.firebaseio.com
FIREBASE_SERVICE_ACCOUNT={"type":"service_account",...весь JSON одним рядком...}
TOURNAMENT_ID=tournament_2025_03_18_xxx
```

2. Встанови залежності:
```bash
pip install -r requirements_swiss.txt
```

3. Запусти:
```bash
python swiss_arbiter_bot.py
```

### Варіант Б: Render.com (рекомендовано)

**Переваги:** працює 24/7, безкоштовно  
**Мінуси:** засинає через 15 хв без активності (але самопінг вирішує)

**Як:**
1. Зайди на [Render.com](https://render.com)
2. Dashboard → **New** → **Web Service**
3. Connect GitHub репозиторій або завантаж файли:
   - `swiss_arbiter_bot.py`
   - `requirements_swiss.txt`
4. Налаштування:
   - **Name:** swiss-arbiter-bot
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements_swiss.txt`
   - **Start Command:** `python swiss_arbiter_bot.py`
5. **Environment Variables** → додай всі змінні:

```
TELEGRAM_TOKEN = (вставити токен)
ADMIN_ID = (вставити ID)
FIREBASE_DATABASE_URL = https://...
FIREBASE_SERVICE_ACCOUNT = (вставити весь JSON)
TOURNAMENT_ID = tournament_...
```

**ВАЖЛИВО для FIREBASE_SERVICE_ACCOUNT:**
- Відкрий файл `firebase-service-account.json`
- Скопіюй **весь вміст** одним рядком
- Вставляй БЕЗ переносів рядків!

Приклад:
```json
{"type":"service_account","project_id":"swiss-arbiter","private_key_id":"abc123","private_key":"-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n","client_email":"firebase-adminsdk@swiss-arbiter.iam.gserviceaccount.com"}
```

6. Натисни **Create Web Service**
7. Render почне деплой — зачекай 2-3 хвилини
8. Коли статус "Live" — бот працює! ✅

### Варіант В: Railway (альтернатива)

Те саме що Render, але інтерфейс інший. Також безкоштовно.

---

## Крок 3: Перевірка роботи

### 3.1 Перевір що бот запустився

**Render/Railway:**
- Відкрий **Logs**
- Маєш побачити:
```
✅ Firebase ініціалізовано
🔄 Автоперевірка кожні 60 сек
♟️ Smart Арбітр Bot | Admin: 123456789
```

**Локально:**
- Ті самі повідомлення в терміналі

### 3.2 Протестуй бота

1. Напиши боту `/start`
2. Маєш отримати:
```
👋 Привіт, Антоне Володимировичу!
Твій ID: 123456789
```

3. Натисни **➕ Додати учня**
4. Введи тестове ім'я (наприклад своє)
5. Бот має підтвердити: "✅ [ім'я] додано!"

### 3.3 Встанови турнір

1. Натисни **🔗 Встановити турнір**
2. Введи ID турніру (з Firebase)
3. Бот підтвердить: "✅ Турнір встановлено!"

### 3.4 Перевір жеребкування

1. Створи тестовий тур у Smart Арбітрі
2. Натисни **🔍 Перевірити жеребкування**
3. Бот має показати пари

---

## Крок 4: Підключи учнів

### Для учнів:
1. Дай їм посилання на бота: `t.me/твій_бот_username`
2. Вони пишуть `/start`
3. Бот автоматично розпізнає їх по імені в Telegram
4. Якщо не розпізнав — вони вводять своє ім'я

### Для тебе:
1. **➕ Додай учнів** — додай всіх хто буде грати
2. **📋 Список** — подивись хто вже зареєструвався
3. **⚠️ Незареєстровані** — нагадай їм написати `/start`

---

## Крок 5: Під час турніру

### Автоматично працює:
- Бот перевіряє Firebase кожну хвилину
- Як тільки створюєш новий тур → миттєво надсилає всім
- Тобі нічого робити не треба! 🎉

### Вручну можеш:
- **🔍 Перевірити жеребкування** — подивитись пари
- **⚠️ Незареєстровані** — побачити хто не отримав сповіщення

---

## 🐛 Якщо щось не працює

### Бот не запускається

**Перевір:**
- `TELEGRAM_TOKEN` правильний?
- `FIREBASE_SERVICE_ACCOUNT` це валідний JSON?
- Всі залежності встановлено? (`pip install -r requirements_swiss.txt`)

**Логи покажуть помилку:**
- Render: Dashboard → Logs
- Локально: в терміналі

### Бот не надсилає сповіщення

**Перевір:**
- Учні додані? (📋 Список учнів)
- Учні зареєструвались? (✅ позначка)
- Турнір встановлено? (🔗)
- ID турніру правильний?

**Подивись логи:**
```
🆕 НОВИЙ тур 3! Надсилаю сповіщення...
✅ Надіслано: Іваненко Іван
```

### Firebase помилки

```
❌ Помилка Firebase: ...
```

**Рішення:**
1. Перевір `FIREBASE_DATABASE_URL` (має бути повний URL з https://)
2. Перевір `FIREBASE_SERVICE_ACCOUNT` (валідний JSON? одним рядком?)
3. Перевір що service account має права на читання Database

**Як дати права:**
- Firebase Console → Database → Rules:
```json
{
  "rules": {
    ".read": true,
    ".write": "auth != null"
  }
}
```

---

## 📝 Швидкий чеклист

- [ ] Є токен бота
- [ ] Є ADMIN_ID (свій telegram ID)
- [ ] Є Firebase Service Account JSON
- [ ] Є Database URL
- [ ] Є Tournament ID
- [ ] Бот запущено (Render/Railway/локально)
- [ ] Бот відповідає на /start
- [ ] Додано учнів
- [ ] Встановлено турнір
- [ ] Учні зареєструвались
- [ ] Тестовий тур створено → сповіщення прийшли ✅

---

## 🎯 Готово!

Тепер кожен раз коли ти створюєш новий тур у Smart Арбітрі — всі учні миттєво отримують сповіщення! 🎉

**Питання?** Пиши — допоможу! 🚀
