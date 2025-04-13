import sqlite3
import os
from datetime import datetime, timedelta, timezone, date
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from database import connect, add_user, get_user, get_all_users, DB_PATH

# Конфигурация
api_id = int(os.environ.get("API_ID"))
api_hash = os.environ.get("API_HASH")
bot_token = os.environ.get("BOT_TOKEN")
admin_id = int(os.environ.get("ADMIN_ID"))

# Инициализация
print(f"Текущая директория: {os.getcwd()}")
connect()
app = Client("hockey_predictor_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
session = {}

@app.on_message(filters.command("start"))
def start(client, message):
    user = message.from_user
    print(f"Пользователь {user.id} запустил бота. Username: {user.username}, First name: {user.first_name}")
    
    if not get_user(user.id):
        add_user(user.id, user.username, user.first_name, user.last_name)

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("📅 Матчи")],
        [KeyboardButton("📊 Таблица лидеров")],
        [KeyboardButton("👀 Посмотреть прогнозы")]
    ], resize_keyboard=True)

    message.reply_text(f"Привет, {user.first_name or 'Пользователь'}! 🏒 Добро пожаловать в хоккейный предиктор!", reply_markup=keyboard)

@app.on_message(filters.command("add_match") & filters.user(ADMIN_ID))
def add_match(client, message):
    parts = message.text.strip().split()
    if len(parts) != 5:
        message.reply_text("⚠️ Неверный формат. Используйте: /add_match <Команда1> <Команда2> <ДД-ММ> <ЧЧ:ММ>")
        return

    _, team1, team2, date_part, time_part = parts
    try:
        day, month = map(int, date_part.split("-"))
        match_date = date(2025, month, day).strftime("%Y-%m-%d")
        datetime.strptime(time_part, "%H:%M")
    except:
        message.reply_text("❗ Проверьте формат даты и времени.")
        return

    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO matches (team1, team2, match_date, match_time) VALUES (?, ?, ?, ?)",
                   (team1, team2, match_date, time_part))
    conn.commit()
    conn.close()

    message.reply_text(f"✅ Матч {team1} vs {team2} добавлен на {date_part} в {time_part}.")

    try:
        users = get_all_users()
        for user_id in users:
            try:
                app.send_message(
                    user_id,
                    f"📣 Новый матч для прогноза: {team1} vs {team2} — {date_part} в {time_part}"
                )
            except Exception as e:
                print(f"❗ Не удалось отправить сообщение {user_id}: {e}")
    except Exception as e:
        print(f"❗ Ошибка рассылки: {e}")

