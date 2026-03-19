"""
Smart Арбітр Bot v2 — Множинні турніри
Підтримка моніторингу кількох турнірів одночасно
"""

import os
import json
import time
import logging
from datetime import datetime
from flask import Flask
from threading import Thread

import telebot
from telebot import types

import firebase_admin
from firebase_admin import credentials, db as firebase_db

# Завантажуємо змінні з .env файлу (для локального запуску)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════════════════
# ЛОГУВАННЯ
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# КОНФІГУРАЦІЯ
# ═══════════════════════════════════════════════════════════════════════════

TOKEN = os.environ.get('TELEGRAM_TOKEN', 'ТВІЙ_ТОКЕН')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
CHECK_INTERVAL = 60
DATA_FILE = 'data.json'

FIREBASE_DATABASE_URL = os.getenv('FIREBASE_DATABASE_URL', 'https://your-project.firebaseio.com')

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# ІНІЦІАЛІЗАЦІЯ FIREBASE
# ═══════════════════════════════════════════════════════════════════════════

def init_firebase():
    try:
        service_account_json = os.getenv('FIREBASE_SERVICE_ACCOUNT')
        if service_account_json:
            service_account = json.loads(service_account_json)
            cred = credentials.Certificate(service_account)
        else:
            cred = credentials.Certificate('firebase-service-account.json')
        
        firebase_admin.initialize_app(cred, {
            'databaseURL': FIREBASE_DATABASE_URL
        })
        log.info("✅ Firebase ініціалізовано")
    except Exception as e:
        log.error(f"❌ Помилка Firebase: {e}")
        raise

# ═══════════════════════════════════════════════════════════════════════════
# ДАНІ (нова структура для множинних турнірів)
# ═══════════════════════════════════════════════════════════════════════════

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "tournaments": {},  # {tournament_id: {name, last_round, students: [names]}}
        "students": {},     # {name: {chat_id, registered}}
        "pending": {}
    }

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

db_local = load_data()

# ═══════════════════════════════════════════════════════════════════════════
# КЛАВІАТУРИ
# ═══════════════════════════════════════════════════════════════════════════

def keyboard_teacher():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📋 Список учнів"),
        types.KeyboardButton("🎯 Турніри"),
        types.KeyboardButton("🔍 Перевірити жеребкування"),
        types.KeyboardButton("➕ Додати учня"),
        types.KeyboardButton("➕ Додати турнір"),
        types.KeyboardButton("⚠️ Незареєстровані"),
        types.KeyboardButton("ℹ️ Допомога"),
    )
    return kb

def keyboard_student():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("♟️ Мої турніри"),
        types.KeyboardButton("♟️ Моє жеребкування"),
        types.KeyboardButton("ℹ️ Допомога"),
    )
    return kb

def keyboard_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("❌ Скасувати"))
    return kb

user_states = {}

# ═══════════════════════════════════════════════════════════════════════════
# FIREBASE (Smart Арбітр)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_tournament_data(tournament_id):
    try:
        ref = firebase_db.reference(f'/tournaments/{tournament_id}')
        data = ref.get()
        if not data:
            log.warning(f"❌ Турнір {tournament_id} не знайдено")
            return None
        return data
    except Exception as e:
        log.error(f"❌ Помилка Firebase: {e}")
        return None

def parse_players(data):
    players_json = data.get('players')
    if not players_json:
        return {}
    
    try:
        players = json.loads(players_json)
        player_map = {}
        
        for player in players:
            player_id = player.get('id')
            name = player.get('name', '')
            
            if player_id and name:
                player_map[player_id] = {
                    'name': name,
                    'elo': player.get('elo', 0)
                }
        
        return player_map
    except Exception as e:
        log.error(f"❌ Помилка парсингу гравців: {e}")
        return {}

