#!/usr/bin/env python3
"""
Chatovod Bot Client
Supports both guest login and registered account login,
and auto-responding using a weights file.

Usage (guest):
  python3 bot.py <chat_url> <bot_nick> <pid_file> <log_file>

Usage (registered account):
  python3 bot.py <chat_url> <bot_nick> <pid_file> <log_file> --email EMAIL --password PASS

Optional:
  --weights PATH   Path to weights .txt file with response rules
"""
import sys, os, time, re, json, signal, random, ssl, argparse, warnings
import requests
import urllib.parse
from requests.adapters import HTTPAdapter
from urllib.parse import urljoin, urlparse

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass


class _TLSAdapter(HTTPAdapter):
    """Forces TLS 1.2+ with relaxed cipher suite to work through Replit's network."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT')
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = ctx
        super().init_poolmanager(*args, **kwargs)

LISTEN_TIMEOUT = 90
RETRY_DELAY    = 3
MAX_ERRORS     = 10

log_path = None


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if log_path:
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(line + "\n")
        except Exception:
            pass


def get_ajax_base(chat_url: str) -> str:
    p = urlparse(chat_url)
    return f"{p.scheme}://{p.netloc}/ajax/"


# ── Weights / auto-reply ──────────────────────────────────────────────────────

def load_weights(weights_path: str) -> list:
    """
    Load weights file and return list of (triggers_list, response) tuples.
    Format:  trigger1|trigger2|... => response text
    Lines starting with # are comments, empty lines are skipped.
    """
    if not weights_path or not os.path.isfile(weights_path):
        return []
    rules = []
    try:
        with open(weights_path, encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=>' not in line:
                    continue
                left, _, right = line.partition('=>')
                triggers = [t.strip().lower() for t in left.split('|') if t.strip()]
                response = right.strip()
                if triggers and response:
                    rules.append((triggers, response))
    except Exception as e:
        log(f"Ошибка загрузки весов: {e}")
    log(f"Загружено {len(rules)} правил автоответа из весов.")
    return rules


def load_numeric_weights(weights_path: str) -> list[tuple[str, int]]:
    """Load numeric weights file and return list of (phrase, weight).

    Format: phrase=10
    - Comments (#) and empty lines are skipped
    - Weight must be integer > 0
    """
    if not weights_path or not os.path.isfile(weights_path):
        return []
    items: list[tuple[str, int]] = []
    try:
        with open(weights_path, encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=>' in line:
                    continue
                if '=' not in line:
                    continue
                left, _, right = line.partition('=')
                phrase = left.strip()
                w_raw = right.strip()
                if not phrase or not w_raw:
                    continue
                try:
                    w = int(w_raw)
                except ValueError:
                    continue
                if w <= 0:
                    continue
                items.append((phrase, w))
    except Exception as e:
        log(f"Ошибка загрузки числовых весов: {e}")
    log(f"Загружено {len(items)} фраз(ы) из числовых весов.")
    return items


def detect_weights_format(weights_path: str) -> str:
    """Detect weights file format.

    Returns:
      - 'rules'   for trigger => response
      - 'numeric' for phrase=weight
    """
    try:
        with open(weights_path, encoding='utf-8') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=>' in line:
                    return 'rules'
                if '=' in line:
                    left, _, right = line.partition('=')
                    if left.strip() and right.strip().isdigit():
                        return 'numeric'
    except Exception:
        return 'rules'
    return 'rules'


def pick_numeric_reply(items: list[tuple[str, int]]) -> str | None:
    if not items:
        return None
    total = sum(w for _, w in items)
    if total <= 0:
        return None
    r = random.randint(1, total)
    acc = 0
    for phrase, w in items:
        acc += w
        if r <= acc:
            return phrase
    return items[-1][0]


BUILTIN_REPLIES: dict[str, list[str]] = {
    'привет|хай|хей|hi |hello|ку |даров|здорово|приветик|хелло|хеллоу|хола': [
        'Привет! Как настроение?', 'Хей! Рад видеть 😊', 'О, привет! Что нового?',
        'Привет-привет! Заходи 😄', 'Ку! Как жизнь?', 'Привет! Всё ок?',
        'Здорово! Как ты?', 'Ого, привет! Давно не виделись!', 'Хай! Откуда?',
        'Приветствую! Чем занимаешься?', 'О, живой! Привет 😁', 'Хей-хей! Рад тебя видеть!',
        'Привет! Что за день сегодня, а?', 'Хай! Как настроение у тебя?',
        'Ну привет! Долго же тебя не было.', 'Привет! Заходи, не стесняйся.',
        'О! Привет! Как ты сегодня?', 'Привет дружище!', 'Хей! Всё хорошо?',
        'Приветик! Как дела-то?',
    ],
    'здравствуй|здравствуйте|доброго времени|добрый день|добрый': [
        'Здравствуйте! Рад вас видеть.', 'Добрый день! Как вы?', 'Здравствуйте, заходите!',
        'Добрый! Чем могу помочь?', 'Привет! Рад вас тут видеть.', 'Здравствуй! Как дела?',
        'Добрый день! Как настроение?', 'Приветствую! Всё нормально?', 'Здравствуйте! Как сегодня?',
        'Добрый! Как жизнь?', 'Рад приветствовать! Всё хорошо?',
    ],
    'как дела|как ты|как жизнь|как сам|как ты там|как делишки|как поживаешь|всё хорошо': [
        'Отлично, спасибо! А у тебя?', 'Нормально, живём 😄 Как ты?',
        'Всё супер! Работаю, отдыхаю. Ты как?', 'Хорошо! Только чай пить не успеваю 😂',
        'Отлично! Что нового у тебя?', 'Бывало лучше, но и хуже тоже. А ты как?',
        'Нормуль! Погода вот немного подводит. Ты как?', 'Живу-поживаю 😊 А ты?',
        'Хорошо! Немного устал, но дела идут. Ты?', 'Отлично! Вот как раз чат захожу.',
        'Всё хорошо, спасибо что спросил! Как у тебя?', 'Да вроде нормально! Как сам?',
        'Хорошо, спасибо! Работаю над кое-чем интересным. Как твои дела?',
        'Супер! Настроение отличное. А ты как?', 'Нормально! Только устал немного. Как ты?',
        'Отлично! Как всегда активен 😄 Как твои?',
    ],
    'что делаешь|чем занимаешься|чем занят|что делаешь сейчас': [
        'Сижу в чате, общаюсь 😄', 'Слежу за обстановкой тут 😊',
        'Да вот, разговариваю с тобой!', 'Скучаю тут без общения, хорошо что зашёл!',
        'Чат держу в порядке, чтоб не скучно было 😄', 'Отдыхаю, разговариваю.',
        'Читаю, думаю... В общем, живу 😄', 'Сижу, жду интересных собеседников!',
        'Да так, мысли думаю 😁', 'Философствую тут в одиночестве 😄',
    ],
    'пока|до свидания|до встречи|удачи|счастливо|ну всё|бывай|досвид': [
        'Пока! Заходи ещё 👋', 'До встречи! Было приятно пообщаться.',
        'Пока-пока! Удачи тебе 😊', 'Бывай! Скоро ещё пообщаемся.',
        'Удачи! Заходи ещё!', 'До свидания! Было приятно.',
        'Счастливо! Не пропадай.', 'Пока! Всего доброго!',
        'Бывай, дружище! До скорого!', 'Удачи тебе! Возвращайся 😄',
    ],
    'спасибо|благодарю|thanks|thank you|мерси': [
        'Пожалуйста! 😊', 'Не за что!', 'Всегда пожалуйста!',
        'Рад помочь!', 'Не стоит благодарности!', 'Пожалуйста, обращайся!',
        'Всегда рад! 😄', 'Не проблема!', 'Ради тебя — всегда! 😊',
    ],
    'скучно|скука|нечего делать|не знаю чем заняться': [
        'Давай поговорим! О чём хочешь?', 'Скучно? Расскажи что-нибудь интересное!',
        'Давай обсудим что-нибудь! Что тебя интересует?',
        'Скучаешь? Можем поиграть! Напиши !игра',
        'Не скучай! Расскажи, как день прошёл?',
        'Скука — двигатель творчества! Придумай что-нибудь 😄',
        'Давай загадку? Или просто поболтаем о жизни?',
    ],
    'помоги|помощь|не понимаю|объясни|подскажи': [
        'Конечно, помогу! Что случилось?', 'Слушаю! В чём вопрос?',
        'Объясни подробнее, постараюсь помочь!', 'Расскажи, что нужно — разберёмся!',
        'Всегда готов помочь! Что именно?', 'Говори, что непонятно — поясню.',
    ],
    'ха|хаха|хахаха|лол|lol|ахаха|смешно': [
        'Ха-ха! 😄', 'Смеёмся!', 'Хорошо тебя прорвало! 😂',
        'Это да, весело!', 'Хахаха, не могу 😁', 'Ну и юморист ты!',
        'Смешно, да 😄', 'Угарно!', 'Вот это поворот! 😂',
    ],
    'окей|окей|ок|ok|хорошо|понял|понятно|ясно': [
        'Отлично!', 'Ок, принято!', 'Хорошо!', 'Договорились!',
        'Ясно-понятно 😄', 'Принято!', 'Окей-доке!', 'Понял тебя!',
    ],
}


def find_reply(rules: list, message: str, used_history: dict | None = None,
               channel: str = 'main') -> str | None:
    """
    Return reply text if message matches any rule, else None.
    Collects ALL matching responses, avoids recently used ones per channel.
    """
    msg_lower = message.lower()
    matching = []
    for triggers, response in rules:
        for trigger in triggers:
            if trigger in msg_lower:
                matching.append(response)
                break  # Don't add same response twice for multiple triggers

    if not matching:
        return None

    if used_history is not None:
        recent = used_history.get(channel, [])
        fresh = [r for r in matching if r not in recent]
        pool = fresh if fresh else matching  # If all used, reset and use all
        if not fresh:
            used_history[channel] = []
    else:
        pool = matching

    reply = random.choice(pool)

    if used_history is not None:
        if channel not in used_history:
            used_history[channel] = []
        used_history[channel].append(reply)
        if len(used_history[channel]) > 60:
            used_history[channel] = used_history[channel][-60:]

    return reply


def find_builtin_reply(message: str, nick: str, used_history: dict | None = None,
                       channel: str = 'main') -> str | None:
    """
    Check built-in reply patterns and return a smart varied response.
    Avoids repeating recently used responses per channel.
    """
    msg_lower = message.lower()
    for pattern, responses in BUILTIN_REPLIES.items():
        for keyword in pattern.split('|'):
            kw = keyword.strip()
            if kw and kw in msg_lower:
                if used_history is not None:
                    recent = used_history.get('builtin_' + channel, [])
                    fresh = [r for r in responses if r not in recent]
                    pool = fresh if fresh else responses
                    if not fresh:
                        used_history['builtin_' + channel] = []
                else:
                    pool = responses
                reply = random.choice(pool)
                if used_history is not None:
                    key = 'builtin_' + channel
                    if key not in used_history:
                        used_history[key] = []
                    used_history[key].append(reply)
                    if len(used_history[key]) > 40:
                        used_history[key] = used_history[key][-40:]
                return reply
    return None


def send_message(session: requests.Session, ajax_url: str, cfg: dict, pv: str, text: str,
                 channel: str = 'main') -> str:
    """Send a message to the chat. Returns new pv on success."""
    data = {
        'act':     'send',
        'chat':    cfg['chat_id'],
        'channel': channel,
        'pv':      pv,
        'msg':     text,
        'csrf':    cfg['csrf_key'],
    }
    try:
        resp = session.post(ajax_url, data=data, timeout=10)
        if resp.ok:
            try:
                result = resp.json()
                if result.get('status') == 'ok':
                    return pv
                log(f"send_message ответ: {json.dumps(result, ensure_ascii=False)[:200]}")
            except Exception:
                log(f"send_message не JSON: {resp.text[:200]}")
        else:
            log(f"send_message HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log(f"Ошибка отправки сообщения: {e}")
    return pv


# ── Chat page scraping ────────────────────────────────────────────────────────

def scrape_chat_config(session: requests.Session, chat_url: str, text: str = None) -> dict:
    if text is None:
        resp = session.get(chat_url, timeout=15)
        resp.raise_for_status()
        text = resp.text

    patterns = {
        'chat_id': [
            r'opts\.chatId\s*=\s*(\d+)',
            r'"chatId"\s*:\s*(\d+)',
            r"'chatId'\s*:\s*(\d+)",
            r'chatId\s*[:=]\s*["\']?(\d+)["\']?',
        ],
        'csrf_key': [
            r'opts\.csrfKey\s*=\s*"([^"]+)"',
            r'"csrfKey"\s*:\s*"([^"]+)"',
            r"'csrfKey'\s*:\s*'([^']+)'",
            r'csrfKey\s*[:=]\s*["\']([^"\']+)["\']',
        ],
        'account_id': [
            r'opts\.accountId\s*=\s*(\d+)',
            r'"accountId"\s*:\s*(\d+)',
            r"'accountId'\s*:\s*(\d+)",
        ],
        'expires': [
            r'opts\.expires\s*=\s*(\d+)',
            r'"expires"\s*:\s*(\d+)',
            r"'expires'\s*:\s*(\d+)",
        ],
        'key': [
            r'opts\.key\s*=\s*"([^"]+)"',
            r'"key"\s*:\s*"([^"]+)"',
            r"'key'\s*:\s*'([^']+)'",
        ],
    }

    values = {}
    for field, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, text)
            if m:
                values[field] = m.group(1)
                break

    if not values.get('chat_id') or not values.get('csrf_key'):
        jsm = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', text, flags=re.S)
        if jsm:
            blob = jsm.group(1)
            for field, keys in {
                'chat_id': ['chatId', 'chat_id', 'id'],
                'csrf_key': ['csrfKey', 'csrf_key', 'csrf'],
                'account_id': ['accountId', 'account_id'],
                'expires': ['expires'],
                'key': ['key'],
            }.items():
                if values.get(field):
                    continue
                for k in keys:
                    m = re.search(rf'"{re.escape(k)}"\s*:\s*"?([^",}}]+)"?', blob)
                    if m:
                        values[field] = m.group(1)
                        break

    if not values.get('chat_id') or not values.get('csrf_key'):
        snippet = re.sub(r'\s+', ' ', text[:1800])
        log("Не удалось распознать конфиг чата. Фрагмент HTML: " + snippet)
        raise RuntimeError("Не удалось найти chatId или csrfKey на странице чата. Проверьте адрес чата.")

    return {
        'chat_id':   values.get('chat_id'),
        'csrf_key':  values.get('csrf_key'),
        'account_id': values.get('account_id'),
        'expires':    values.get('expires'),
        'key':        values.get('key'),
    }


def _get_auth_base(chat_url: str) -> str:
    """
    Determine the Chatovod auth base URL from the chat URL.
    Supports chatovod.ru and any subdomain of it.
    Example:
      https://chat-ai.chatovod.ru/    -> https://chatovod.ru
    """
    p = urlparse(chat_url)
    host = p.hostname or ''
    parts = host.split('.')
    if len(parts) >= 2:
        root = '.'.join(parts[-2:])  # e.g. chatovod.ru
    else:
        root = host
    return f"{p.scheme}://{root}"


def _validate_chat_url(chat_url: str):
    """Raises ValueError if chat_url is not on chatovod.ru domain."""
    p = urlparse(chat_url)
    host = p.hostname or ''
    if not (host == 'chatovod.ru' or host.endswith('.chatovod.ru')):
        raise ValueError(
            f"Неподдерживаемый домен: {host}. "
            "Принимаются только адреса на chatovod.ru (например https://chat-ai.chatovod.ru/)."
        )


def account_login(session: requests.Session, chat_url: str, email: str, password: str) -> dict:
    """
    Logs into Chatovod (chatovod.ru) with email+password,
    follows the redirect to the chat, and returns the chat config dict.
    The auth domain is auto-detected from the chat URL.
    """
    auth_base  = _get_auth_base(chat_url)
    chat_base  = chat_url.rstrip('/').split('?')[0] + '/'
    return_to  = chat_base + '?autologin=1'
    login_urls = [
        auth_base + '/account/login/?return_to=' + urllib.parse.quote(return_to, safe=''),
        auth_base + '/login/?return_to=' + urllib.parse.quote(return_to, safe=''),
    ]

    r = None
    login_url = ''
    for candidate in login_urls:
        log(f"Загружаю страницу входа ({candidate})...")
        try:
            r = session.get(candidate, timeout=15)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            login_url = candidate
            break
        except requests.RequestException:
            continue
    if not r or not login_url:
        raise RuntimeError(f"Не удалось открыть страницу входа на {auth_base}.")

    fkey_m = re.search(r'name="fkey"\s+value="([^"]+)"', r.text)
    if not fkey_m:
        fkey_m = re.search(r'"fkey"\s*:\s*"([^"]+)"', r.text)
    if not fkey_m:
        raise RuntimeError(f"Не удалось получить fkey со страницы входа {auth_base}. Проверьте адрес чата.")
    fkey = fkey_m.group(1)

    log(f"Выполняю вход в аккаунт ({email}) на {auth_base}...")
    r2 = session.post(login_url, data={
        'fact':            'loginpartner',
        'fkey':            fkey,
        'partnerEmail':    email,
        'partnerPassword': password,
    }, timeout=15, allow_redirects=False)

    if r2.status_code not in (301, 302, 303):
        if r2.headers.get('Content-Type', '').startswith('application/json'):
            try:
                err = r2.json()
                raise RuntimeError(f"Ошибка входа: {err.get('message') or err}")
            except Exception as e:
                raise RuntimeError(str(e))
        raise RuntimeError(f"Неожиданный ответ при входе: HTTP {r2.status_code}. Проверьте email и пароль.")

    redirect_url = r2.headers.get('Location', '')
    if not redirect_url:
        redirect_url = return_to
    elif redirect_url.startswith('/'):
        redirect_url = auth_base + redirect_url

    log(f"Перехожу по адресу чата после авторизации: {redirect_url[:80]}...")
    r3 = session.get(redirect_url, timeout=15, allow_redirects=True)
    r3.raise_for_status()

    cfg = scrape_chat_config(session, chat_url, text=r3.text)

    if not cfg.get('account_id') or cfg['account_id'] == '0':
        raise RuntimeError("Вход выполнен, но чат не распознал авторизацию. Проверьте email/пароль и адрес чата.")

    log(f"Аккаунт авторизован: accountId={cfg['account_id']}, expires={cfg.get('expires')}")
    return cfg


def login_guest(session: requests.Session, ajax_url: str, cfg: dict, nick: str) -> str:
    """Guest login — returns pv value on success."""
    data = {
        'act':  'login',
        'chat': cfg['chat_id'],
        'msg':  nick,
        'pv':   '0',
        'c':    '',
        'bind': '0',
        'csrf': cfg['csrf_key'],
    }
    resp = session.post(ajax_url, data=data, timeout=15)
    resp.raise_for_status()
    return _parse_login_result(resp, nick)


def login_account(session: requests.Session, ajax_url: str, cfg: dict, nick: str) -> str:
    """Registered account login — returns pv value on success."""
    if not cfg.get('account_id') or not cfg.get('key') or not cfg.get('expires'):
        raise RuntimeError("Не получены данные аккаунта для входа (accountId/key/expires).")

    data = {
        'act':     'login',
        'chat':    cfg['chat_id'],
        'msg':     nick,
        'pv':      '0',
        'c':       '',
        'bind':    '0',
        'csrf':    cfg['csrf_key'],
        'id':      cfg['account_id'],
        'key':     cfg['key'],
        'expires': cfg['expires'],
    }
    resp = session.post(ajax_url, data=data, timeout=15)
    resp.raise_for_status()
    return _parse_login_result(resp, nick)


def _parse_login_result(resp, nick: str) -> str:
    try:
        result = resp.json()
    except Exception:
        raise RuntimeError(f"Неверный ответ от сервера: {resp.text[:200]}")

    status = result.get('status', '')
    log(f"Ответ на вход: {result}")

    if status == 'alreadyinchat':
        raise RuntimeError("Этот ник уже используется в чате. Выберите другое имя бота.")
    if status == 'needpassword':
        raise RuntimeError("Чат защищён паролем — бот не может войти.")
    if status == 'badcaptcha':
        raise RuntimeError("Чат требует капчу — автоматический вход невозможен.")
    if status == 'onlyRegistered':
        raise RuntimeError("В чат допускаются только зарегистрированные пользователи.")
    if status == 'onlyActivated':
        raise RuntimeError("Ник должен быть активирован администратором чата.")
    if status not in ('ok', '', None) and status:
        log(f"Неизвестный статус входа: {status} — продолжаем")

    return str(result.get('pv', '0'))


# ── Listen loop ───────────────────────────────────────────────────────────────

def _extract_messages(data, my_nick: str) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """
    Extract (sender_nick, message_text, channel) pairs from Chatovod listen response
    and presence events (event_type, nick, channel) for enter/leave.

    Chatovod format:
      {"messages": [{"text": "hi", "from": "User", "fromid": 123, "t": 1234567890, "type": "event"|absent, ...}]}

    Rules:
     - Skip events that have type "event", "login", "logout" (system notifications)
     - Skip messages from the bot itself (by nick or empty from)
     - Use field "text" for message, "from" for sender
    """
    messages: list[tuple[str, str, str]] = []
    presence: list[tuple[str, str, str]] = []

    if not isinstance(data, dict):
        return messages, presence

    # Chatovod puts messages in different keys depending on chat version
    events = (
        data.get('messages')
        or data.get('events')
        or data.get('e')
        or data.get('items')
        or data.get('list')
        or []
    )

    if not isinstance(events, list):
        return messages, presence

    SYSTEM_TYPES = {'event', 'login', 'logout', 'enter', 'leave', 'kick', 'ban', 'topic', 'status'}

    for evt in events:
        if not isinstance(evt, dict):
            continue

        # "type" field is a string like "event", "login"; absent for regular chat messages
        etype = str(evt.get('type', '')).lower()
        if etype in ('enter', 'leave'):
            sender = str(
                evt.get('from')
                or evt.get('nick')
                or evt.get('user')
                or evt.get('name')
                or evt.get('author')
                or ''
            ).strip()
            if sender and sender.lower() != my_nick.lower():
                channel = str(evt.get('channel') or 'main')
                presence.append((etype, sender, channel))
            continue
        if etype in SYSTEM_TYPES:
            continue

        # Message text is in "text" field (Chatovod), fallback for other variants
        msg_text = str(
            evt.get('text')
            or evt.get('msg')
            or evt.get('message')
            or evt.get('body')
            or evt.get('html')
            or ''
        ).strip()
        if not msg_text:
            continue

        # Sender is in "from" field (Chatovod), fallback for other variants
        sender = str(
            evt.get('from')
            or evt.get('nick')
            or evt.get('user')
            or evt.get('name')
            or evt.get('author')
            or ''
        ).strip()

        # Skip empty sender (system/bot messages) and own messages
        if not sender or sender.lower() == my_nick.lower():
            continue

        # Channel for reply (default 'main')
        channel = str(evt.get('channel') or 'main')

        messages.append((sender, msg_text, channel))

    return messages, presence


def _pick_presence_phrase(is_enter: bool) -> str:
    if is_enter:
        return random.choice([
            'привет', 'приветик', 'хай', 'о привет', 'добро пожаловать', 'рад видеть',
        ])
    return random.choice([
        'пока', 'до встречи', 'увидимся', 'хорошего дня', 'береги себя',
    ])


# ── Game Engine ───────────────────────────────────────────────────────────────

HANGMAN_WORDS = [
    'программист', 'компьютер', 'интернет', 'клавиатура', 'монитор',
    'алгоритм', 'переменная', 'функция', 'массив', 'программа',
    'процессор', 'операция', 'система', 'разработка', 'команда',
    'сервер', 'данные', 'протокол', 'браузер', 'пароль',
]

QUIZ_QUESTIONS = [
    {'q': 'Сколько планет в Солнечной системе?', 'a': '8'},
    {'q': 'Столица Франции?', 'a': 'париж'},
    {'q': 'Сколько цветов у радуги?', 'a': '7'},
    {'q': 'Химическая формула воды?', 'a': 'h2o'},
    {'q': 'Самая большая планета Солнечной системы?', 'a': 'юпитер'},
    {'q': 'Сколько ног у паука?', 'a': '8'},
    {'q': 'В каком году закончилась Вторая мировая война?', 'a': '1945'},
    {'q': 'Автор «Войны и мира»?', 'a': 'толстой'},
    {'q': 'Сколько букв в русском алфавите?', 'a': '33'},
    {'q': 'Самый большой океан на Земле?', 'a': 'тихий'},
]

TRUE_FALSE_FACTS = [
    {'fact': 'Золото тяжелее серебра.', 'answer': 'правда'},
    {'fact': 'Мухи живут ровно один день.', 'answer': 'ложь'},
    {'fact': 'Земля — третья планета от Солнца.', 'answer': 'правда'},
    {'fact': 'Акулы — млекопитающие.', 'answer': 'ложь'},
    {'fact': 'Вода кипит при 100°C на уровне моря.', 'answer': 'правда'},
    {'fact': 'Страус — самая быстрая птица.', 'answer': 'ложь'},
    {'fact': 'Молния никогда не бьёт дважды в одно место.', 'answer': 'ложь'},
    {'fact': 'Бананы растут на деревьях.', 'answer': 'ложь'},
    {'fact': 'Луна — единственный естественный спутник Земли.', 'answer': 'правда'},
    {'fact': 'Слоны — единственные животные, которые не могут прыгать.', 'answer': 'правда'},
]

WORDS_SEED = [
    'апельсин', 'банан', 'виноград', 'груша', 'дыня',
    'арбуз', 'манго', 'лимон', 'персик', 'слива',
]

GAME_NAMES = {
    'guess':     '🔢 Угадай число',
    'hangman':   '🔤 Виселица',
    'quiz':      '❓ Викторина',
    'words':     '🔗 Слова',
    'truefalse': '✅ Правда или ложь',
}

GAME_ALIASES = {
    'угадай': 'guess', 'число': 'guess', 'guess': 'guess',
    'виселица': 'hangman', 'вис': 'hangman', 'hangman': 'hangman',
    'викторина': 'quiz', 'вик': 'quiz', 'quiz': 'quiz',
    'слова': 'words', 'слово': 'words', 'words': 'words',
    'правда': 'truefalse', 'ложь': 'truefalse', 'пил': 'truefalse', 'truefalse': 'truefalse',
}


def _hangman_display(word: str, guessed: set) -> str:
    return ' '.join(c if c in guessed else '_' for c in word)


def game_start(game_type: str) -> tuple:
    """Start a new game. Returns (state_dict, intro_message)."""
    if game_type == 'guess':
        n = random.randint(1, 100)
        state = {'type': 'guess', 'number': n, 'attempts': 0}
        msg = ('🔢 Угадай число! Я загадал число от 1 до 100. '
               'Пиши число в чат — скажу больше или меньше. Удачи!')
    elif game_type == 'hangman':
        word = random.choice(HANGMAN_WORDS)
        state = {'type': 'hangman', 'word': word, 'guessed': set(), 'errors': 0, 'max_errors': 6}
        display = _hangman_display(word, set())
        msg = f'🔤 Виселица! Слово: {display} ({len(word)} букв). Угадывайте по одной букве. Макс. ошибок: 6.'
    elif game_type == 'quiz':
        qs = QUIZ_QUESTIONS.copy()
        random.shuffle(qs)
        state = {'type': 'quiz', 'questions': qs, 'current': 0, 'scores': {}}
        msg = f'❓ Викторина! {len(qs)} вопросов. Вопрос 1/{len(qs)}: {qs[0]["q"]}'
    elif game_type == 'words':
        start = random.choice(WORDS_SEED).lower()
        state = {'type': 'words', 'last_word': start, 'used': {start}, 'last_letter': start[-1]}
        msg = (f'🔗 Игра «Слова»! Я начинаю: «{start.upper()}». '
               f'Ваше слово должно начинаться на «{start[-1].upper()}».')
    elif game_type == 'truefalse':
        facts = TRUE_FALSE_FACTS.copy()
        random.shuffle(facts)
        state = {'type': 'truefalse', 'facts': facts, 'current': 0, 'scores': {}}
        msg = (f'✅ Правда или ложь! {len(facts)} утверждений. '
               f'№1/{len(facts)}: «{facts[0]["fact"]}» — правда или ложь?')
    else:
        return {}, 'Неизвестный тип игры.'
    return state, msg


def game_handle(state: dict, sender: str, text: str):
    """
    Handle a user message during an active game.
    Returns (new_state, reply_text).
    new_state=None means the game ended. reply_text=None means message was irrelevant.
    """
    gtype = state.get('type')
    txt = text.strip()

    if gtype == 'guess':
        try:
            guess = int(txt)
        except ValueError:
            return state, None
        state['attempts'] += 1
        n = state['number']
        if guess < n:
            return state, f'{sender}, больше! (попытка {state["attempts"]})'
        elif guess > n:
            return state, f'{sender}, меньше! (попытка {state["attempts"]})'
        else:
            return None, (f'🎉 {sender} угадал(а) число {n} за {state["attempts"]} попыток! '
                          f'Игра окончена. Напиши «!старт» для новой игры.')

    elif gtype == 'hangman':
        letter = txt.lower()
        if len(letter) != 1 or not letter.isalpha():
            return state, None
        word = state['word']
        guessed = state['guessed']
        if letter in guessed:
            return state, f'{sender}, буква «{letter}» уже была.'
        guessed.add(letter)
        display = _hangman_display(word, guessed)
        if letter in word:
            if '_' not in display:
                return None, (f'🎉 {sender} угадал(а) слово «{word.upper()}»! '
                              f'Игра окончена. Напиши «!старт» для новой игры.')
            return state, f'✅ «{letter}» есть! {display}  (ошибок: {state["errors"]}/{state["max_errors"]})'
        else:
            state['errors'] += 1
            if state['errors'] >= state['max_errors']:
                return None, (f'💀 Игра окончена! Слово было: «{word.upper()}». '
                              f'Напиши «!старт» для новой игры.')
            lives = state['max_errors'] - state['errors']
            return state, (f'❌ «{letter}» нет. {display}  '
                           f'(ошибок: {state["errors"]}/{state["max_errors"]}, осталось: {lives})')

    elif gtype == 'quiz':
        answer = txt.lower()
        qs = state['questions']
        idx = state['current']
        correct = qs[idx]['a'].lower()
        if answer != correct:
            return state, f'❌ {sender}, неверно! Подумай ещё.'
        state['scores'][sender] = state['scores'].get(sender, 0) + 1
        state['current'] += 1
        if state['current'] >= len(qs):
            scores_str = ', '.join(
                f'{k}: {v}🏆' for k, v in sorted(state['scores'].items(), key=lambda x: -x[1]))
            return None, (f'✅ {sender} ответил(а) верно! Викторина окончена! '
                          f'Итог: {scores_str}. Напиши «!старт» для новой игры.')
        nq = qs[state['current']]
        return state, (f'✅ {sender} правильно (+1🏆)! '
                       f'Вопрос {state["current"]+1}/{len(qs)}: {nq["q"]}')

    elif gtype == 'words':
        word = txt.lower()
        if not word.isalpha() or len(word) < 2:
            return state, None
        if word[0] != state['last_letter']:
            return state, f'❌ {sender}, слово должно начинаться на «{state["last_letter"].upper()}»!'
        if word in state['used']:
            return state, f'❌ {sender}, слово «{word}» уже было!'
        state['used'].add(word)
        state['last_word'] = word
        state['last_letter'] = word[-1]
        return state, f'✅ {sender}: «{word.upper()}». Следующее на «{word[-1].upper()}».'

    elif gtype == 'truefalse':
        answer = txt.lower()
        if answer not in ('правда', 'ложь'):
            return state, None
        facts = state['facts']
        idx = state['current']
        correct = facts[idx]['answer'].lower()
        if answer != correct:
            return state, f'❌ {sender}, неверно! Правильно: «{correct}».'
        state['scores'][sender] = state['scores'].get(sender, 0) + 1
        state['current'] += 1
        if state['current'] >= len(facts):
            scores_str = ', '.join(
                f'{k}: {v}🏆' for k, v in sorted(state['scores'].items(), key=lambda x: -x[1]))
            return None, (f'✅ {sender} правильно! Игра окончена! '
                          f'Итог: {scores_str}. Напиши «!старт» для новой игры.')
        nf = facts[state['current']]
        return state, (f'✅ {sender} правильно (+1🏆)! '
                       f'№{state["current"]+1}/{len(facts)}: «{nf["fact"]}» — правда или ложь?')

    return state, None


# ── Listen loop ───────────────────────────────────────────────────────────────

def check_broadcast(broadcast_file: str, last_broadcast_id: int, session, ajax_url, cfg, pv, nick) -> tuple[int, str]:
    """Check for a new admin broadcast and send it. Returns (new_last_id, new_pv)."""
    if not broadcast_file or not os.path.isfile(broadcast_file):
        return last_broadcast_id, pv
    try:
        with open(broadcast_file, encoding='utf-8') as f:
            data = json.load(f)
        bid = int(data.get('id', 0))
        text = str(data.get('text', '')).strip()
        if bid > last_broadcast_id and text:
            log(f"Рассылка от админа (id={bid}): «{text[:60]}»")
            time.sleep(random.uniform(1.0, 2.5))
            pv = send_message(session, ajax_url, cfg, pv, text)
            return bid, pv
    except Exception as e:
        log(f"Ошибка чтения рассылки: {e}")
    return last_broadcast_id, pv


def listen_loop(
    session: requests.Session,
    ajax_url: str,
    cfg: dict,
    nick: str,
    pv: str,
    rules: list,
    weights_format: str = 'rules',
    numeric_items: list[tuple[str, int]] | None = None,
    delay_min: float = 0.5,
    delay_max: float = 1.5,
    user_cooldown: int = 0,
    rpm: int = 0,
    mention_only: bool = False,
    game_mode: bool = False,
    game_type: str = 'guess',
    broadcast_file: str = '',
    live_chat_presence: bool = False,
):
    errors = 0
    user_last_reply: dict = {}
    user_last_presence: dict = {}
    reply_times: list    = []
    current_game: dict | None = None
    used_replies: dict = {}
    last_broadcast_id: int = 0
    last_broadcast_check: float = 0.0

    if numeric_items is None:
        numeric_items = []

    log(f"Бот «{nick}» присутствует в чате. Поддерживаю соединение...")
    log("Переход в цикл listen...")
    if weights_format == 'numeric' and numeric_items:
        parts = [f"Автоответы активны: {len(numeric_items)} фраз(ы) загружено (числовые веса)"]
        parts.append(f"задержка {delay_min}–{delay_max} с")
        if user_cooldown:  parts.append(f"кулдаун {user_cooldown} с/польз.")
        if rpm:            parts.append(f"лимит {rpm} отв/мин")
        if mention_only:   parts.append("только при упоминании")
        log(". ".join(parts) + ".")
    elif rules:
        parts = [f"Автоответы активны: {len(rules)} правил загружено"]
        parts.append(f"задержка {delay_min}–{delay_max} с")
        if user_cooldown:  parts.append(f"кулдаун {user_cooldown} с/польз.")
        if rpm:            parts.append(f"лимит {rpm} отв/мин")
        if mention_only:   parts.append("только при упоминании")
        log(". ".join(parts) + ".")
    else:
        log("Файл весов не загружен — автоответы выключены.")
    if game_mode:
        log(f"Игровой режим активен: {GAME_NAMES.get(game_type, game_type)}. Команды: !игра, !старт, !стоп.")
    # Инициализация безопасной фоновой очереди сообщений перед началом цикла
    message_queue = []

    while True:
              # Динамическое чтение настроек из JSON-файла, созданного Telegram-ботом
        try:
            safe_name = re.sub(r"\W+", "_", nick)
            config_path = f"./configs/{safe_name}.json"
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f_cfg:
                    live_cfg = json.load(f_cfg)
                    delay_min = float(live_cfg.get("delay_min", delay_min))
                    delay_max = float(live_cfg.get("delay_max", delay_max))
                    user_cooldown = int(live_cfg.get("cooldown", user_cooldown))
                    rpm = int(live_cfg.get("max_answers_per_min", rpm))
                    mention_only = bool(live_cfg.get("only_mention", mention_only))
        except Exception:
            pass
        try:
            params = {
                'act':  'listen',
                'chat': cfg['chat_id'],
                'pv':   pv,
                '_':    str(random.random()),
            }
            resp = session.get(ajax_url, params=params, timeout=LISTEN_TIMEOUT + 5)
            log(f"listen HTTP {resp.status_code}")

            if resp.ok:
                try:
                    data = resp.json()
                    if not data:
                        log("listen пустой ответ")

                    # Update pv
                    if isinstance(data, dict):
                        if 'pv' in data:
                            pv = str(data['pv'])
                        if data.get('status') in ('kick', 'ban', 'logout'):
                            log(f"Бот выгнан из чата: {data.get('status')}")
                            return
                    elif isinstance(data, list):
                        for item in reversed(data):
                            if isinstance(item, dict) and 'pv' in item:
                                pv = str(item['pv'])
                                break
                        for item in data:
                            if isinstance(item, dict) and item.get('status') in ('kick', 'ban', 'logout'):
                                log(f"Бот выгнан из чата: {item.get('status')}")
                                return

                    # Check for admin broadcast every 20 seconds
                    now_bc = time.time()
                    if broadcast_file and (now_bc - last_broadcast_check >= 20):
                        last_broadcast_check = now_bc
                        last_broadcast_id, pv = check_broadcast(
                            broadcast_file, last_broadcast_id, session, ajax_url, cfg, pv, nick)

                    # Handle messages: game commands first, then weights, then builtins
                    extracted, presence = _extract_messages(data, nick)
                    if extracted:
                        log(f"listen messages: {len(extracted)}")

                    # Presence reactions (enter/leave) — only when enabled for live-chat numeric mode
                    if live_chat_presence and weights_format == 'numeric' and numeric_items and presence:
                        now_p = time.time()
                        for etype, who, pch in presence:
                            # anti-spam per user
                            if (now_p - user_last_presence.get((etype, who), 0)) < 45:
                                continue
                            user_last_presence[(etype, who)] = now_p

                            # global rpm limiter shares the same pool
                            reply_times[:] = [t for t in reply_times if now_p - t < 60]
                            if rpm and len(reply_times) >= rpm:
                                continue

                            phrase = _pick_presence_phrase(is_enter=(etype == 'enter'))
                            out = f"{who}, {phrase}!" if etype == 'enter' else f"{who}, {phrase}."
                            log(f"Presence {etype} [{who}]: → «{out[:70]}»")
                            time.sleep(random.uniform(delay_min, delay_max))
                            pv = send_message(session, ajax_url, cfg, pv, out, pch)
                            reply_times.append(time.time())

                    for sender, text, channel in extracted:
                        txt = text.strip()
                        txt_lo = txt.lower()
                        words_split = txt_lo.split()
                        cmd = words_split[0] if words_split else ''
                        replied = False

                        # ── Game commands ──────────────────────────────
                        if game_mode:
                            if cmd == '!игра':
                                if current_game:
                                    gname = GAME_NAMES.get(current_game['type'], current_game['type'])
                                    out = f"🎮 Идёт игра: {gname}. Напиши «!стоп» чтобы завершить."
                                else:
                                    glist = ' | '.join(GAME_NAMES.values())
                                    out = (f"🎮 Игровой режим! Игры: {glist}. "
                                           f"Напиши «!старт» для {GAME_NAMES.get(game_type, game_type)}, "
                                           f"или «!старт <название>».")
                                time.sleep(random.uniform(delay_min, delay_max))
                                pv = send_message(session, ajax_url, cfg, pv, out, channel)
                                replied = True

                            elif cmd == '!стоп':
                                if current_game:
                                    gname = GAME_NAMES.get(current_game['type'], current_game['type'])
                                    current_game = None
                                    out = f"⏹ {sender} завершил(а) игру «{gname}»."
                                else:
                                    out = "Игра не запущена. Напиши «!старт» чтобы начать!"
                                time.sleep(random.uniform(delay_min, delay_max))
                                pv = send_message(session, ajax_url, cfg, pv, out, channel)
                                replied = True

                            elif cmd == '!старт':
                                chosen = game_type
                                if len(words_split) > 1:
                                    chosen = GAME_ALIASES.get(words_split[1], game_type)
                                current_game, intro = game_start(chosen)
                                log(f"Игра «{chosen}» запущена по команде [{sender}]")
                                time.sleep(random.uniform(delay_min, delay_max))
                                pv = send_message(session, ajax_url, cfg, pv, intro, channel)
                                replied = True

                            elif current_game:
                                new_state, game_reply = game_handle(current_game, sender, txt)
                                if game_reply is not None:
                                    current_game = new_state
                                    log(f"Игра ответ [{sender}]: «{game_reply[:60]}»")
                                    time.sleep(random.uniform(delay_min, delay_max))
                                    pv = send_message(session, ajax_url, cfg, pv, game_reply, channel)
                                    replied = True

                        # ── Weights auto-reply (smart, with history) ──────
                        if not replied:
                            if mention_only and nick.lower() not in txt_lo:
                                pass
                            else:
                                reply = None
                                if weights_format == 'numeric' and numeric_items:
                                    reply = pick_numeric_reply(numeric_items)
                                elif rules:
                                    reply = find_reply(rules, txt, used_replies, channel)
                                # Fall back to built-in smart replies if no weights match
                                if not reply:
                                    reply = find_builtin_reply(txt, nick, used_replies, channel)

                                if reply:
                                    now_ts = time.time()
                                    reply_times[:] = [t for t in reply_times if now_ts - t < 60]
                                    if rpm and len(reply_times) >= rpm:
                                        log(f"Лимит {rpm} отв/мин достигнут, пропускаю ответ [{sender}]")
                                    elif user_cooldown and (now_ts - user_last_reply.get(sender, 0)) < user_cooldown:
                                        remaining = int(user_cooldown - (now_ts - user_last_reply.get(sender, 0)))
                                        log(f"Кулдаун для [{sender}]: ещё {remaining} с")
                                    else:
                                        reply_with_mention = f"{sender}, {reply}"
                                        log(f"Автоответ [{sender}]: «{txt[:50]}» → «{reply_with_mention[:70]}»")
                                        time.sleep(random.uniform(delay_min, delay_max))
                                        pv = send_message(session, ajax_url, cfg, pv, reply_with_mention, channel)
                                        reply_times.append(time.time())
                                        user_last_reply[sender] = time.time()

                except Exception as ex:
                    log(f"Ошибка обработки: {ex}")
                errors = 0
            else:
                errors += 1
                log(f"Ошибка listen HTTP {resp.status_code} (попытка {errors}/{MAX_ERRORS})")
                if errors >= MAX_ERRORS:
                    raise RuntimeError(f"Слишком много ошибок подключения ({MAX_ERRORS})")
                time.sleep(RETRY_DELAY)

        except requests.exceptions.Timeout:
            errors = 0
            continue
        except requests.exceptions.ConnectionError as e:
            errors += 1
            log(f"Ошибка соединения: {e} (попытка {errors}/{MAX_ERRORS})")
            if errors >= MAX_ERRORS:
                raise
            time.sleep(RETRY_DELAY)


def run_bot(
    chat_url: str,
    nick: str,
    email: str = None,
    password: str = None,
    weights_path: str = None,
    delay_min: float = 0.5,
    delay_max: float = 1.5,
    user_cooldown: int = 0,
    rpm: int = 0,
    mention_only: bool = False,
    game_mode: bool = False,
    game_type: str = 'guess',
    broadcast_file: str = '',
    live_chat_presence: bool = False,
):
    session = requests.Session()
    session.mount('https://', _TLSAdapter())
    session.verify = False
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            f'Chrome/124.0 Safari/537.36'
        ),
        'Accept-Language': 'ru-RU,ru;q=0.9',
    })

    _validate_chat_url(chat_url)
    rules = []
    numeric_items: list[tuple[str, int]] = []
    weights_format = 'rules'
    if weights_path:
        weights_format = detect_weights_format(weights_path)
        if weights_format == 'numeric':
            numeric_items = load_numeric_weights(weights_path)
        else:
            rules = load_weights(weights_path)
    ajax_url = get_ajax_base(chat_url)

    if email and password:
        # --- Registered account mode ---
        log(f"Режим: вход через аккаунт Chatovod ({email})")
        cfg = account_login(session, chat_url, email, password)
        log(f"chatId={cfg['chat_id']}  csrf={cfg['csrf_key'][:8]}...  accountId={cfg['account_id']}")
        log(f"AJAX endpoint: {ajax_url}")
        log(f"Вхожу в чат как зарегистрированный пользователь «{nick}»...")
        pv = login_account(session, ajax_url, cfg, nick)
    else:
        # --- Guest mode ---
        log(f"Режим: вход как гость")
        log(f"Загружаю страницу чата: {chat_url}")
        cfg = scrape_chat_config(session, chat_url)
        log(f"chatId={cfg['chat_id']}  csrf={cfg['csrf_key'][:8]}...")
        log(f"AJAX endpoint: {ajax_url}")
        log(f"Вхожу в чат с ником «{nick}»...")
        pv = login_guest(session, ajax_url, cfg, nick)

    listen_loop(session, ajax_url, cfg, nick, pv, rules,
                weights_format=weights_format,
                numeric_items=numeric_items,
                delay_min=delay_min, delay_max=delay_max,
                user_cooldown=user_cooldown, rpm=rpm, mention_only=mention_only,
                game_mode=game_mode, game_type=game_type,
                broadcast_file=broadcast_file,
                live_chat_presence=live_chat_presence)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Chatovod Bot Client')
    parser.add_argument('chat_url')
    parser.add_argument('bot_nick')
    parser.add_argument('pid_file')
    parser.add_argument('log_file')
    parser.add_argument('--email',        default=None)
    parser.add_argument('--password',     default=None)
    parser.add_argument('--weights',      default=None,  help='Path to weights .txt file')
    parser.add_argument('--delay-min',    type=float, default=0.5,  help='Min reply delay (sec)')
    parser.add_argument('--delay-max',    type=float, default=1.5,  help='Max reply delay (sec)')
    parser.add_argument('--user-cooldown',type=int,   default=0,    help='Min sec between replies to same user')
    parser.add_argument('--rpm',          type=int,   default=0,    help='Reply per minute limit (0=unlimited)')
    parser.add_argument('--mention-only', action='store_true',      help='Reply only when bot is mentioned')
    parser.add_argument('--game-mode',    action='store_true',      help='Enable game mode')
    parser.add_argument('--game-type',    default='guess',          help='Default game type')
    parser.add_argument('--broadcast-file',default=None,            help='Path to broadcast.json')
    parser.add_argument('--live-chat-presence', action='store_true', help='React to enter/leave events (live chat)')
    args = parser.parse_args()

    log_path = args.log_file

    try:
        with open(args.pid_file, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print(f"Cannot write pid file: {e}")
        sys.exit(1)

    def _cleanup(sig, frame):
        log(f"Бот остановлен (сигнал {sig})")
        try:
            os.remove(args.pid_file)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)

    signal.signal(signal.SIGINT,  _cleanup)

    mode = f"аккаунт {args.email}" if args.email else "гость"
    try:
        log(f"=== Старт бота «{args.bot_nick}» для {args.chat_url} [{mode}] (PID {os.getpid()}) ===")
        run_bot(
            args.chat_url, args.bot_nick,
            args.email, args.password, args.weights,
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            user_cooldown=args.user_cooldown,
            rpm=args.rpm,
            mention_only=args.mention_only,
            game_mode=args.game_mode,
            game_type=args.game_type,
            broadcast_file=args.broadcast_file,
            live_chat_presence=args.live_chat_presence,
        )
        log("Бот завершил работу.")
    except Exception as e:
        log(f"ОШИБКА: {e}")
        try:
            os.remove(args.pid_file)
        except Exception:
            pass
        sys.exit(1)
