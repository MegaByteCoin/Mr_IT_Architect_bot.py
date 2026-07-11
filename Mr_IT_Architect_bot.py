import telebot
import ollama
import json
import os
import subprocess
import threading
import random
import requests
import time
from bs4 import BeautifulSoup
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

ADMIN_ID = "all"
BOT_TOKEN = "8749606311:AAF9cDDB3qS7XAyFCC4t1vnWPB6lnvC961E"
MODEL_NAME = "qwen2.5:1.5b"  
BOT_NICKNAME = "Архитектор"  

bot = telebot.TeleBot(BOT_TOKEN)
MEMORY_FILE = "bot_memory.json"
CHATOVOD_FILE = "chatovod_config.json"

PENDING_COMMANDS = {}
USER_STATES = {}  

chatovod_session = None
chatovod_active = False
last_msg_id = 0

if os.path.exists(MEMORY_FILE):
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        bot_memory = json.load(f)
else:
    bot_memory = {}

if os.path.exists(CHATOVOD_FILE):
    with open(CHATOVOD_FILE, "r", encoding="utf-8") as f:
        chatovod_config = json.load(f)
else:
    chatovod_config = {"url": "", "email": "", "password": "", "prompt": "Ты вежливый ИИ-помощник."}

print("Собираем код бота...")

def start_builtin_server(port):
    def server_thread():
        try:
            TCPServer.allow_reuse_address = True
            with TCPServer(("", port), SimpleHTTPRequestHandler) as httpd:
                print(f"Локальный сервер запущен на порту {port}")
                httpd.serve_forever()
        except Exception as e:
            print(f"Ошибка сервера: {e}")
    threading.Thread(target=server_thread, daemon=True).start()

def run_system_command(command):
    try:
        if "http.server" in command or "python" in command and "-m" in command:
            port = 8778
            for word in command.split():
                if word.isdigit(): port = int(word)
            start_builtin_server(port)
            return f"🚀 Встроенный веб-сервер успешно запущен на порту {port}!"
        result = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=15)
        output = result.stdout if result.stdout else ""
        error = result.stderr if result.stderr else ""
        return f"Вывод:\n```\n{output}\n```" if output else f"Лог:\n```\n{error}\n```"
    except Exception as e:
        return f"Ошибка выполнения: {e}"

def get_main_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("🎮 Начать текстовый квест"), KeyboardButton("🧠 Загадка от ИИ"))
    markup.row(KeyboardButton("🤖 Чатовод"), KeyboardButton("💬 Давай поболтаем"))
    return markup