def parse_rounds(data):
    rounds_json = data.get('rounds')
    if not rounds_json:
        return []
    
    try:
        return json.loads(rounds_json)
    except Exception as e:
        log.error(f"❌ Помилка парсингу турів: {e}")
        return []

def normalize(text):
    return text.lower().replace(',', '').replace('  ', ' ').strip()

def find_player_pairing(pairs, player_name, player_map):
    name_norm = normalize(player_name)
    
    for pair in pairs:
        if pair.get('isBye'):
            white_id = pair.get('white', {}).get('id')
            if white_id in player_map:
                white_name = player_map[white_id]['name']
                if name_norm in normalize(white_name):
                    return {
                        'is_bye': True,
                        'player_name': white_name
                    }
            continue
        
        white_id = pair.get('white', {}).get('id')
        black_id = pair.get('black', {}).get('id')
        
        white_player = player_map.get(white_id)
        black_player = player_map.get(black_id)
        
        if not white_player or not black_player:
            continue
        
        white_name = white_player['name']
        black_name = black_player['name']
        
        if name_norm in normalize(white_name):
            return {
                'is_bye': False,
                'color': 'white',
                'color_text': '⚪️ БІЛИМИ',
                'player_name': white_name,
                'opponent_name': black_name,
                'opponent_elo': black_player.get('elo', 0),
                'board': pairs.index(pair) + 1
            }
        
        if name_norm in normalize(black_name):
            return {
                'is_bye': False,
                'color': 'black',
                'color_text': '⚫️ ЧОРНИМИ',
                'player_name': black_name,
                'opponent_name': white_name,
                'opponent_elo': white_player.get('elo', 0),
                'board': pairs.index(pair) + 1
            }
    
    return None

def format_pairing_message(pairing, round_num, tournament_name):
    if pairing['is_bye']:
        return (
            f"🔔 <b>{tournament_name}</b>\n"
            f"<b>Тур №{round_num}</b>\n\n"
            f"♟️ <b>{pairing['player_name']}</b>\n\n"
            f"🏖️ У вас <b>БАЙ</b> (+1 очко)\n\n"
            f"Відпочиньте та підготуйтесь до наступного туру!"
        )
    else:
        return (
            f"🔔 <b>{tournament_name}</b>\n"
            f"<b>Тур №{round_num}</b>\n\n"
            f"♟️ <b>{pairing['player_name']}</b>\n"
            f"🪑 Дошка №<b>{pairing['board']}</b>\n\n"
            f"▶️ Граєш <b>{pairing['color_text']}</b>\n"
            f"🥊 Суперник: <b>{pairing['opponent_name']}</b>\n"
            f"📊 Рейтинг: {pairing.get('opponent_elo', '—')}\n\n"
            f"Успіхів! ♟️"
        )

# ═══════════════════════════════════════════════════════════════════════════
# АВТОПЕРЕВІРКА (для всіх турнірів)
# ═══════════════════════════════════════════════════════════════════════════

def auto_check_loop():
    log.info(f"🔄 Автоперевірка кожні {CHECK_INTERVAL} сек")
    
    while True:
        time.sleep(CHECK_INTERVAL)
        
        try:
            tournaments = db_local.get('tournaments', {})
            
            if not tournaments:
                log.debug("Немає активних турнірів для моніторингу")
                continue
            
            for tournament_id, tournament_info in tournaments.items():
                check_tournament(tournament_id, tournament_info)
                    
        except Exception as e:
            log.error(f"❌ Помилка в auto_check_loop: {e}", exc_info=True)