@app.on_message(filters.text & filters.regex(r"^📅 Матчи$"))
def show_matches(client, message):
    now_moscow = datetime.now(timezone.utc) + timedelta(hours=3)

    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT match_id, team1, team2, match_date, match_time, result FROM matches")
    matches = cursor.fetchall()
    conn.close()

    keyboard = []
    for match in matches:
        match_id, team1, team2, match_date, match_time, result = match
        
        # Пропускаем матчи с уже введенными результатами
        if result:
            continue
            
        match_time = match_time or "00:00"
        try:
            match_datetime = datetime.strptime(f"{match_date} {match_time}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            if now_moscow >= match_datetime:
                continue

            formatted_date = datetime.strptime(match_date, "%Y-%m-%d").strftime("%d.%m")
            keyboard.append([InlineKeyboardButton(f"{team1} vs {team2} — {formatted_date}", callback_data=f"predict_{match_id}")])
        except Exception as e:
            print(f"Ошибка при обработке матча {match_id}: {e}")

    if keyboard:
        message.reply_text("Выберите матч для прогноза:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        message.reply_text("❗ Нет доступных матчей для прогнозов.")

@app.on_callback_query(filters.regex(r"^predict_\d+$"))
def ask_for_prediction(client, callback_query):
    match_id = int(callback_query.data.split("_")[1])
    user_id = callback_query.from_user.id
    
    # Проверяем, есть ли уже результат для этого матча
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT team1, team2, result FROM matches WHERE match_id = ?", (match_id,))
    match_info = cursor.fetchone()
    conn.close()
    
    if not match_info:
        callback_query.answer("Матч не найден")
        return
        
    team1, team2, result = match_info
    
    # Если результат уже введен, не даем делать прогноз
    if result:
        callback_query.answer(f"Для этого матча уже введен результат: {result}")
        return
    
    # Сохраняем выбор пользователя в сессии
    session[f"prediction_{user_id}"] = match_id
    
    # Отвечаем на callback, чтобы убрать "часики" у кнопки
    callback_query.answer()
    
    # Отправляем сообщение с запросом прогноза
    callback_query.message.reply_text(f"📝 Введите ваш прогноз для матча {team1} vs {team2} в формате 2:1")

@app.on_message(filters.text & filters.regex(r"^\d+:\d+$"))
def handle_score_input(client, message):
    user_id = message.from_user.id
    score = message.text.strip()

    # Обработка ввода результата матча администратором
    if f"add_result_{user_id}" in session:
        match_id = session[f"add_result_{user_id}"]
        conn = sqlite3.connect("hockey.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("UPDATE matches SET result = ? WHERE match_id = ?", (score, match_id))
        conn.commit()
        calculate_points(match_id, score)
        del session[f"add_result_{user_id}"]
        message.reply_text(f"✅ Результат {score} сохранён и баллы начислены!")
        return

    # Обработка ввода прогноза пользователем
    if f"prediction_{user_id}" not in session:
        message.reply_text("❗ Сначала выберите матч для прогноза.")
        return

    match_id = session[f"prediction_{user_id}"]
    
    # Проверяем, есть ли уже результат для этого матча
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT result FROM matches WHERE match_id = ?", (match_id,))
    result = cursor.fetchone()
    
    if result and result[0]:
        message.reply_text(f"❗ Для этого матча уже введен результат: {result[0]}. Прогноз не может быть сохранен.")
        del session[f"prediction_{user_id}"]
        conn.close()
        return
    
    # Сохраняем прогноз
    cursor.execute("INSERT OR REPLACE INTO predictions (user_id, match_id, prediction) VALUES (?, ?, ?)", (user_id, match_id, score))
    conn.commit()
    conn.close()
    del session[f"prediction_{user_id}"]
    message.reply_text(f"✅ Ваш прогноз {score} сохранён!")

@app.on_message(filters.text & filters.regex(r"^👀 Посмотреть прогнозы$"))
def show_prediction_menu(client, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Мои прогнозы", callback_data="my_predictions")],
        [InlineKeyboardButton("👥 Прогнозы других", callback_data="all_predictions")]
    ])
    message.reply_text("📊 Выберите, что хотите посмотреть:", reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"^my_predictions$"))
def show_my_predictions(client, callback_query):
    user_id = callback_query.from_user.id
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT matches.team1, matches.team2, predictions.prediction, matches.result
        FROM predictions
        JOIN matches ON predictions.match_id = matches.match_id
        WHERE predictions.user_id = ?
    """, (user_id,))
    predictions = cursor.fetchall()
    conn.close()

    # Отвечаем на callback, чтобы убрать "часики" у кнопки
    callback_query.answer()

    if not predictions:
        callback_query.message.reply_text("❗ У вас нет прогнозов.")
    else:
        response = "📄 **Ваши прогнозы:**\n\n"
        for team1, team2, prediction, result in predictions:
            status = f"✅ Результат: {result}" if result else "⏳ Ожидается"
            response += f"{team1} vs {team2} — {prediction} | {status}\n"
        callback_query.message.reply_text(response)

@app.on_callback_query(filters.regex(r"^all_predictions$"))
def show_all_predictions(client, callback_query):
    user_id = callback_query.from_user.id
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT users.username, users.first_name, matches.team1, matches.team2, predictions.prediction, matches.result
        FROM predictions
        JOIN matches ON predictions.match_id = matches.match_id
        JOIN users ON predictions.user_id = users.user_id
        WHERE predictions.user_id != ?
    """, (user_id,))
    predictions = cursor.fetchall()
    conn.close()

    # Отвечаем на callback, чтобы убрать "часики" у кнопки
    callback_query.answer()

    if not predictions:
        callback_query.message.reply_text("❗ Нет прогнозов от других пользователей.")
    else:
        response = "👥 **Прогнозы других игроков:**\n\n"
        current_user = None
        for username, first_name, team1, team2, prediction, result in predictions:
            display_name = username or first_name or f"Пользователь"
            if display_name != current_user:
                current_user = display_name
                response += f"\n**{display_name}**\n"
            status = f"✅ Результат: {result}" if result else "⏳ Ожидается"
            response += f"{team1} vs {team2} — {prediction} | {status}\n"
        callback_query.message.reply_text(response)

