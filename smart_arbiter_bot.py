"""
Smart Арбітр Bot v3 — список турнірів + реєстрація через бота

НОВЕ в v3:
  /tournaments — список публічних активних турнірів з Firebase
  /register    — реєстрація на турнір через бота
  Сповіщення адміну про нову заявку
"""

import os
import json
import time
import uuid
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from threading import Thread
import telebot
from telebot import types
import firebase_admin
from firebase_admin import credentials, db as firebase_db

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
TOKEN                = os.environ.get('TELEGRAM_TOKEN', 'ТВІЙ_ТОКЕН')
ADMIN_ID             = int(os.environ.get('ADMIN_ID', '0'))
BOT_SECRET           = os.environ.get('BOT_SECRET', 'my_secret_key')
CHECK_INTERVAL       = 60
DATA_FILE            = 'data.json'
FIREBASE_DATABASE_URL = os.getenv('FIREBASE_DATABASE_URL', 'https://your-project.firebaseio.com')

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

# ═══════════════════════════════════════════════════════════════════════════
# FIREBASE
# ═══════════════════════════════════════════════════════════════════════════
def init_firebase():
    try:
        service_account_json = os.getenv('FIREBASE_SERVICE_ACCOUNT')
        if service_account_json:
            cred = credentials.Certificate(json.loads(service_account_json))
        else:
            cred = credentials.Certificate('firebase-service-account.json')
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DATABASE_URL})
        log.info("✅ Firebase ініціалізовано")
    except Exception as e:
        log.error(f"❌ Помилка Firebase: {e}")
        raise

# ═══════════════════════════════════════════════════════════════════════════
# ЛОКАЛЬНІ ДАНІ
# ═══════════════════════════════════════════════════════════════════════════
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"tournaments": {}, "students": {}, "tg_users": {}, "pending": {}}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

db_local = load_data()
if 'tg_users' not in db_local:
    db_local['tg_users'] = {}

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
        types.KeyboardButton("🏆 Активні турніри"),
        types.KeyboardButton("📝 Зареєструватись"),
        types.KeyboardButton("♟️ Мої турніри"),
        types.KeyboardButton("♟️ Моє жеребкування"),
        types.KeyboardButton("ℹ️ Допомога"),
    )
    return kb

def keyboard_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("❌ Скасувати"))
    return kb

user_states = {}  # { user_id: state_string_or_dict }

# ═══════════════════════════════════════════════════════════════════════════
# FIREBASE HELPERS (існуючі)
# ═══════════════════════════════════════════════════════════════════════════
def fetch_tournament_data(tournament_id):
    try:
        ref  = firebase_db.reference(f'/tournaments/{tournament_id}')
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
        return {p['id']: {'name': p.get('name',''), 'elo': p.get('elo',0)}
                for p in players if p.get('id') and p.get('name')}
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
                if name_norm in normalize(player_map[white_id]['name']):
                    return {'is_bye': True, 'player_name': player_map[white_id]['name']}
            continue
        white_id = pair.get('white', {}).get('id')
        black_id = pair.get('black', {}).get('id')
        white_p  = player_map.get(white_id)
        black_p  = player_map.get(black_id)
        if not white_p or not black_p:
            continue
        if name_norm in normalize(white_p['name']):
            return {'is_bye': False, 'color': 'white', 'color_text': '⚪️ БІЛИМИ',
                    'player_name': white_p['name'], 'opponent_name': black_p['name'],
                    'opponent_elo': black_p.get('elo', 0), 'board': pairs.index(pair) + 1}
        if name_norm in normalize(black_p['name']):
            return {'is_bye': False, 'color': 'black', 'color_text': '⚫️ ЧОРНИМИ',
                    'player_name': black_p['name'], 'opponent_name': white_p['name'],
                    'opponent_elo': white_p.get('elo', 0), 'board': pairs.index(pair) + 1}
    return None

def format_pairing_message(pairing, round_num, tournament_name):
    if pairing['is_bye']:
        return (f"🔔 <b>{tournament_name}</b>\n<b>Тур №{round_num}</b>\n\n"
                f"♟️ <b>{pairing['player_name']}</b>\n\n"
                f"🏖️ У вас <b>БАЙ</b> (+1 очко)\n\nВідпочиньте та підготуйтесь до наступного туру!")
    return (f"🔔 <b>{tournament_name}</b>\n<b>Тур №{round_num}</b>\n\n"
            f"♟️ <b>{pairing['player_name']}</b>\n"
            f"🪑 Дошка №<b>{pairing['board']}</b>\n\n"
            f"▶️ Граєш <b>{pairing['color_text']}</b>\n"
            f"🥊 Суперник: <b>{pairing['opponent_name']}</b>\n"
            f"📊 Рейтинг: {pairing.get('opponent_elo', '—')}\n\nУспіхів! ♟️")