def check_tournament(tournament_id, tournament_info):
    """Перевіряє один турнір на нові тури"""
    try:
        tournament_name = tournament_info.get('name', tournament_id)
        tournament_students = tournament_info.get('students', [])
        last_round = tournament_info.get('last_round', 0)
        
        if not tournament_students:
            return
        
        data = fetch_tournament_data(tournament_id)
        if not data:
            return
        
        player_map = parse_players(data)
        rounds = parse_rounds(data)
        
        if not rounds:
            return
        
        current_round_count = len(rounds)
        
        if current_round_count <= last_round:
            return
        
        log.info(f"🆕 НОВИЙ тур {current_round_count} в турнірі '{tournament_name}'!")
        
        latest_round = rounds[-1]
        pairs = latest_round.get('pairs', [])
        
        not_registered = []
        sent_count = 0
        error_count = 0
        
        all_students = db_local.get('students', {})
        
        for student_name in tournament_students:
            if student_name not in all_students:
                continue
            
            student_info = all_students[student_name]
            chat_id = student_info.get('chat_id')
            
            if not chat_id:
                not_registered.append(student_name)
                continue
            
            pairing = find_player_pairing(pairs, student_name, player_map)
            
            if pairing:
                try:
                    message = format_pairing_message(pairing, current_round_count, tournament_name)
                    bot.send_message(
                        chat_id,
                        message,
                        parse_mode='HTML',
                        reply_markup=keyboard_student()
                    )
                    log.info(f"✅ Надіслано: {student_name} ({tournament_name})")
                    sent_count += 1
                except Exception as e:
                    log.error(f"❌ Помилка для {student_name}: {e}")
                    error_count += 1
            else:
                try:
                    bot.send_message(
                        chat_id,
                        f"⚠️ <b>{tournament_name}</b>\nТур {current_round_count} розпочато, але <b>{student_name}</b> "
                        f"не знайдено в жеребкуванні.\nМожливо, пропуск туру.",
                        parse_mode='HTML',
                        reply_markup=keyboard_student()
                    )
                except:
                    pass
        
        log.info(f"📊 [{tournament_name}] Відправлено: {sent_count}, помилок: {error_count}")
        
        if not_registered and ADMIN_ID:
            names = "\n".join(f"• {n}" for n in not_registered)
            bot.send_message(
                ADMIN_ID,
                f"🆕 <b>{tournament_name}</b>\n<b>Тур {current_round_count} розпочано!</b>\n\n"
                f"Ці учні не отримали сповіщення:\n{names}",
                parse_mode='HTML',
                reply_markup=keyboard_teacher()
            )
        
        db_local['tournaments'][tournament_id]['last_round'] = current_round_count
        save_data(db_local)
        
    except Exception as e:
        log.error(f"❌ Помилка check_tournament для {tournament_id}: {e}", exc_info=True)

def self_ping_loop():
    time.sleep(60)
    app_url = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not app_url:
        return
    
    log.info(f"🏓 Самопінг: {app_url}")
    import requests
    while True:
        try:
            requests.get(app_url, timeout=10)
        except:
            pass
        time.sleep(300)

# ═══════════════════════════════════════════════════════════════════════════
# ХЕЛПЕРИ
# ═══════════════════════════════════════════════════════════════════════════

def is_admin(message):
    return message.from_user.id == ADMIN_ID

def get_keyboard(message):
    return keyboard_teacher() if is_admin(message) else keyboard_student()

def get_student_name(user_id):
    for name, info in db_local.get('students', {}).items():
        if info.get('chat_id') == user_id:
            return name
    return None

