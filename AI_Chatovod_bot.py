import os
import re
import subprocess
import signal
import json
import telebot
from telebot import types
import time
import shlex

BOT_SCRIPT_PATH = "/root/ai_telegram/bot.py"
CONFIG_FILE = "/root/ai_telegram/bots_config.json"
OLLAMA_MODEL = "/usr/share/ollama/.ollama/models/blobs/sha256-5ee4f07cdb9beadbbb293e85803c569b01bd37ed059d2715faa7bb405f31caa6"  # 3B модель по умолчанию
LOG_FILE = "/root/ai_telegram/bot_manager.log"
PROJECT_DIR = "/root/ai_telegram"


def log(msg):
    """Логирование сообщений в stdout и файл."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"Ошибка записи в лог-файл: {e}")


API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8765087697:AAHB7NDqdJ1jDtPlx5rZRJQJrPonvnINlAw")
if not API_TOKEN or API_TOKEN == "8765087697:AAHB7NDqdJ1jDtPlx5rZRJQJrPonvnINlAw":
    log("⚠️ ВНИМАНИЕ: API токен не задан через переменную окружения TELEGRAM_BOT_TOKEN. Используется дефолтное значение (небезопасно для продакшена).")

bot = telebot.TeleBot(API_TOKEN)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


bots_storage = load_config()
user_states = {}


def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🎮 Управление ботом", "⚙️ Настройки реакции бота на сообщения")
    return markup


def get_settings_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔐 Сменить пароль", callback_data="set_pass"),
        types.InlineKeyboardButton(
            "📝 Изменить Промпт", callback_data="set_prompt"
        ),
        types.InlineKeyboardButton(
            "⏳ Настройка задержек и лимитов", callback_data="set_reaction"
        ),
        types.InlineKeyboardButton("🎲 Выбрать Игру", callback_data="set_game"),
    )
    return markup


@bot.message_handler(commands=["start"])
def start_cmd(message):
    uid = str(message.from_user.id)
    log(f"Команда /start от пользователя {uid}")
    if uid not in bots_storage:
        user_states[uid] = {"step": "wait_nick"}
        bot.send_message(
            message.chat.id,
            "👋 Создаем нового бота для Chatovod.\n\nВведите **Никнейм** бота:",
            parse_mode="Markdown",
        )
        log(f"Начало регистрации нового бота для пользователя {uid}")
    else:
        bot.send_message(
            message.chat.id,
            f"🤖 Бот `{bots_storage[uid]['nick']}` уже настроен.\n\n💬 Вы можете управлять им кнопками или **просто общаться со мной здесь**, отправляя любые текстовые сообщения!",
            reply_markup=get_main_keyboard(),
        )
        log(f"Пользователь {uid} уже имеет настроенного бота: {bots_storage[uid]['nick']}")


@bot.message_handler(
    func=lambda msg: user_states.get(str(msg.from_user.id), {}).get("step")
    == "wait_nick"
)
def get_nick(message):
    uid = str(message.from_user.id)
    user_states[uid]["nick"] = message.text.strip()
    user_states[uid]["step"] = "wait_url"
    bot.send_message(
        message.chat.id, "Теперь отправьте **ссылку на чат** Chatovod:"
    )


@bot.message_handler(
    func=lambda msg: user_states.get(str(msg.from_user.id), {}).get("step")
    == "wait_url"
)
def get_url(message):
    uid = str(message.from_user.id)
    user_states[uid]["chat_url"] = message.text.strip()
    user_states[uid]["step"] = "wait_email"
    bot.send_message(message.chat.id, "Введите **Email** от аккаунта:")


@bot.message_handler(
    func=lambda msg: user_states.get(str(msg.from_user.id), {}).get("step")
    == "wait_email"
)
def get_email(message):
    uid = str(message.from_user.id)
    user_states[uid]["email"] = message.text.strip()
    user_states[uid]["step"] = "wait_pass"
    bot.send_message(message.chat.id, "Введите **Пароль** от аккаунта:")


@bot.message_handler(
    func=lambda msg: user_states.get(str(msg.from_user.id), {}).get("step")
    == "wait_pass"
)
def get_initial_password(message):
    uid = str(message.from_user.id)
    bots_storage[uid] = {
        "nick": user_states[uid]["nick"],
        "chat_url": user_states[uid]["chat_url"],
        "email": user_states[uid]["email"],
        "password": message.text.strip(),
        "prompt": "Ты — полезный ИИ ассистент в чате.",
        "game": "Выключен",
        "delay_min": 0.5,
        "delay_max": 1.5,
        "cooldown": 0,
        "max_answers_per_min": 0,
        "only_mention": False,
    }
    save_config(bots_storage)
    del user_states[uid]
    log(f"Новый бот создан для пользователя {uid}: ник={bots_storage[uid]['nick']}, чат={bots_storage[uid]['chat_url']}")
    bot.send_message(
        message.chat.id,
        "🎉 Конфигурация успешно создана! Панель управления добавлена в поле ввода. Теперь вы также можете просто переписываться со мной.",
        reply_markup=get_main_keyboard(),
    )
@bot.message_handler(func=lambda msg: msg.text == "🎮 Управление ботом")
def control_panel(message):
    uid = str(message.from_user.id)
    if uid not in bots_storage:
        bot.reply_to(message, "Запустите /start для настройки бота.")
        return
    safe_nick = re.sub(r"\W+", "_", bots_storage[uid]["nick"])
    status = (
        "▶️ Запущен"
        if os.path.exists(f"/root/ai_telegram/{safe_nick}.pid")
        else "⏹️ Остановлен"
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("▶️ Старт", callback_data="run"),
        types.InlineKeyboardButton("⏹️ Стоп", callback_data="stop"),
        types.InlineKeyboardButton(
            "📋 Что нового в чате?", callback_data="view_log"
        ),
    )
    bot.send_message(
        message.chat.id,
        f"📊 **Панель Управления**\n\n🤖 Бот: `{bots_storage[uid]['nick']}`\n"
        f"🌐 Чат: {bots_storage[uid]['chat_url']}\n⚡ Статус: `{status}`\n\n"
        f"⚙️ **Лимиты реакции:**\n"
        f"⏱️ Задержка: `{bots_storage[uid].get('delay_min', 0.5)} – {bots_storage[uid].get('delay_max', 1.5)} сек`\n"
        f"⏳ Кулдаун: `{bots_storage[uid].get('cooldown', 0)} сек`\n"
        f"📈 Лимит/мин: `{bots_storage[uid].get('max_answers_per_min', 0)}`\n"
        f"💬 Только по тегу: `{'✅ Да' if bots_storage[uid].get('only_mention', False) else '❌ Нет'}`",
        reply_markup=markup,
        parse_mode="Markdown",
    )


@bot.message_handler(
    func=lambda msg: msg.text == "⚙️ Настройки реакции бота на сообщения"
)
def settings_panel(message):
    uid = str(message.from_user.id)
    if uid not in bots_storage:
        return
    bot.send_message(
        message.chat.id,
        "⚙️ **Настройки бота**\n\nВыберите нужный параметр для изменения:",
        reply_markup=get_settings_markup(),
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data in ["run", "stop", "view_log"])
def handle_control_actions(call):
    uid = str(call.from_user.id)
    data = bots_storage.get(uid)
    if not data:
        bot.answer_callback_query(call.id, "❌ Сначала зарегистрируйте бота через /start", show_alert=True)
        return
    safe_nick = re.sub(r"\W+", "_", data["nick"])
    pid_file = f"/root/ai_telegram/{safe_nick}.pid"
    log_file = f"/root/ai_telegram/{safe_nick}.log"

    if call.data == "run":
        if os.path.exists(pid_file):
            bot.answer_callback_query(
                call.id, "⚠️ Бот уже работает!", show_alert=True
            )
            return

        engine_config = {
            "delay_min": float(data.get("delay_min", 0.5)),
            "delay_max": float(data.get("delay_max", 1.5)),
            "cooldown": int(data.get("cooldown", 0)),
            "max_answers_per_min": int(data.get("max_answers_per_min", 0)),
            "only_mention": bool(data.get("only_mention", False)),
            "prompt": data.get("prompt", "Ты — полезный ИИ ассистент."),
            "game": data.get("game", "Выключен"),
            "use_ollama": True,
            "ollama_model": OLLAMA_MODEL,
        }

        os.makedirs("/root/ai_telegram/configs/", exist_ok=True)
        config_path = f"/root/ai_telegram/configs/{safe_nick}.json"
        log(f"Сохраняю конфиг в {config_path}")
        with open(config_path, "w", encoding="utf-8") as config_file:
            json.dump(engine_config, config_file, ensure_ascii=False, indent=4)
        log(f"Конфиг сохранен успешно")

        cmd = [
            "python3",
            BOT_SCRIPT_PATH,
            data["chat_url"],
            data["nick"],
            pid_file,
            log_file,
            "--email",
            data["email"],
            "--password",
            data["password"],
            "--use-ollama",
            "--prompt",
            data.get("prompt", "Ты — полезный ИИ ассистент."),
            "--ollama-model",
            OLLAMA_MODEL,
        ]
        try:
            # Используем subprocess.Popen с аргументами как список для безопасного запуска
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            bot.answer_callback_query(call.id, "▶️ Запуск...")
            bot.send_message(
                call.message.chat.id,
                f"✅ Бот `{data['nick']}` успешно запущен в чате!",
            )
            log(f"Бот {data['nick']} запущен пользователем {uid}")
        except Exception as e:
            log(f"Ошибка запуска бота {data['nick']}: {e}")
            bot.send_message(call.message.chat.id, f"❌ Ошибка: {e}")

    elif call.data == "stop":
        if not os.path.exists(pid_file):
            bot.answer_callback_query(
                call.id, "⚠️ Бот не запущен.", show_alert=True
            )
            return
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            os.remove(pid_file)
            bot.answer_callback_query(call.id, "🛑 Остановлен!")
            bot.send_message(
                call.message.chat.id, f"🛑 Бот `{data['nick']}` вышел из чата."
            )
            log(f"Бот {data['nick']} остановлен пользователем {uid}")
        except Exception as e:
            log(f"Ошибка остановки бота {data['nick']}: {e}")
            bot.send_message(call.message.chat.id, f"❌ Ошибка: {e}")
            if os.path.exists(pid_file):
                os.remove(pid_file)

    elif call.data == "view_log":
        if not os.path.exists(log_file):
            bot.answer_callback_query(
                call.id,
                "📋 В чате пока пусто или лог еще не создан.",
                show_alert=True,
            )
            return
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-15:]
        log_text = "".join(lines)
        bot.send_message(
            call.message.chat.id,
            f"📋 **Сводка событий из чата Чатовод:**\n\n```\n{log_text[-3500:]}\n```",
            parse_mode="Markdown",
        )
        bot.answer_callback_query(call.id)
@bot.callback_query_handler(
    func=lambda call: call.data
    in ["set_pass", "set_prompt", "set_game", "set_reaction"]
)
def handle_settings_actions(call):
    uid = str(call.from_user.id)
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id, reply_markup=None
    )

    if call.data == "set_pass":
        user_states[uid] = {"step": "change_pass"}
        bot.send_message(
            call.message.chat.id, "🔐 Введите **новый пароль** от аккаунта Chatovod:"
        )

    elif call.data == "set_prompt":
        user_states[uid] = {"step": "change_prompt"}
        bot.send_message(
            call.message.chat.id,
            f"📝 Текущий Промпт:\n`{bots_storage[uid]['prompt']}`\n\nВведите **новый системный промпт**:",
            parse_mode="Markdown",
        )

    elif call.data == "set_game":
        markup = types.InlineKeyboardMarkup(row_width=1)
        games = [
            "Виселица",
            "Викторина",
            "Слова",
            "Правда или ложь",
            "Угадай число",
            "Выключить игры",
        ]
        for g in games:
            markup.add(
                types.InlineKeyboardButton(g, callback_data=f"game_{g}")
            )
        bot.send_message(
            call.message.chat.id,
            "🎲 Выберите игровой режим из списка:",
            reply_markup=markup,
        )

    elif call.data == "set_reaction":
        user_states[uid] = {"step": "wait_delay_min"}
        bot.send_message(
            call.message.chat.id, "⏱️ Шаг 1: Введите **Задержку мин (сек)**:"
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("mention_"))
def save_mention_setting(call):
    uid = str(call.from_user.id)
    if uid not in bots_storage:
        bot.answer_callback_query(call.id, "❌ Ошибка: бот не настроен. Запустите /start", show_alert=True)
        return
    val = call.data.replace("mention_", "") == "yes"
    bots_storage[uid]["only_mention"] = val
    save_config(bots_storage)
    log(f"Настройка 'only_mention' изменена на {val} для пользователя {uid}")
    
    safe_nick = re.sub(r"\W+", "_", bots_storage[uid]["nick"])
    os.makedirs("/root/ai_telegram/configs/", exist_ok=True)
    engine_config = {
        "delay_min": float(bots_storage[uid].get("delay_min", 0.5)),
        "delay_max": float(bots_storage[uid].get("delay_max", 1.5)),
        "cooldown": int(bots_storage[uid].get("cooldown", 0)),
        "max_answers_per_min": int(bots_storage[uid].get("max_answers_per_min", 0)),
        "only_mention": bool(val),
        "prompt": bots_storage[uid].get("prompt", "Ты — полезный ИИ ассистент."),
        "game": bots_storage[uid].get("game", "Выключен"),
        "use_ollama": True,
        "ollama_model": OLLAMA_MODEL,
    }
    with open(f"/root/ai_telegram/configs/{safe_nick}.json", "w", encoding="utf-8") as config_file:
        json.dump(engine_config, config_file, ensure_ascii=False, indent=4)

    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id, reply_markup=None
    )
    bot.send_message(
        call.message.chat.id,
        f"✅ Все настройки реакции успешно сохранены!\n\n"
        f"💬 Отвечать только по упоминанию: `{'Включено' if val else 'Выключено'}`",
        reply_markup=get_main_keyboard(),
    )


@bot.message_handler(func=lambda msg: str(msg.from_user.id) in user_states)
def process_text_settings(message):
    uid = str(message.from_user.id)
    step = user_states[uid]["step"]

    if step == "change_pass":
        bots_storage[uid]["password"] = message.text.strip()
        bot.send_message(message.chat.id, "✅ Пароль успешно обновлен!")
        del user_states[uid]
        save_config(bots_storage)
        log(f"Пароль обновлен для бота пользователя {uid}")

    elif step == "change_prompt":
        bots_storage[uid]["prompt"] = message.text.strip()
        bot.send_message(message.chat.id, "✅ Характер (Промпт) ИИ изменен!")
        del user_states[uid]
        save_config(bots_storage)
        log(f"Промпт изменен для бота пользователя {uid}")

    elif step == "wait_delay_min":
        try:
            delay_min = float(message.text.strip())
            if delay_min < 0:
                bot.send_message(message.chat.id, "❌ Задержка не может быть отрицательной. Введите корректное значение:")
                return
            bots_storage[uid]["delay_min"] = delay_min
            user_states[uid]["step"] = "wait_delay_max"
            bot.send_message(
                message.chat.id, "⏱️ Шаг 2: Введите **Задержку макс (сек)**:"
            )
        except ValueError:
            bot.send_message(message.chat.id, "❌ Некорректное число. Введите задержку в секундах (например, 0.5):")

    elif step == "wait_delay_max":
        try:
            delay_max = float(message.text.strip())
            if delay_max < 0:
                bot.send_message(message.chat.id, "❌ Задержка не может быть отрицательной. Введите корректное значение:")
                return
            if delay_max < bots_storage[uid].get("delay_min", 0.5):
                bot.send_message(message.chat.id, "❌ Максимальная задержка должна быть больше минимальной. Введите корректное значение:")
                return
            bots_storage[uid]["delay_max"] = delay_max
            # user_states[uid]["step"] = "wait_cooldown"
            # bot.send_message(
            #     message.chat.id, "⏳ Шаг 3: Введите **Кулдаун (сек/польз.)**:"
            # )
            # Пропускаем шаг кулдауна, сразу переходим к лимиту
            user_states[uid]["step"] = "wait_limit"
            bot.send_message(
                message.chat.id, "📈 Шаг 3: Введите **Лимит ответов/мин**:"
            )
        except ValueError:
            bot.send_message(message.chat.id, "❌ Некорректное число. Введите задержку в секундах (например, 1.5):")

    # elif step == "wait_cooldown":
    #     try:
    #         cooldown = int(message.text.strip())
    #         if cooldown < 0:
    #             bot.send_message(message.chat.id, "❌ Кулдаун не может быть отрицательным. Введите корректное значение:")
    #             return
    #         bots_storage[uid]["cooldown"] = cooldown
    #         user_states[uid]["step"] = "wait_limit"
    #         bot.send_message(
    #             message.chat.id, "📈 Шаг 4: Введите **Лимит ответов/мин**:"
    #         )
    #         return
    #     except ValueError:
    #         bot.send_message(message.chat.id, "❌ Некорректное число. Введите кулдаун в секундах (целое число):")

    elif step == "wait_limit":
        try:
            limit = int(message.text.strip())
            if limit < 0:
                bot.send_message(message.chat.id, "❌ Лимит не может быть отрицательным. Введите корректное значение:")
                return
            bots_storage[uid]["max_answers_per_min"] = limit
            del user_states[uid]
            save_config(bots_storage)

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Да", callback_data="mention_yes"),
                types.InlineKeyboardButton("❌ Нет", callback_data="mention_no"),
            )
            bot.send_message(
                message.chat.id,
                "💬 Шаг 4: Реагировать **Только по упоминанию** никнейма?",
                reply_markup=markup,
            )
            return  # Важно! Прерываем выполнение после настройки
        except ValueError:
            bot.send_message(message.chat.id, "❌ Некорректное число. Введите лимит ответов в минуту (целое число):")

    if uid not in bots_storage:
        bot.reply_to(message, "Запустите /start для первичной настройки бота.")
        return

    bot.send_chat_action(message.chat.id, "typing")

    # Убран вызов Ollama - бот только для настроек, не отвечает на сообщения в Telegram
    # try:
    #     response = ollama.chat(
    #         model=OLLAMA_MODEL,
    #         messages=[
    #             {
    #                 "role": "system",
    #                 "content": "Ты — живой, общительный ИИ собеседник в Telegram. Отвечай дружелюбно, как человек, пиши коротко и поддерживай любую беседу.",
    #             },
    #             {"role": "user", "content": message.text},
    #         ],
    #     )
    #     bot.reply_to(message, response["message"]["content"])
    #     log(f"Ollama ответ отправлен пользователю {uid}")
    # except Exception as e:
    #     log(f"Ошибка Ollama для пользователя {uid}: {e}")
    #     bot.reply_to(
    #         message,
    #         f"❌ Не удалось подключиться к нейросети Ollama на сервере: {e}",
    #     )


@bot.callback_query_handler(func=lambda call: call.data.startswith("game_"))
def save_game(call):
    uid = str(call.from_user.id)
    game_name = call.data.replace("game_", "")
    bots_storage[uid]["game"] = (
        "Выключен" if game_name == "Выключить игры" else game_name
    )
    save_config(bots_storage)
    log(f"Игровой режим изменен на {bots_storage[uid]['game']} для пользователя {uid}")
    bot.edit_message_reply_markup(
        call.message.chat.id, call.message.message_id, reply_markup=None
    )
    bot.send_message(
        call.message.chat.id,
        f"🎲 Игровой режим изменен на: `{bots_storage[uid]['game']}`",
        parse_mode="Markdown",
    )


if __name__ == "__main__":
    log("Бот-Менеджер успешно запущен и слушает команды!")
    bot.infinity_polling()
