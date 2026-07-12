import telebot, ollama, json, os, threading, time, requests, re, subprocess
from bs4 import BeautifulSoup
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

BOT_TOKEN = "8749606311:AAF9cDDB3qS7XAyFCC4t1vnWPB6lnvC961E"
MODEL_NAME = "qwen2.5:1.5b"
MEMORY_FILE = "bot_memory.json"
bot = telebot.TeleBot(BOT_TOKEN)

# Глобальная инициализация памяти
if os.path.exists(MEMORY_FILE):
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        try: 
            bot_memory = json.load(f)
            if not isinstance(bot_memory, dict): 
                bot_memory = {}
        except: 
            bot_memory = {}
else: 
    bot_memory = {}

def run_server():
    try:
        TCPServer.allow_reuse_address = True
        with TCPServer(("", 8778), SimpleHTTPRequestHandler) as httpd:
            httpd.serve_forever()
    except: pass

def search_web(query):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(f"https://duckduckgo.com{query}", headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        return " ".join([a.text for a in soup.find_all('a', class_='result__snippet')][:3])
    except: return "Нет сети."

def get_main_kb():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    m.row(KeyboardButton("🎮 Начать текстовый квест"), KeyboardButton("🧠 Загадка от ИИ"))
    m.row(KeyboardButton("🌐 Запустить сервер"), KeyboardButton("🤖 Чатовод"))
    m.row(KeyboardButton("💬 Давай поболтаем"))
    return m

def get_rating_kb(msg_id):
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("👍", callback_data=f"like_{msg_id}"),
           InlineKeyboardButton("👎", callback_data=f"dislike_{msg_id}"))
    return kb

@bot.message_handler(commands=['start'])
def start(m): 
    bot.reply_to(m, "Архитектор готов.", reply_markup=get_main_kb())

@bot.message_handler(func=lambda m: True)
def handle_msg(m):
    global bot_memory  # ИСПРАВЛЕНО: объявляем переменную как глобальную, чтобы избежать UnboundLocalError
    user_text = m.text
    chat_id = str(m.chat.id)
    
    if user_text == "🌐 Запустить сервер":
        threading.Thread(target=run_server, daemon=True).start()
        bot.reply_to(m, "✅ Сервер: http://localhost:8778")
        return
    elif user_text == "🤖 Чатовод":
        bot.reply_to(m, "🤖 Чатовод активен.")
        return
    elif user_text == "🎮 Начать текстовый квест":
        user_text = "Начни текстовый квест-выживание в IT."
    elif user_text == "🧠 Загадка от ИИ":
        user_text = "Загадай IT загадку."
        
    status = bot.reply_to(m, "⏳ *Размышляю...*", parse_mode="Markdown")
    ctx = ""
    if chat_id in bot_memory and isinstance(bot_memory[chat_id], dict):
        last = list(bot_memory[chat_id].values())[-4:]
        ctx = "\n".join([f"П: {e.get('user_text')}\nА: {e.get('bot_text')}" for e in last if isinstance(e, dict)])
        
    try:
        stream = ollama.chat(model=MODEL_NAME, messages=[
            {'role': 'system', 'content': 'Ты ИИ-Архитектор. Создаешь сайт - пиши код в ```html ... ```. Нужна команда терминала - пиши в ```bash ... ```.'},
            {'role': 'user', 'content': f"История:\n{ctx}\nВопрос: {user_text}"}
        ], stream=True)
        
        reply_text = ""
        last_upd = 0
        
        for chunk in stream:
            delta = chunk.get('message', {}).get('content', '')
            if delta:
                reply_text += delta
                if time.time() - last_upd > 1.5:
                    display_text = reply_text if len(reply_text) < 3800 else reply_text[:3800] + "\n\n...[Обрезано]..."
                    try: bot.edit_message_text(display_text + " ▌", m.chat.id, status.message_id)
                    except: pass
                    last_upd = time.time()
                    
        extra_msg = ""
        html_match = re.search(r'```html\n(.*?)\n```', reply_text, re.DOTALL)
        if html_match:
            with open("index.html", "w", encoding="utf-8") as f: f.write(html_match.group(1))
            extra_msg += "\n\n✅ *index.html сохранен!*"
            
        cmd_match = re.search(r'```bash\n(.*?)\n```', reply_text, re.DOTALL)
        kb = None
        if cmd_match:
            cmd = cmd_match.group(1).strip()
            extra_msg += f"\n\n⚠️ **Выполнить?**\n`{cmd}`"
            kb = InlineKeyboardMarkup().row(
                InlineKeyboardButton("✅ Да", callback_data=f"exec_{status.message_id}"),
                InlineKeyboardButton("❌ Нет", callback_data=f"cancel_{status.message_id}")
            )
        else:
            kb = get_rating_kb(status.message_id)
            
        final_display = reply_text if len(reply_text) < 3800 else reply_text[:3800] + "\n\n*[Обрезано]*"
        final_display += extra_msg
        
        sent = bot.edit_message_text(final_display, m.chat.id, status.message_id, reply_markup=kb, parse_mode="Markdown")
        
        if not isinstance(bot_memory, dict): 
            bot_memory = {}
        if chat_id not in bot_memory or not isinstance(bot_memory[chat_id], dict): 
            bot_memory[chat_id] = {}
            
        bot_memory[chat_id][str(sent.message_id)] = {
            "user_text": user_text, 
            "bot_text": reply_text, 
            "cmd": cmd_match.group(1).strip() if cmd_match else ""
        }
        with open(MEMORY_FILE, "w", encoding="utf-8") as f: 
            json.dump(bot_memory, f, ensure_ascii=False)
            
    except Exception as e: 
        try: bot.edit_message_text(f"❌ Ошибка в handle_msg: {e}", m.chat.id, status.message_id)
        except: pass

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    try:
        data = c.data.split("_")
        if len(data) < 2: return
        
        action = data
        msg_id = data
        chat_id = str(c.message.chat.id)
        
        if action == "exec":
            cmd = bot_memory.get(chat_id, {}).get(msg_id, {}).get("cmd")
            if cmd:
                bot.edit_message_text(f"⏳ Выполняю:\n`{cmd}`", int(chat_id), c.message.message_id, parse_mode="Markdown")
                try:
                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
                    out = res.stdout if res.stdout else res.stderr
                    if not out: out = "[Команда выполнена без вывода текста]"
                    bot.edit_message_text(f"✅ **Результат:**\n`{out}`", int(chat_id), c.message.message_id, parse_mode="Markdown")
                except Exception as e:
                    bot.edit_message_text(f"❌ *Ошибка при выполнении:* \n`{e}`", int(chat_id), c.message.message_id, parse_mode="Markdown")
        elif action == "cancel":
            bot.edit_message_text("❌ Выполнение команды отменено.", int(chat_id), c.message.message_id)
        elif action in ["like", "dislike"]:
            bot.answer_callback_query(c.id, text="Спасибо за оценку! 👍" if action == "like" else "Принято, будем улучшать! 👎")
    except Exception as e:
        try: bot.answer_callback_query(c.id, text=f"Ошибка кнопок: {e}")
        except: pass

if __name__ == "__main__":
    print("Бот Mr_IT_Architect успешно запущен и слушает команды!")
    bot.infinity_polling()