# ═══════════════════════════════════════════════════════════════════════════
# КОМАНДИ
# ═══════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or ''
    last_name = message.from_user.last_name or ''
    full_name = f"{last_name} {first_name}".strip()

    if user_id == ADMIN_ID:
        tournaments_count = len(db_local.get('tournaments', {}))
        bot.send_message(
            user_id,
            f"👋 Привіт, Антоне Володимировичу!\n\n"
            f"Твій ID: <code>{user_id}</code>\n"
            f"Активних турнірів: {tournaments_count}\n\n"
            f"Використовуй кнопки нижче 👇",
            parse_mode='HTML',
            reply_markup=keyboard_teacher()
        )
        return

    matched = None
    full_name_norm = normalize(full_name)
    for student_name in db_local.get('students', {}):
        student_norm = normalize(student_name)
        if student_norm in full_name_norm or full_name_norm in student_norm:
            matched = student_name
            break

    if matched:
        db_local['students'][matched]['chat_id'] = user_id
        db_local['students'][matched]['registered'] = True
        db_local.get('pending', {}).pop(str(user_id), None)
        save_data(db_local)
        
        bot.send_message(
            user_id,
            f"✅ Привіт, <b>{matched}</b>!\n\n"
            f"Тепер ти будеш отримувати сповіщення про нові тури автоматично 🎉",
            parse_mode='HTML',
            reply_markup=keyboard_student()
        )
        
        if ADMIN_ID:
            bot.send_message(
                ADMIN_ID,
                f"✅ <b>{matched}</b> зареєструвався.",
                parse_mode='HTML',
                reply_markup=keyboard_teacher()
            )
    else:
        db_local.setdefault('pending', {})[str(user_id)] = 'waiting_name'
        save_data(db_local)
        
        bot.send_message(
            user_id,
            f"👋 Привіт!\n\n"
            f"Твоє ім'я в Telegram: <b>{full_name}</b>\n\n"
            f"Я не знайшов тебе в списку. Напиши своє прізвище та ім'я "
            f"як у турнірних списках\n(наприклад: <i>Іваненко Іван</i>):",
            parse_mode='HTML',
            reply_markup=keyboard_cancel()
        )

@bot.message_handler(commands=['help'])
@bot.message_handler(func=lambda m: m.text == "ℹ️ Допомога")
def cmd_help(message):
    if is_admin(message):
        text = (
            "⚙️ <b>Керування ботом v2:</b>\n\n"
            "📋 <b>Список учнів</b> — хто зареєстрований\n"
            "🎯 <b>Турніри</b> — список активних турнірів\n"
            "➕ <b>Додати учня</b> — додати до списку\n"
            "➕ <b>Додати турнір</b> — почати моніторити новий турнір\n"
            "⚠️ <b>Незареєстровані</b> — хто не писав /start\n\n"
            "🤖 Бот автоматично моніторить ВСІ активні турніри!"
        )
    else:
        text = (
            "♟️ <b>Smart Арбітр Bot</b>\n\n"
            "♟️ <b>Мої турніри</b> — переглянути в яких турнірах граю\n\n"
            "Бот автоматично надішле сповіщення щойно з'явиться новий тур!"
        )
    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=get_keyboard(message))

@bot.message_handler(func=lambda m: m.text == "📋 Список учнів")
def btn_list(message):
    if not is_admin(message):
        return
    
    students = db_local.get('students', {})
    if not students:
        bot.send_message(
            message.chat.id,
            "Список порожній.\nНатисни ➕ Додати учня",
            reply_markup=keyboard_teacher()
        )
        return
    
    lines = []
    for name, info in students.items():
        status = "✅" if info.get('chat_id') else "⏳"
        lines.append(f"{status} {name}")
    
    bot.send_message(
        message.chat.id,
        f"📋 <b>Учні ({len(students)}):</b>\n\n" + "\n".join(lines) +
        "\n\n✅ зареєстрований  ⏳ не писав /start",
        parse_mode='HTML',
        reply_markup=keyboard_teacher()
    )

@bot.message_handler(func=lambda m: m.text == "🎯 Турніри")
def btn_tournaments(message):
    if not is_admin(message):
        return
    
    tournaments = db_local.get('tournaments', {})
    
    if not tournaments:
        bot.send_message(
            message.chat.id,
            "Немає активних турнірів.\nНатисни ➕ Додати турнір",
            reply_markup=keyboard_teacher()
        )
        return
    
    lines = []
    for tid, info in tournaments.items():
        name = info.get('name', tid)
        students_count = len(info.get('students', []))
        last_round = info.get('last_round', 0)
        lines.append(f"🎯 <b>{name}</b>\n   Учнів: {students_count} | Останній тур: {last_round}\n   ID: <code>{tid}</code>")
    
    bot.send_message(
        message.chat.id,
        f"🎯 <b>Активні турніри ({len(tournaments)}):</b>\n\n" + "\n\n".join(lines),
        parse_mode='HTML',
        reply_markup=keyboard_teacher()
    )