def resolve_chat_id(telegram_username: str) -> int | None:
    if not telegram_username:
        return None
    username_clean = telegram_username.lstrip('@').lower()
    chat_id = db_local.get('tg_users', {}).get(username_clean)
    if chat_id:
        return chat_id
    for name, info in db_local.get('students', {}).items():
        stored = (info.get('tg_username') or '').lstrip('@').lower()
        if stored == username_clean and info.get('chat_id'):
            return info['chat_id']
    return None

# ═══════════════════════════════════════════════════════════════════════════
# НОВЕ: СПИСОК ПУБЛІЧНИХ ТУРНІРІВ З FIREBASE
# ═══════════════════════════════════════════════════════════════════════════
def fetch_public_tournaments():
    """
    Повертає список активних публічних турнірів з Firebase.
    Формат: [{ 'id', 'name', 'city', 'date', 'reg_open', 'players_count', 'rounds' }]
    """
    try:
        ref  = firebase_db.reference('/tournaments')
        all_t = ref.get()
        if not all_t:
            return []
        result = []
        for tid, tdata in all_t.items():
            if not isinstance(tdata, dict):
                continue
            meta = tdata.get('_meta', {}) or {}
            # Показуємо тільки публічні і активні (не завершені)
            if meta.get('visibility', 'public') != 'public':
                continue
            if meta.get('status') == 'finished':
                continue
            name       = meta.get('name') or tdata.get('meta', {}).get('tName', tid)
            city       = meta.get('city', '')
            date       = meta.get('date', '')
            reg_open   = tdata.get('regOpen', False)
            # Кількість гравців
            try:
                players = json.loads(tdata.get('players', '[]'))
                players_count = len(players)
            except Exception:
                players_count = 0
            # Кількість турів
            try:
                rounds = json.loads(tdata.get('rounds', '[]'))
                rounds_count = len(rounds)
                total_rounds = tdata.get('meta', {}).get('totalRounds', 0)
            except Exception:
                rounds_count = 0
                total_rounds = 0
            result.append({
                'id':            tid,
                'name':          name,
                'city':          city,
                'date':          date,
                'reg_open':      reg_open,
                'players_count': players_count,
                'rounds_done':   rounds_count,
                'total_rounds':  total_rounds,
            })
        # Сортуємо: спочатку з відкритою реєстрацією
        result.sort(key=lambda x: (not x['reg_open'], x.get('name', '')))
        return result
    except Exception as e:
        log.error(f"❌ fetch_public_tournaments: {e}")
        return []