def get_chatovod_inline():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("⚙️ Настройка Данных", callback_data="chatovod_setup"))
    keyboard.row(InlineKeyboardButton("📝 Задать Роль (Промт)", callback_data="chatovod_prompt"))
    keyboard.row(InlineKeyboardButton("🔑 Войти в чат", callback_data="chatovod_login"),
                 InlineKeyboardButton("📡 Читать чат (Старт)", callback_data="chatovod_start"))
    keyboard.row(InlineKeyboardButton("🚪 Выйти из чата", callback_data="chatovod_logout"))
    return keyboard

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    bot.reply_to(message, "Привет!", reply_markup=get_main_keyboard())

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = str(message.chat.id)
    bot.send_chat_action(chat_id, 'typing')
    status_msg = bot.reply_to(message, "⏳ **[ИИ Статус]:** 📥 *Скачиваю изображение...*", parse_mode="Markdown")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        img_name = "background.jpg"
        with open(img_name, 'wb') as new_file:
            new_file.write(downloaded_file)
        bot.edit_message_text(f"⏳ **[ИИ Статус]:** ✅ *Картинка сохранена как `{img_name}`!*", chat_id, status_msg.message_id, parse_mode="Markdown")
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {e}", chat_id, status_msg.message_id)
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    user_name = message.from_user.first_name or "Пользователь"
    text = message.text

    if user_id in USER_STATES:
        state = USER_STATES[user_id]
        if state == "wait_url":
            chatovod_config["url"] = text
            USER_STATES[user_id] = "wait_email"
            bot.reply_to(message, "💬 Адрес сохранен. Введите **почту (Email)**:")
        elif state == "wait_email":
            chatovod_config["email"] = text
            USER_STATES[user_id] = "wait_pass"
            bot.reply_to(message, "💬 Почта сохранена. Введите **пароль**:")
        elif state == "wait_pass":
            chatovod_config["password"] = text
            del USER_STATES[user_id]
            with open(CHATOVOD_FILE, "w", encoding="utf-8") as f:
                json.dump(chatovod_config, f, ensure_ascii=False, indent=4)
            bot.reply_to(message, "✅ Данные сохранены!", reply_markup=get_chatovod_inline())
        elif state == "wait_prompt":
            chatovod_config["prompt"] = text
            del USER_STATES[user_id]
            with open(CHATOVOD_FILE, "w", encoding="utf-8") as f:
                json.dump(chatovod_config, f, ensure_ascii=False, indent=4)
            bot.reply_to(message, f"✅ Новая роль записана:\n*{text}*", parse_mode="Markdown", reply_markup=get_chatovod_inline())
        return

    if text == "🤖 Чатовод":
        bot.reply_to(message, "Панель управления Чатовода:", reply_markup=get_chatovod_inline())
        return
    elif text == "🎮 Начать текстовый квест":
        text = "Придумай и начни для меня интересный текстовый квест-выживание с вариантами действий"
    elif text == "🧠 Загадка от ИИ":
        text = "Загадай мне хитрую загадку на логику или программирование"
    elif text == "💬 Давай поболтаем":
        text = "Привет, давай просто пообщаемся"

    if message.chat.type in ['group', 'supergroup']:
        is_triggered = (
            (message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id) or 
            f"@{bot.get_me().username}" in text or 
            BOT_NICKNAME.lower() in text.lower() or 
            random.random() < 0.15
        )
        if not is_triggered: return
        text = text.replace(f"@{bot.get_me().username}", "").replace(BOT_NICKNAME, "").strip()

    bot.send_chat_action(chat_id, 'typing')
    status_msg = bot.reply_to(message, "⏳ **[ИИ Статус]:** 🧠 *Размышляю...*", parse_mode="Markdown")
    context_str = "\n".join(bot_memory[chat_id][-5:]) if chat_id in bot_memory else ""

    is_site_request = any(word in text.lower() for word in ["сайт", "страниц", "html", "web", "веб"])

    if is_site_request:
        system_prompt = (
            "Пользователь хочет создать веб-страницу (сайт). Сгенерируй полноценный красивый HTML-код. "
            "Если на сервере есть файл background.jpg, обязательно используй его в качестве фона в стилях CSS (background-image: url('background.jpg');). "
            "Отдай этот HTML-код СТРОГО внутри ОДНОГО блока кода Markdown с меткой html. Больше ничего не пиши.\n"
            f"История чата:\n{context_str}"
        )
    else:
        system_prompt = (
            "Ты — продвинутый ИИ-Архитектор, эксперт в программировании, администрировании Termux и классный собеседник для игр и флуда. "
            "Если пользователь просит выполнить задачу на сервере, пиши команду СТРОГО внутри блока кода Markdown с меткой shell или bash.\n"
            f"История чата:\n{context_str}"
        )
    try:
        bot.edit_message_text("⏳ **[ИИ Статус]:** ⚙️ *Генерирую решение...*", chat_id, status_msg.message_id, parse_mode="Markdown")
        response = ollama.chat(model=MODEL_NAME, messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{user_name}: {text}"}
        ])
        reply_text = response['message']['content']

        if is_site_request and "```html" in reply_text:
            bot.edit_message_text("⏳ **[ИИ Статус]:** 📝 *Записываю HTML-код...*", chat_id, status_msg.message_id, parse_mode="Markdown")
            start = reply_text.find("```html") + 7
            end = reply_text.find("```", start)
            with open("index.html", "w", encoding="utf-8") as html_file:
                html_file.write(reply_text[start:end].strip())
            bot.edit_message_text("⏳ **[ИИ Статус]:** 🚀 *Запускаю веб-сервер...*", chat_id, status_msg.message_id, parse_mode="Markdown")
            start_builtin_server(8778)
            bot.send_message(chat_id, f"🎉 **Сайт создан!**\nПроверяй:\nhttp://localhost:8778", parse_mode="Markdown")
            bot.delete_message(chat_id, status_msg.message_id)
        else:
            cmd = ""
            for marker in ["```shell", "```bash", "```"]:
                if marker in reply_text:
                    start = reply_text.find(marker) + len(marker)
                    end = reply_text.find("```", start)
                    if end != -1: cmd = reply_text[start:end].strip(); break

            if cmd:
                bot.edit_message_text("⏳ **[ИИ Статус]:** 🛡 *Проверяю безопасность...*", chat_id, status_msg.message_id, parse_mode="Markdown")
                if any(w in cmd for w in ["rm ", "rmdir", "format", "truncate"]):
                    bot.delete_message(chat_id, status_msg.message_id)
                    keyboard = InlineKeyboardMarkup()
                    keyboard.row(InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{message.message_id}"),
                                 InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_{message.message_id}"))
                    PENDING_COMMANDS[str(message.message_id)] = {"cmd": cmd, "user_id": user_id}
                    bot.reply_to(message, f"⚠️ **Опасная команда:**\n`{cmd}`\nВыполнить?", parse_mode="Markdown", reply_markup=keyboard)
                else:
                    bot.edit_message_text(f"⏳ **[ИИ Статус]:** 🚀 *Выполняю:* `{cmd}`", chat_id, status_msg.message_id, parse_mode="Markdown")
                    cmd_result = run_system_command(cmd)
                    bot.send_message(chat_id, f"{reply_text}\n\n📊 **Результат:**\n{cmd_result}", parse_mode="Markdown")
                    bot.delete_message(chat_id, status_msg.message_id)
            else:
                bot.edit_message_text(reply_text, chat_id, status_msg.message_id, parse_mode="Markdown")

        if chat_id not in bot_memory: bot_memory[chat_id] = []
        bot_memory[chat_id].append(f"{user_name}: {text} -> Бот: {reply_text}")
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_memory, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка: {e}")
        bot.edit_message_text("❌ Ошибка обработки запроса.", chat_id, status_msg.message_id)

def chatovod_monitoring_loop(tg_chat_id):
    global chatovod_session, chatovod_active, last_msg_id
    base_url = chatovod_config["url"].rstrip('/')
    try:
        main_page = chatovod_session.get(base_url, timeout=10)
        chat_id_marker = "chatId: "
        if chat_id_marker in main_page.text:
            start_idx = main_page.text.find(chat_id_marker) + len(chat_id_marker)
            end_idx = main_page.text.find(",", start_idx)
            chat_room_id = main_page.text[start_idx:end_idx].strip().replace("'", "").replace('"', '')
        else:
            chat_room_id = "1"
        ajax_url = f"https://chatovod.ru{chat_room_id}"
        send_url = f"https://chatovod.ru{chat_room_id}"
        bot.send_message(tg_chat_id, f"📡 `[Чатовод]:` Подключение к ID {chat_room_id}! Мониторинг.")
    except Exception as e:
        bot.send_message(tg_chat_id, f"❌ Ошибка API чата: {e}")
        chatovod_active = False
        return

    while chatovod_active:
        try:
            payload = {"lastId": last_msg_id, "v": 2}
            res = chatovod_session.post(ajax_url, data=payload, timeout=10)
            if res.status_code == 200 and res.text.strip().startswith("{"):
                data = res.json()
                if "messages" in data:
                    for msg in data["messages"]:
                        msg_id = int(msg.get("id", 0))
                        if msg_id > last_msg_id:
                            last_msg_id = msg_id
                            if msg.get("type") == "message" and not msg.get("fromMe", False):
                                sender = msg.get("from", {}).get("nick", "Пользователь")
                                msg_text = msg.get("text", "").strip()
                                if not msg_text: continue
                                sys_prompt = f"Ты в онлайн-чате. Твоя роль: {chatovod_config['prompt']}. Отвечай кратко, в один абзац."
                                ai_res = ollama.chat(model=MODEL_NAME, messages=[
                                    {"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": f"{sender}: {msg_text}"}
                                ])
                                ai_reply = ai_res['message']['content']
                                chatovod_session.post(send_url, data={"text": ai_reply, "v": 2}, timeout=10)
                                bot.send_message(tg_chat_id, f"📡 `[Чатовод]` **{sender}:** {msg_text}\n🤖 **Ответ:** {ai_reply}", parse_mode="Markdown")
                if "lastId" in data: last_msg_id = int(data["lastId"])
        except Exception as e:
            print(f"Ошибка API: {e}")
        time.sleep(3)
@bot.callback_query_handler(func=lambda call: True)
def callback_listener(call):
    global chatovod_session, chatovod_active, last_msg_id
    data_parts = call.data.split("_", 1)
    action = data_parts[0]
    msg_id = data_parts[1] if len(data_parts) > 1 else ""

    if action == "chatovod":
        if msg_id == "setup":
            USER_STATES[str(call.from_user.id)] = "wait_url"
            bot.send_message(call.message.chat.id, "⚙️ Введите адрес Чатовода (URL):")
        elif msg_id == "prompt":
            USER_STATES[str(call.from_user.id)] = "wait_prompt"
            bot.send_message(call.message.chat.id, "📝 Введите роль для ИИ:")
        elif msg_id == "login":
            bot.answer_callback_query(call.id, "🔑 Подключаюсь...")
            try:
                chatovod_session = requests.Session()
                chatovod_session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                    "X-Requested-With": "XMLHttpRequest"
                })
                reg_page = chatovod_session.get("https://chatovod.ru")
                soup = BeautifulSoup(reg_page.text, 'html.parser')
                csrf_token = ""
                csrf_input = soup.find('input', {'name': 'csrf'})
                if csrf_input: csrf_token = csrf_input.get('value', '')
                
                login_data = {
                    "email": chatovod_config["email"],
                    "password": chatovod_config["password"],
                    "csrf": csrf_token,
                    "submit": "1"
                }
                chatovod_session.post("https://chatovod.ru", data=login_data, timeout=15)
                
                if "rem" in chatovod_session.cookies.get_dict() or "sid" in chatovod_session.cookies.get_dict():
                    bot.send_message(call.message.chat.id, f"✅ `[Чатовод]:` Авторизация успешна!\nПрофиль: `{chatovod_config['email']}`")
                else:
                    bot.send_message(call.message.chat.id, "❌ `[Чатовод]:` Ошибка авторизации. Проверьте почту и пароль!")
            except Exception as e:
                bot.send_message(call.message.chat.id, f"❌ Ошибка сети: {e}")
                
        elif msg_id == "start":
            if chatovod_session is None:
                bot.answer_callback_query(call.id, "❌ Сначала нажмите кнопку 'Войти в чат'!", show_alert=True)
                return
            bot.answer_callback_query(call.id, "📡 Запуск...")
            chatovod_active = True
            last_msg_id = 0
            threading.Thread(target=chatovod_monitoring_loop, args=(str(call.message.chat.id),), daemon=True).start()
        elif msg_id == "logout":
            bot.answer_callback_query(call.id, "🚪 Выхожу...")
            chatovod_active = False
            chatovod_session = None
            bot.send_message(call.message.chat.id, "🚪 `[Чатовод]:` Вы вышли из чата. Поток мониторинга остановлен.")
        bot.answer_callback_query(call.id)
        return

    if msg_id not in PENDING_COMMANDS:
        bot.answer_callback_query(call.id, "Команда устарела.")
        return
    saved_data = PENDING_COMMANDS[msg_id]

    if action == "confirm":
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        res = run_system_command(saved_data['cmd'])
        bot.send_message(call.message.chat.id, res, parse_mode="Markdown")
    else:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, "❌ Отменено.")
    del PENDING_COMMANDS[msg_id]
    bot.answer_callback_query(call.id)

bot.infinity_polling()