@bot.message_handler(func=lambda m: m.text == "🔍 Перевірити жеребкування")
def btn_check_pairings(message):
    if not is_admin(message):
        return
    
    tournaments = db_local.get('tournaments', {})
    
    if not tournaments:
        bot.send_message(
            message.chat.id,
            "Немає активних турнірів.",
            reply_markup=keyboard_teacher()
        )
        return
    
    bot.send_chat_action(message.chat.id, 'typing')
    bot.send_message(message.chat.id, "🔍 Завантажую дані турнірів...", reply_markup=keyboard_teacher())
    
    for tournament_id, tournament_info in tournaments.items():
        tournament_name = tournament_info.get('name', tournament_id)
        tournament_students = tournament_info.get('students', [])
        
        data = fetch_tournament_data(tournament_id)
        if not data:
            bot.send_message(
                message.chat.id,
                f"❌ <b>{tournament_name}</b>\nНе можу завантажити дані",
                parse_mode='HTML'
            )
            continue
        
        player_map = parse_players(data)
        rounds = parse_rounds(data)
        
        if not rounds:
            bot.send_message(
                message.chat.id,
                f"🕐 <b>{tournament_name}</b>\nЖеребкування ще не створено",
                parse_mode='HTML'
            )
            continue
        
        round_num = len(rounds)
        latest_round = rounds[-1]
        pairs = latest_round.get('pairs', [])
        
        bot.send_message(
            message.chat.id,
            f"📅 <b>{tournament_name}</b>\n<b>Тур №{round_num}</b>",
            parse_mode='HTML'
        )
        
        all_students = db_local.get('students', {})
        
        for student_name in tournament_students:
            if student_name not in all_students:
                continue
            
            pairing = find_player_pairing(pairs, student_name, player_map)
            if pairing:
                message_text = format_pairing_message(pairing, round_num, tournament_name)
                bot.send_message(message.chat.id, message_text, parse_mode='HTML')
            else:
                bot.send_message(
                    message.chat.id,
                    f"🤷 <b>{student_name}</b> — не знайдено в турі {round_num}",
                    parse_mode='HTML'
                )

@bot.message_handler(func=lambda m: m.text == "➕ Додати учня")
def btn_add_student(message):
    if not is_admin(message):
        return
    
    user_states[message.from_user.id] = 'waiting_add_student'
    bot.send_message(
        message.chat.id,
        "Напиши прізвище та ім'я учня:\n(наприклад: <i>Іваненко Іван</i>)",
        parse_mode='HTML',
        reply_markup=keyboard_cancel()
    )

@bot.message_handler(func=lambda m: m.text == "➕ Додати турнір")
def btn_add_tournament(message):
    if not is_admin(message):
        return
    
    user_states[message.from_user.id] = 'waiting_tournament_data'
    bot.send_message(
        message.chat.id,
        "📝 Додавання турніру\n\n"
        "Надішли дані у форматі:\n"
        "<code>ID | Назва | Учні</code>\n\n"
        "Приклад:\n"
        "<code>trenuvalxnyy_turni_586285 | Тренувальний | Іваненко Іван, Петренко Петро, Сидоренко Сергій</code>\n\n"
        "Або тільки ID (учні будуть всі зареєстровані):\n"
        "<code>trenuvalxnyy_turni_586285</code>",
        parse_mode='HTML',
        reply_markup=keyboard_cancel()
    )