@app.on_message(filters.command("add_result") & filters.user(ADMIN_ID))
def show_matches_for_result(client, message):
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT match_id, team1, team2 FROM matches WHERE result IS NULL")
    matches = cursor.fetchall()
    conn.close()

    if not matches:
        message.reply_text("❗ Нет матчей без результата.")
        return

    keyboard = [
        [InlineKeyboardButton(f"{team1} vs {team2}", callback_data=f"add_result_{match_id}")]
        for match_id, team1, team2 in matches
    ]
    message.reply_text("📊 Выберите матч для добавления результата:", reply_markup=InlineKeyboardMarkup(keyboard))

@app.on_callback_query(filters.regex(r"^add_result_\d+$"))
def ask_for_result(client, callback_query):
    match_id = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id
    session[f"add_result_{user_id}"] = match_id
    
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT team1, team2 FROM matches WHERE match_id = ?", (match_id,))
    match_info = cursor.fetchone()
    conn.close()
    
    # Отвечаем на callback, чтобы убрать "часики" у кнопки
    callback_query.answer()
    
    if match_info:
        team1, team2 = match_info
        callback_query.message.reply_text(f"📝 Введите результат матча {team1} vs {team2} в формате 2:1")
    else:
        callback_query.message.reply_text("📝 Введите результат матча в формате 2:1")

@app.on_message(filters.text & filters.regex(r"^📊 Таблица лидеров$"))
def show_leaderboard(client, message):
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    
    # Проверяем, существует ли таблица leaderboard
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leaderboard'")
    if not cursor.fetchone():
        message.reply_text("❗ Таблица лидеров еще не создана.")
        conn.close()
        return
    
    cursor.execute("""
        SELECT users.username, users.first_name, leaderboard.points
        FROM leaderboard
        JOIN users ON leaderboard.user_id = users.user_id
        ORDER BY leaderboard.points DESC
    """)
    leaderboard = cursor.fetchall()
    conn.close()

    if not leaderboard:
        message.reply_text("❗ Пока нет данных для таблицы лидеров.")
        return

    response = "🏆 **Таблица лидеров:**\n\n"
    for i, (username, first_name, points) in enumerate(leaderboard, 1):
        medal = ""
        if i == 1:
            medal = "🥇 "
        elif i == 2:
            medal = "🥈 "
        elif i == 3:
            medal = "🥉 "
        else:
            medal = f"{i}. "
        
        display_name = username or first_name or "Пользователь"
        response += f"{medal}**{display_name}** — {points} баллов\n"
    message.reply_text(response)