def format_tournament_info(t):
    """Форматує один турнір для виводу в боті."""
    reg_badge = "🟢 Реєстрація відкрита" if t['reg_open'] else "🔴 Реєстрація закрита"
    city_line = f"📍 {t['city']}" if t['city'] else ""
    date_line = f"📅 {t['date']}" if t['date'] else ""
    progress  = f"🎯 Тур {t['rounds_done']}/{t['total_rounds']}" if t['total_rounds'] else ""
    players_line = f"👥 {t['players_count']} учасників"
    lines = [f"🏆 <b>{t['name']}</b>", reg_badge]
    if city_line: lines.append(city_line)
    if date_line: lines.append(date_line)
    lines.append(players_line)
    if progress: lines.append(progress)
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════════════════
# НОВЕ: РЕЄСТРАЦІЯ НА ТУРНІР ЧЕРЕЗ БОТА → Firebase registrations
# ═══════════════════════════════════════════════════════════════════════════
def submit_registration_to_firebase(tournament_id, reg_data):
    """
    Записує заявку в Firebase: tournaments/{tid}/registrations/{reg_id}
    reg_data: { name, rank, club, birth, telegram_id, telegram_username, ts, status }
    """
    try:
        reg_id = f"tg_{uuid.uuid4().hex[:12]}"
        ref    = firebase_db.reference(f'/tournaments/{tournament_id}/registrations/{reg_id}')
        ref.set(reg_data)
        log.info(f"✅ Заявку {reg_id} записано у Firebase для турніру {tournament_id}")
        return reg_id
    except Exception as e:
        log.error(f"❌ submit_registration_to_firebase: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════
# FLASK ENDPOINTS (існуючі + нові)
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/')
def home():
    s = db_local.get('students', {})
    reg = sum(1 for i in s.values() if i.get('chat_id'))
    t   = db_local.get('tournaments', {})
    return (f"♟️ Smart Арбітр Bot v3 | Учнів: {len(s)} | Зареєстровано: {reg} | "
            f"Турнірів: {len(t)}")

@app.route('/ping', methods=['GET', 'OPTIONS'])
def ping():
    if request.method == 'OPTIONS':
        return '', 204
    return jsonify({'status': 'ok', 'bot': 'Smart Арбітр Bot v3',
                    'students': len(db_local.get('students', {}))})

@app.route('/send-round', methods=['POST', 'OPTIONS'])
def send_round():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400

    if data.get('secret') != BOT_SECRET:
        log.warning(f"❌ /send-round: невірний secret від {request.remote_addr}")
        return jsonify({'error': 'Unauthorized'}), 401

    tournament_name = data.get('tournament_name', 'Турнір')
    round_num       = data.get('round_num', '?')
    pairs           = data.get('pairs', [])
    if not pairs:
        return jsonify({'error': 'No pairs provided'}), 400

    log.info(f"📨 /send-round: {tournament_name} тур {round_num}, пар: {len(pairs)}")
    sent = skipped = errors = 0
    no_tg = []

    for pair in pairs:
        if pair.get('isBye'):
            player_info  = pair.get('player', {})
            tg_username  = player_info.get('telegram', '')
            player_name  = player_info.get('name', '—')
            chat_id      = resolve_chat_id(tg_username)
            if not chat_id:
                skipped += 1
                if player_name: no_tg.append(player_name)
                continue
            msg = format_pairing_from_data(player_name, None, None, None, None, None,
                                           round_num, tournament_name, True)
            try:
                bot.send_message(chat_id, msg, parse_mode='HTML', reply_markup=keyboard_student())
                sent += 1
            except Exception as e:
                log.error(f"❌ BYE send error {player_name}: {e}")
                errors += 1
        else:
            board = pair.get('board', '?')
            white = pair.get('white', {})
            black = pair.get('black', {})
            for player_info, opponent_info, color, color_text in [
                (white, black, 'white', '⚪️ БІЛИМИ'),
                (black, white, 'black', '⚫️ ЧОРНИМИ'),
            ]:
                tg_username = player_info.get('telegram', '')
                player_name = player_info.get('name', '—')
                chat_id     = resolve_chat_id(tg_username)
                if not chat_id:
                    skipped += 1
                    if player_name: no_tg.append(player_name)
                    continue
                msg = format_pairing_from_data(
                    player_name, color, color_text,
                    opponent_info.get('name', '—'), opponent_info.get('elo', 0),
                    board, round_num, tournament_name, False
                )
                try:
                    bot.send_message(chat_id, msg, parse_mode='HTML', reply_markup=keyboard_student())
                    sent += 1
                except Exception as e:
                    log.error(f"❌ Send error {player_name}: {e}")
                    errors += 1

    if no_tg and ADMIN_ID:
        names = "\n".join(f"• {n}" for n in no_tg[:20])
        try:
            bot.send_message(ADMIN_ID,
                f"📨 <b>{tournament_name}</b> — Тур {round_num}\n\n"
                f"✅ Надіслано: {sent}\n"
                f"⚠️ Без Telegram:\n{names}", parse_mode='HTML')
        except Exception:
            pass

    return jsonify({'status': 'ok', 'sent': sent, 'skipped': skipped, 'errors': errors})

def format_pairing_from_data(player_name, color, color_text, opponent_name,
                              opponent_elo, board, round_num, tournament_name, is_bye):
    if is_bye:
        return (f"🔔 <b>{tournament_name}</b>\n<b>Тур №{round_num}</b>\n\n"
                f"♟️ <b>{player_name}</b>\n\n"
                f"🏖️ У вас <b>БАЙ</b> (+1 очко)\n\nВідпочиньте та підготуйтесь до наступного туру!")
    return (f"🔔 <b>{tournament_name}</b>\n<b>Тур №{round_num}</b>\n\n"
            f"♟️ <b>{player_name}</b>\n"
            f"🪑 Дошка №<b>{board}</b>\n\n"
            f"▶️ Граєш <b>{color_text}</b>\n"
            f"🥊 Суперник: <b>{opponent_name}</b>\n"
            f"📊 Рейтинг: {opponent_elo or '—'}\n\nУспіхів! ♟️")

# ═══════════════════════════════════════════════════════════════════════════
# АВТОПЕРЕВІРКА (без змін)
# ═══════════════════════════════════════════════════════════════════════════
def auto_check_loop():
    log.info(f"🔄 Автоперевірка кожні {CHECK_INTERVAL} сек")
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            for tournament_id, tournament_info in db_local.get('tournaments', {}).items():
                check_tournament(tournament_id, tournament_info)
        except Exception as e:
            log.error(f"❌ auto_check_loop: {e}", exc_info=True)

def check_tournament(tournament_id, tournament_info):
    try:
        tournament_name     = tournament_info.get('name', tournament_id)
        tournament_students = tournament_info.get('students', [])
        last_round          = tournament_info.get('last_round', 0)
        if not tournament_students:
            return
        data = fetch_tournament_data(tournament_id)
        if not data:
            return
        player_map    = parse_players(data)
        rounds_list   = parse_rounds(data)
        if not rounds_list:
            return
        current_round_count = len(rounds_list)
        if current_round_count <= last_round:
            return
        log.info(f"🆕 НОВИЙ тур {current_round_count} в '{tournament_name}'!")
        pairs        = rounds_list[-1].get('pairs', [])
        not_reg      = []
        sent_count   = 0
        all_students = db_local.get('students', {})
        for student_name in tournament_students:
            if student_name not in all_students:
                continue
            chat_id = all_students[student_name].get('chat_id')
            if not chat_id:
                not_reg.append(student_name)
                continue
            pairing = find_player_pairing(pairs, student_name, player_map)
            if pairing:
                try:
                    bot.send_message(chat_id, format_pairing_message(pairing, current_round_count, tournament_name),
                                     parse_mode='HTML', reply_markup=keyboard_student())
                    sent_count += 1
                except Exception as e:
                    log.error(f"❌ {student_name}: {e}")
        if not_reg and ADMIN_ID:
            names = "\n".join(f"• {n}" for n in not_reg)
            bot.send_message(ADMIN_ID,
                f"🆕 <b>{tournament_name}</b>\n<b>Тур {current_round_count} розпочато!</b>\n\n"
                f"Не отримали сповіщення:\n{names}", parse_mode='HTML')
        db_local['tournaments'][tournament_id]['last_round'] = current_round_count
        save_data(db_local)
    except Exception as e:
        log.error(f"❌ check_tournament: {e}", exc_info=True)

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
# КОМАНДИ БОТА
# ═══════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id   = message.from_user.id
    username  = (message.from_user.username or '').lower()
    first_name = message.from_user.first_name or ''
    last_name  = message.from_user.last_name or ''
    full_name  = f"{last_name} {first_name}".strip()

    if username:
        db_local.setdefault('tg_users', {})[username] = user_id
        save_data(db_local)

    if user_id == ADMIN_ID:
        bot.send_message(user_id,
            f"👋 Привіт, Антоне Володимировичу!\n\nВикористовуй кнопки нижче 👇",
            parse_mode='HTML', reply_markup=keyboard_teacher())
        return

    # Пошук по username
    if username:
        for student_name, info in db_local.get('students', {}).items():
            if (info.get('tg_username') or '').lstrip('@').lower() == username:
                db_local['students'][student_name]['chat_id']    = user_id
                db_local['students'][student_name]['registered'] = True
                save_data(db_local)
                bot.send_message(user_id,
                    f"✅ Привіт, <b>{student_name}</b>!\n\nТепер будеш отримувати сповіщення 🎉",
                    parse_mode='HTML', reply_markup=keyboard_student())
                if ADMIN_ID:
                    bot.send_message(ADMIN_ID, f"✅ <b>{student_name}</b> зареєструвався (@{username}).", parse_mode='HTML')
                return

    # Пошук по ПІБ
    full_name_norm = normalize(full_name)
    for student_name in db_local.get('students', {}):
        if normalize(student_name) in full_name_norm or full_name_norm in normalize(student_name):
            db_local['students'][student_name]['chat_id']    = user_id
            db_local['students'][student_name]['registered'] = True
            save_data(db_local)
            bot.send_message(user_id,
                f"✅ Привіт, <b>{student_name}</b>!\n\nТепер будеш отримувати сповіщення 🎉",
                parse_mode='HTML', reply_markup=keyboard_student())
            if ADMIN_ID:
                bot.send_message(ADMIN_ID, f"✅ <b>{student_name}</b> зареєструвався.", parse_mode='HTML')
            return

    # Не знайдено — вітання і показуємо меню (можна реєструватись на турніри)
    bot.send_message(user_id,
        f"👋 Привіт!\n\n♟️ <b>Smart Арбітр Bot</b>\n\n"
        f"Переглядай активні турніри та реєструйся на них прямо тут!\n\n"
        f"Використовуй кнопки нижче 👇",
        parse_mode='HTML', reply_markup=keyboard_student())

@bot.message_handler(commands=['tournaments'])
@bot.message_handler(func=lambda m: m.text == "🏆 Активні турніри")
def cmd_tournaments(message):
    """Список публічних активних турнірів з Firebase."""
    bot.send_chat_action(message.chat.id, 'typing')
    tournaments = fetch_public_tournaments()
    if not tournaments:
        bot.send_message(message.chat.id,
            "😔 Наразі активних публічних турнірів немає.",
            reply_markup=get_keyboard(message))
        return

    text = f"🏆 <b>Активні турніри ({len(tournaments)}):</b>\n\n"
    text += "\n\n".join(format_tournament_info(t) for t in tournaments)
    text += "\n\n📝 Натисни <b>Зареєструватись</b> щоб подати заявку на турнір."
    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=get_keyboard(message))