@bot.message_handler(func=lambda m: m.text == "⚠️ Незареєстровані")
def btn_not_registered(message):
    if not is_admin(message):
        return
    
    students = db_local.get('students', {})
    not_reg = [n for n, i in students.items() if not i.get('chat_id')]
    
    if not not_reg:
        bot.send_message(
            message.chat.id,
            "✅ Всі учні зареєстровані!",
            reply_markup=keyboard_teacher()
        )
    else:
        names = "\n".join(f"• {n}" for n in not_reg)
        bot.send_message(
            message.chat.id,
            f"⏳ <b>Не писали /start ({len(not_reg)}):</b>\n\n{names}\n\n"
            f"Попроси їх написати боту /start!",
            parse_mode='HTML',
            reply_markup=keyboard_teacher()
        )

@bot.message_handler(func=lambda m: m.text == "♟️ Мої турніри")
def btn_my_tournaments(message):
    user_id = message.from_user.id
    
    student_name = get_student_name(user_id)
    if not student_name:
        bot.send_message(
            message.chat.id,
            "❌ Тебе немає в списку. Зверніться до тренера.",
            reply_markup=keyboard_student()
        )
        return
    
    tournaments = db_local.get('tournaments', {})
    my_tournaments = []
    
    for tid, info in tournaments.items():
        if student_name in info.get('students', []):
            my_tournaments.append(f"🎯 {info.get('name', tid)} (Тур {info.get('last_round', 0)})")
    
    if not my_tournaments:
        bot.send_message(
            message.chat.id,
            "У тебе поки немає активних турнірів.",
            reply_markup=keyboard_student()
        )
    else:
        bot.send_message(
            message.chat.id,
            f"♟️ <b>Твої турніри:</b>\n\n" + "\n".join(my_tournaments),
            parse_mode='HTML',
            reply_markup=keyboard_student()
        )

@bot.message_handler(func=lambda m: m.text == "♟️ Моє жеребкування")
def btn_my_pairings(message):
    user_id = message.from_user.id
    
    student_name = get_student_name(user_id)
    if not student_name:
        bot.send_message(
            message.chat.id,
            "❌ Тебе немає в списку. Зверніться до тренера.",
            reply_markup=keyboard_student()
        )
        return
    
    tournaments = db_local.get('tournaments', {})
    found_any = False
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    for tournament_id, tournament_info in tournaments.items():
        if student_name not in tournament_info.get('students', []):
            continue
        
        tournament_name = tournament_info.get('name', tournament_id)
        
        data = fetch_tournament_data(tournament_id)
        if not data:
            continue
        
        player_map = parse_players(data)
        rounds = parse_rounds(data)
        
        if not rounds:
            bot.send_message(
                message.chat.id,
                f"🕐 <b>{tournament_name}</b>\nЖеребкування ще не створено",
                parse_mode='HTML'
            )
            continue
        
        round_num = len(rounds)
        latest_round = rounds[-1]
        pairs = latest_round.get('pairs', [])
        
        pairing = find_player_pairing(pairs, student_name, player_map)
        
        if pairing:
            found_any = True
            message_text = format_pairing_message(pairing, round_num, tournament_name)
            bot.send_message(
                message.chat.id,
                message_text,
                parse_mode='HTML',
                reply_markup=keyboard_student()
            )
        else:
            bot.send_message(
                message.chat.id,
                f"🤷 <b>{tournament_name}</b>\nТебе не знайдено в турі {round_num}.\nМожливо, пропуск туру.",
                parse_mode='HTML',
                reply_markup=keyboard_student()
            )
    
    if not found_any:
        bot.send_message(
            message.chat.id,
            "У тебе поки немає активних турнірів з жеребкуванням.",
            reply_markup=keyboard_student()
        )