def calculate_points(match_id, result, conn=None):
    try:
        r1, r2 = map(int, result.split(":"))
        outcome = (r1 > r2) - (r1 < r2)
        diff = abs(r1 - r2)

        own_connection = False
        if conn is None:
            conn = sqlite3.connect("hockey.db", check_same_thread=False)
            own_connection = True

        cursor = conn.cursor()
        cursor.execute("SELECT team1, team2 FROM matches WHERE match_id = ?", (match_id,))
        match_info = cursor.fetchone()
        
        if not match_info:
            print(f"Матч с ID {match_id} не найден")
            if own_connection:
                conn.close()
            return
            
        team1, team2 = match_info

        cursor.execute("SELECT user_id, prediction FROM predictions WHERE match_id = ?", (match_id,))
        predictions = cursor.fetchall()

        for user_id, prediction in predictions:
            try:
                p1, p2 = map(int, prediction.split(":"))
            except:
                continue

            user_outcome = (p1 > p2) - (p1 < p2)
            user_diff = abs(p1 - p2)

            if p1 == r1 and p2 == r2:
                points = 3
            elif user_outcome == outcome and user_diff == diff:
                points = 2
            elif user_outcome == outcome:
                points = 1
            else:
                points = 0

            cursor.execute("INSERT OR IGNORE INTO leaderboard (user_id, points) VALUES (?, 0)", (user_id,))
            cursor.execute("UPDATE leaderboard SET points = points + ? WHERE user_id = ?", (points, user_id))

            try:
                app.send_message(
                    user_id,
                    f"🏁 Результат матча {team1} vs {team2}: {r1}:{r2}\n"
                    f"🧠 Ваш прогноз: {p1}:{p2}\n"
                    f"🎯 Начислено баллов: {points}"
                )
            except Exception as e:
                print(f"Не удалось отправить результат {user_id}: {e}")

        if own_connection:
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"Ошибка при начислении очков: {e}")
        if conn and own_connection:
            conn.close()

@app.on_message(filters.command("matches") & filters.user(ADMIN_ID))
def list_all_matches(client, message):
    conn = sqlite3.connect("hockey.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT match_id, team1, team2, match_date, match_time, result FROM matches")
    matches = cursor.fetchall()
    conn.close()

    if not matches:
        message.reply_text("❗ Нет матчей в базе данных.")
        return

    response = "📋 **Список всех матчей:**\n\n"
    for match in matches:
        match_id, team1, team2, match_date, match_time, result = match
        formatted_date = datetime.strptime(match_date, "%Y-%m-%d").strftime("%d.%m")
        result_text = f"Результат: {result}" if result else "Результат не установлен"
        response += f"ID: {match_id} | {team1} vs {team2} | {formatted_date} {match_time} | {result_text}\n\n"

    message.reply_text(response)

@app.on_message(filters.command("debug") & filters.user(ADMIN_ID))
def debug_info(client, message):
    # Выводим информацию о текущей директории и файлах
    current_dir = os.getcwd()
    files = os.listdir(current_dir)
    
    response = f"📂 Текущая директория: {current_dir}\n\n"
    response += "📄 Файлы в директории:\n"
    for file in files:
        file_path = os.path.join(current_dir, file)
        file_size = os.path.getsize(file_path) / 1024  # KB
        response += f"- {file} ({file_size:.2f} KB)\n"
    
    # Проверяем наличие базы данных
    db_path = os.path.join(current_dir, "hockey.db")
    if os.path.exists(db_path):
        response += f"\n✅ База данных найдена: {db_path} ({os.path.getsize(db_path)/1024:.2f} KB)"
        
        # Выводим информацию о таблицах
        conn = sqlite3.connect("hockey.db", check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        response += "\n\n📊 Таблицы в базе данных:\n"
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cursor.fetchone()[0]
            response += f"- {table[0]}: {count} записей\n"
        
        conn.close()
    else:
        response += f"\n❌ База данных не найдена по пути: {db_path}"
    
    message.reply_text(response)

@app.on_message(filters.command("wipe") & filters.user(ADMIN_ID))
def wipe_database(client, message):
    try:
        import os
        from database import DB_PATH
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        connect()
        message.reply_text("♻️ База данных очищена и создана заново.")
    except Exception as e:
        message.reply_text(f"❗ Ошибка при очистке: {e}")


print("🚀 Бот запущен.")
app.run()