@bot.message_handler(commands=['register'])
@bot.message_handler(func=lambda m: m.text == "📝 Зареєструватись")
def cmd_register(message):
    """Крок 1: показуємо список турнірів з відкритою реєстрацією."""
    bot.send_chat_action(message.chat.id, 'typing')
    tournaments = fetch_public_tournaments()
    open_t = [t for t in tournaments if t['reg_open']]

    if not open_t:
        bot.send_message(message.chat.id,
            "😔 Наразі реєстрація не відкрита ні в одному турнірі.\n\n"
            "Перевір пізніше або звернись до організатора.",
            reply_markup=get_keyboard(message))
        return

    # Показуємо кнопки вибору турніру
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in open_t:
        label = f"🏆 {t['name']}"
        if t['city']:
            label += f" · {t['city']}"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"reg_select:{t['id']}"))

    bot.send_message(message.chat.id,
        "📝 <b>Реєстрація на турнір</b>\n\nОбери турнір:",
        parse_mode='HTML', reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith('reg_select:'))
def cb_reg_select(call):
    """Крок 2: турнір обраний — просимо ввести ПІБ."""
    tournament_id = call.data.split(':', 1)[1]
    user_id       = call.from_user.id

    # Зберігаємо стан реєстрації
    user_states[user_id] = {
        'step':          'waiting_name',
        'tournament_id': tournament_id,
    }

    # Отримаємо назву турніру
    try:
        meta = firebase_db.reference(f'/tournaments/{tournament_id}/_meta').get() or {}
        t_name = meta.get('name', tournament_id)
    except Exception:
        t_name = tournament_id

    bot.answer_callback_query(call.id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    bot.send_message(call.message.chat.id,
        f"✅ Турнір: <b>{t_name}</b>\n\n"
        f"Крок 1/3 — Введи своє <b>Прізвище та Ім'я</b>\n"
        f"(наприклад: <i>Іваненко Іван</i>):",
        parse_mode='HTML', reply_markup=keyboard_cancel())

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    user_id = message.from_user.id
    text    = message.text.strip()
    state   = user_states.get(user_id)

    # ── Скасування ──────────────────────────────────────────────────────
    if text == "❌ Скасувати":
        user_states.pop(user_id, None)
        bot.send_message(message.chat.id, "Скасовано.", reply_markup=get_keyboard(message))
        return

    # ── Реєстрація: крок waiting_name ───────────────────────────────────
    if isinstance(state, dict) and state.get('step') == 'waiting_name':
        user_states[user_id] = {**state, 'step': 'waiting_rank', 'name': text}
        bot.send_message(message.chat.id,
            f"Крок 2/3 — Введи свій <b>розряд</b> (або натисни /skip щоб пропустити):\n"
            f"Приклад: <i>І розряд</i>, <i>КМС</i>, <i>б/р</i>",
            parse_mode='HTML', reply_markup=keyboard_cancel())
        return

    # ── Реєстрація: крок waiting_rank ───────────────────────────────────
    if isinstance(state, dict) and state.get('step') == 'waiting_rank':
        rank = '' if text.startswith('/') else text
        user_states[user_id] = {**state, 'step': 'waiting_club', 'rank': rank}
        bot.send_message(message.chat.id,
            f"Крок 3/3 — Введи свій <b>клуб / школу</b> (або /skip щоб пропустити):",
            parse_mode='HTML', reply_markup=keyboard_cancel())
        return

    # ── Реєстрація: крок waiting_club → підтвердження ───────────────────
    if isinstance(state, dict) and state.get('step') == 'waiting_club':
        club          = '' if text.startswith('/') else text
        tournament_id = state['tournament_id']
        player_name   = state['name']
        rank          = state.get('rank', '')
        username      = (message.from_user.username or '')
        tg_id         = str(user_id)

        reg_data = {
            'name':              player_name,
            'rank':              rank,
            'club':              club,
            'telegram_id':       tg_id,
            'telegram_username': username,
            'ts':                int(time.time() * 1000),
            'status':            'pending',
            'source':            'telegram_bot',
        }

        reg_id = submit_registration_to_firebase(tournament_id, reg_data)
        user_states.pop(user_id, None)

        if reg_id:
            # Підтвердження гравцю
            bot.send_message(message.chat.id,
                f"✅ <b>Заявку подано!</b>\n\n"
                f"👤 {player_name}\n"
                f"🎖️ {rank or '—'}\n"
                f"🏛 {club or '—'}\n\n"
                f"Чекай підтвердження від арбітра. "
                f"Ти отримаєш сповіщення коли заявку схвалять.",
                parse_mode='HTML', reply_markup=keyboard_student())

            # Сповіщення адміну
            if ADMIN_ID:
                try:
                    tg_link = f"@{username}" if username else f"ID:{tg_id}"
                    meta    = firebase_db.reference(f'/tournaments/{tournament_id}/_meta').get() or {}
                    t_name  = meta.get('name', tournament_id)
                    bot.send_message(ADMIN_ID,
                        f"📥 <b>Нова заявка на турнір!</b>\n\n"
                        f"🏆 {t_name}\n"
                        f"👤 <b>{player_name}</b>\n"
                        f"🎖️ {rank or '—'}\n"
                        f"🏛 {club or '—'}\n"
                        f"📱 {tg_link}\n\n"
                        f"Підтвердь або відхили заявку в <a href='https://swiss-chess.netlify.app/#tid={tournament_id}'>Smart Арбітрі</a> "
                        f"(вкладка 📋 Заявки)",
                        parse_mode='HTML', disable_web_page_preview=True,
                        reply_markup=keyboard_teacher())
                except Exception as e:
                    log.error(f"❌ Сповіщення адміну: {e}")
        else:
            bot.send_message(message.chat.id,
                "❌ Помилка при поданні заявки. Спробуй пізніше або зв'яжись з організатором.",
                reply_markup=keyboard_student())
        return

    # ── Адмін: додати турнір ────────────────────────────────────────────
    if isinstance(state, str) and state == 'waiting_tournament_data':
        parts = [p.strip() for p in text.split('|')]
        if len(parts) == 1:
            tournament_id      = parts[0]
            tournament_name    = tournament_id
            tournament_students = list(db_local.get('students', {}).keys())
        elif len(parts) >= 3:
            tournament_id       = parts[0]
            tournament_name     = parts[1]
            tournament_students = [s.strip() for s in parts[2].split(',')]
        else:
            bot.send_message(message.chat.id, "❌ Невірний формат.", reply_markup=keyboard_cancel())
            return
        db_local.setdefault('tournaments', {})[tournament_id] = {
            'name': tournament_name, 'last_round': 0, 'students': tournament_students
        }
        save_data(db_local)
        user_states.pop(user_id)
        bot.send_message(message.chat.id,
            f"✅ <b>Турнір додано!</b>\n\nНазва: {tournament_name}\n"
            f"ID: <code>{tournament_id}</code>\nУчнів: {len(tournament_students)}",
            parse_mode='HTML', reply_markup=keyboard_teacher())
        return

    # ── Адмін: додати учня ──────────────────────────────────────────────
    if isinstance(state, str) and state == 'waiting_add_student':
        parts     = text.split()
        tg_part   = ''
        name_parts = []
        for p in parts:
            if p.startswith('@'):
                tg_part = p.lstrip('@').lower()
            else:
                name_parts.append(p)
        name = ' '.join(name_parts).strip() or text
        db_local.setdefault('students', {})[name] = {
            'chat_id': None, 'registered': False, 'tg_username': tg_part
        }
        save_data(db_local)
        user_states.pop(user_id)
        bot.send_message(message.chat.id,
            f"✅ <b>{name}</b> додано!\n{'Telegram: @'+tg_part if tg_part else ''}\n\nПопроси написати /start боту.",
            parse_mode='HTML', reply_markup=keyboard_teacher())
        return

    # ── Реєстрація по ПІБ (pending) ─────────────────────────────────────
    if db_local.get('pending', {}).get(str(user_id)) == 'waiting_name':
        text_norm = normalize(text)
        for student_name in db_local.get('students', {}):
            if text_norm in normalize(student_name) or normalize(student_name) in text_norm:
                db_local['students'][student_name]['chat_id']    = user_id
                db_local['students'][student_name]['registered'] = True
                del db_local['pending'][str(user_id)]
                save_data(db_local)
                bot.send_message(user_id,
                    f"✅ Знайшов: <b>{student_name}</b>!\n\nТепер будеш отримувати сповіщення 🎉",
                    parse_mode='HTML', reply_markup=keyboard_student())
                if ADMIN_ID:
                    bot.send_message(ADMIN_ID, f"✅ <b>{student_name}</b> зареєструвався.", parse_mode='HTML')
                return
        bot.send_message(user_id,
            f"❌ Не знайшов <b>{text}</b>.\nНапиши точно як у турнірних списках.",
            parse_mode='HTML', reply_markup=keyboard_cancel())
        return

    # ── Кнопки ──────────────────────────────────────────────────────────
    if text == "📋 Список учнів" and is_admin(message):
        students  = db_local.get('students', {})
        tg_users  = db_local.get('tg_users', {})
        lines     = []
        for name, info in students.items():
            tg        = info.get('tg_username', '')
            tg_known  = tg.lstrip('@').lower() in tg_users if tg else False
            status    = "✅" if info.get('chat_id') else ("📱" if tg_known else "⏳")
            tg_label  = f" (@{tg.lstrip('@')})" if tg else ''
            lines.append(f"{status} {name}{tg_label}")
        bot.send_message(message.chat.id,
            f"📋 <b>Учні ({len(students)}):</b>\n\n" + "\n".join(lines) +
            "\n\n✅ зареєстр. 📱 є у TG ⏳ не писав /start",
            parse_mode='HTML', reply_markup=keyboard_teacher())

    elif text == "🎯 Турніри" and is_admin(message):
        tournaments = db_local.get('tournaments', {})
        if not tournaments:
            bot.send_message(message.chat.id, "Немає турнірів.", reply_markup=keyboard_teacher())
        else:
            lines = [f"🎯 <b>{i.get('name',tid)}</b>\n Учнів: {len(i.get('students',[]))} | Тур: {i.get('last_round',0)}\n ID: <code>{tid}</code>"
                     for tid, i in tournaments.items()]
            bot.send_message(message.chat.id,
                f"🎯 <b>Моніторинг турнірів ({len(tournaments)}):</b>\n\n" + "\n\n".join(lines),
                parse_mode='HTML', reply_markup=keyboard_teacher())

    elif text == "🔍 Перевірити жеребкування" and is_admin(message):
        bot.send_chat_action(message.chat.id, 'typing')
        for tournament_id, tournament_info in db_local.get('tournaments', {}).items():
            tournament_name = tournament_info.get('name', tournament_id)
            data = fetch_tournament_data(tournament_id)
            if not data:
                bot.send_message(message.chat.id, f"❌ {tournament_name}: не завантажено", parse_mode='HTML')
                continue
            rounds_list = parse_rounds(data)
            if not rounds_list:
                bot.send_message(message.chat.id, f"🕐 {tournament_name}: немає жеребкування", parse_mode='HTML')
                continue
            player_map = parse_players(data)
            round_num  = len(rounds_list)
            pairs      = rounds_list[-1].get('pairs', [])
            bot.send_message(message.chat.id, f"📅 <b>{tournament_name}</b> — Тур {round_num}", parse_mode='HTML')
            for student_name in tournament_info.get('students', []):
                if student_name not in db_local.get('students', {}):
                    continue
                pairing = find_player_pairing(pairs, student_name, player_map)
                if pairing:
                    bot.send_message(message.chat.id, format_pairing_message(pairing, round_num, tournament_name), parse_mode='HTML')
                else:
                    bot.send_message(message.chat.id, f"🤷 {student_name} — не знайдено в турі {round_num}", parse_mode='HTML')

    elif text == "➕ Додати учня" and is_admin(message):
        user_states[user_id] = 'waiting_add_student'
        bot.send_message(message.chat.id,
            "Напиши ПІБ учня (і @telegram якщо є):\n<i>Іваненко Іван @ivan</i>",
            parse_mode='HTML', reply_markup=keyboard_cancel())

    elif text == "➕ Додати турнір" and is_admin(message):
        user_states[user_id] = 'waiting_tournament_data'
        bot.send_message(message.chat.id,
            "📝 Формат:\n<code>ID | Назва | Учень1, Учень2</code>",
            parse_mode='HTML', reply_markup=keyboard_cancel())

    elif text == "⚠️ Незареєстровані" and is_admin(message):
        not_reg = [n for n, i in db_local.get('students', {}).items() if not i.get('chat_id')]
        if not not_reg:
            bot.send_message(message.chat.id, "✅ Всі зареєстровані!", reply_markup=keyboard_teacher())
        else:
            bot.send_message(message.chat.id,
                f"⏳ <b>Не писали /start ({len(not_reg)}):</b>\n\n" + "\n".join(f"• {n}" for n in not_reg),
                parse_mode='HTML', reply_markup=keyboard_teacher())

    elif text == "♟️ Мої турніри":
        student_name = get_student_name(user_id)
        if not student_name:
            bot.send_message(message.chat.id, "❌ Тебе немає в списку учнів.", reply_markup=keyboard_student())
            return
        my_t = [f"🎯 {i.get('name',tid)} (Тур {i.get('last_round',0)})"
                for tid, i in db_local.get('tournaments', {}).items()
                if student_name in i.get('students', [])]
        if not my_t:
            bot.send_message(message.chat.id, "У тебе поки немає активних турнірів.", reply_markup=keyboard_student())
        else:
            bot.send_message(message.chat.id, "♟️ <b>Твої турніри:</b>\n\n" + "\n".join(my_t),
                             parse_mode='HTML', reply_markup=keyboard_student())

    elif text == "♟️ Моє жеребкування":
        student_name = get_student_name(user_id)
        if not student_name:
            bot.send_message(message.chat.id, "❌ Тебе немає в списку учнів.", reply_markup=keyboard_student())
            return
        bot.send_chat_action(message.chat.id, 'typing')
        found = False
        for tournament_id, tournament_info in db_local.get('tournaments', {}).items():
            if student_name not in tournament_info.get('students', []):
                continue
            data = fetch_tournament_data(tournament_id)
            if not data:
                continue
            rounds_list = parse_rounds(data)
            if not rounds_list:
                continue
            pairing = find_player_pairing(rounds_list[-1].get('pairs', []), student_name, parse_players(data))
            if pairing:
                found = True
                bot.send_message(message.chat.id,
                    format_pairing_message(pairing, len(rounds_list), tournament_info.get('name', tournament_id)),
                    parse_mode='HTML', reply_markup=keyboard_student())
        if not found:
            bot.send_message(message.chat.id, "У тебе поки немає жеребкування.", reply_markup=keyboard_student())

    elif text == "ℹ️ Допомога":
        if is_admin(message):
            bot.send_message(message.chat.id,
                "⚙️ <b>Smart Арбітр Bot v3</b>\n\n"
                "📋 Список учнів\n🎯 Турніри — моніторинг\n"
                "🔍 Перевірити жеребкування\n➕ Додати учня / турнір\n"
                "⚠️ Незареєстровані\n\n"
                "📨 Розсилка жеребкування — з кнопки в Smart Арбітрі\n"
                "📥 Заявки з бота — у вкладці 📋 Заявки в додатку",
                parse_mode='HTML', reply_markup=keyboard_teacher())
        else:
            bot.send_message(message.chat.id,
                "♟️ <b>Smart Арбітр Bot v3</b>\n\n"
                "🏆 Активні турніри — переглянути всі\n"
                "📝 Зареєструватись — подати заявку\n"
                "♟️ Мої турніри — мої активні турніри\n"
                "♟️ Моє жеребкування — мої пари\n\n"
                "Бот надішле сповіщення коли з'явиться новий тур!",
                parse_mode='HTML', reply_markup=keyboard_student())

# ═══════════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/notify-reg', methods=['POST', 'OPTIONS'])
def notify_reg():
    """Сповіщення гравця про статус заявки (approve/reject)."""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400

    if data.get('secret') != BOT_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401

    telegram_id     = data.get('telegram_id')
    player_name     = data.get('player_name', '—')
    tournament_name = data.get('tournament_name', 'Турнір')
    status          = data.get('status', '')

    if not telegram_id:
        return jsonify({'error': 'No telegram_id'}), 400

    try:
        chat_id = int(telegram_id)
    except Exception:
        return jsonify({'error': 'Invalid telegram_id'}), 400

    if status == 'approved':
        msg = (f"✅ <b>Заявку підтверджено!</b>

"
               f"🏆 {tournament_name}
"
               f"👤 {player_name}

"
               f"Ти доданий до списку учасників. "
               f"Бот надішле сповіщення коли з'явиться жеребкування! ♟️")
    elif status == 'rejected':
        msg = (f"❌ <b>Заявку відхилено</b>

"
               f"🏆 {tournament_name}
"
               f"👤 {player_name}

"
               f"Зверніться до організатора турніру для уточнення.")
    else:
        return jsonify({'error': 'Unknown status'}), 400

    try:
        bot.send_message(chat_id, msg, parse_mode='HTML', reply_markup=keyboard_student())
        log.info(f"✅ notify-reg → {telegram_id} ({status})")
        return jsonify({'status': 'ok'})
    except Exception as e:
        log.error(f"❌ notify-reg send error: {e}")
        return jsonify({'error': str(e)}), 500


def run_web():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    print(f"♟️ Smart Арбітр Bot v3 | Admin: {ADMIN_ID}")
    init_firebase()
    Thread(target=run_web, daemon=True).start()
    Thread(target=auto_check_loop, daemon=True).start()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