@bot.message_handler(func=lambda m: m.text == "❌ Скасувати")
def btn_cancel(message):
    user_states.pop(message.from_user.id, None)
    bot.send_message(message.chat.id, "Скасовано.", reply_markup=get_keyboard(message))

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Адмін додає турнір
    if user_states.get(user_id) == 'waiting_tournament_data':
        parts = [p.strip() for p in text.split('|')]
        
        if len(parts) == 1:
            # Тільки ID — всі учні
            tournament_id = parts[0]
            tournament_name = tournament_id
            all_students = list(db_local.get('students', {}).keys())
            tournament_students = all_students
        elif len(parts) >= 3:
            # ID | Назва | Учні
            tournament_id = parts[0]
            tournament_name = parts[1]
            tournament_students = [s.strip() for s in parts[2].split(',')]
        else:
            bot.send_message(
                message.chat.id,
                "❌ Невірний формат. Спробуй ще раз.",
                reply_markup=keyboard_cancel()
            )
            return
        
        db_local.setdefault('tournaments', {})[tournament_id] = {
            'name': tournament_name,
            'last_round': 0,
            'students': tournament_students
        }
        save_data(db_local)
        user_states.pop(user_id)
        
        bot.send_message(
            message.chat.id,
            f"✅ <b>Турнір додано!</b>\n\n"
            f"Назва: {tournament_name}\n"
            f"ID: <code>{tournament_id}</code>\n"
            f"Учнів: {len(tournament_students)}\n\n"
            f"Бот почав моніторинг! 🤖",
            parse_mode='HTML',
            reply_markup=keyboard_teacher()
        )
        return

    # Адмін додає учня
    if user_states.get(user_id) == 'waiting_add_student':
        db_local.setdefault('students', {})[text] = {
            "chat_id": None,
            "registered": False
        }
        save_data(db_local)
        user_states.pop(user_id)
        
        bot.send_message(
            message.chat.id,
            f"✅ <b>{text}</b> додано!\n\nПопроси учня написати /start боту.",
            parse_mode='HTML',
            reply_markup=keyboard_teacher()
        )
        return

    # Учень вводить ім'я
    if db_local.get('pending', {}).get(str(user_id)) == 'waiting_name':
        text_norm = normalize(text)
        
        for student_name in db_local.get('students', {}):
            student_norm = normalize(student_name)
            if text_norm in student_norm or student_norm in text_norm:
                db_local['students'][student_name]['chat_id'] = user_id
                db_local['students'][student_name]['registered'] = True
                del db_local['pending'][str(user_id)]
                save_data(db_local)
                
                bot.send_message(
                    user_id,
                    f"✅ Знайшов: <b>{student_name}</b>!\n\nТепер будеш отримувати сповіщення 🎉",
                    parse_mode='HTML',
                    reply_markup=keyboard_student()
                )
                
                if ADMIN_ID:
                    bot.send_message(
                        ADMIN_ID,
                        f"✅ <b>{student_name}</b> зареєструвався.",
                        parse_mode='HTML',
                        reply_markup=keyboard_teacher()
                    )
                return
        
        bot.send_message(
            user_id,
            f"❌ Не знайшов <b>{text}</b>.\nНапиши точно як у турнірних списках.",
            parse_mode='HTML',
            reply_markup=keyboard_cancel()
        )

# ═══════════════════════════════════════════════════════════════════════════
# FLASK + ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/')
def home():
    s = db_local.get('students', {})
    reg = sum(1 for i in s.values() if i.get('chat_id'))
    t = db_local.get('tournaments', {})
    return f"♟️ Smart Арбітр Bot v2 | Учнів: {len(s)} | Зареєстровано: {reg} | Турнірів: {len(t)}"

def run_web():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"♟️ Smart Арбітр Bot v2 | Admin: {ADMIN_ID} | Інтервал: {CHECK_INTERVAL}s")
    
    init_firebase()
    
    Thread(target=run_web, daemon=True).start()
    Thread(target=auto_check_loop, daemon=True).start()
    Thread(target=self_ping_loop, daemon=True).start()
    
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
