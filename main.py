"""
Telegram Selfbot (Userbot) - نسخه فارسی
ساخته‌شده با Telethon برای اجرا روی Replit

نکات مهم امنیتی/قانونی:
- این اسکریپت با اکانت شخصی تلگرام شما وارد می‌شه (نه یک بات جدا از BotFather)
- فقط پیام‌هایی که خودتون (owner) بفرستید به‌عنوان دستور اجرا می‌شن
- استفاده افراطی از دستورات، مخصوصاً ساعت زنده با فاصله خیلی کم، یا اسپم و
  ادعای عضویت انبوه ممکنه باعث محدودیت اکانت توسط تلگرام بشه. مقادیر پیش‌فرض
  رعایت شده تا ریسک این موضوع کم باشه.
- دستورات مدیریتی (kick/ban/promote/demote) رو فقط توی گروه‌هایی که خودتون
  ادمین هستید استفاده کنید.
"""

import os
import re
import ast
import json
import time
import random
import hashlib
import operator
import asyncio
import aiohttp
from io import BytesIO
from urllib.parse import quote as urlquote
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telethon import TelegramClient, events, errors, functions
from telethon.tl.types import InputMediaDice

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PREFIX = os.getenv("PREFIX", ".")
TIMEZONE_OFFSET = float(os.getenv("TIMEZONE_OFFSET", "3.5"))  # پیش‌فرض: تهران (UTC+3:30)
CLOCK_INTERVAL = max(int(os.getenv("CLOCK_INTERVAL", "60")), 30)  # حداقل ۳۰ ثانیه
NOTES_FILE = os.getenv("NOTES_FILE", "notes.json")  # اگه Volume وصل کردی: مثلاً /data/notes.json
AUTOPOST_FILE = os.getenv("AUTOPOST_FILE", "autopost.json")  # اگه Volume وصل کردی: مثلاً /data/autopost.json
AUTOPOST_MIN_INTERVAL_MINUTES = 1  # حداقل فاصله مجاز - برای کاهش ریسک اسپم بهتره کمتر از ۵ نذاری
ASSISTANT_FILE = os.getenv("ASSISTANT_FILE", "assistant.json")  # اگه Volume وصل کردی: مثلاً /data/assistant.json
FONT_STATE_FILE = os.getenv("FONT_STATE_FILE", "font_state.json")  # اگه Volume وصل کردی: مثلاً /data/font_state.json
ASSISTANT_ONLINE_THRESHOLD = int(os.getenv("ASSISTANT_ONLINE_THRESHOLD", "180"))  # ثانیه - آستانه‌ی تشخیص آنلاین‌بودن
ASSISTANT_CHECK_INTERVAL = max(int(os.getenv("ASSISTANT_CHECK_INTERVAL", "30")), 15)  # هر چند ثانیه وضعیت چک بشه
STATS_FILE = os.getenv("STATS_FILE", "stats.json")  # اگه Volume وصل کردی: مثلاً /data/stats.json
STATS_SAVE_INTERVAL = 60  # هر چند ثانیه آمار روی دیسک ذخیره بشه

if SESSION_STRING:
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    client = TelegramClient("selfbot_session", API_ID, API_HASH)

START_TIME = time.time()
SELF_ID = None  # توی main() موقع اتصال پر می‌شه - برای جلوگیری از فراخوانی مکرر get_me()

HTTP_SESSION: aiohttp.ClientSession | None = None  # توی main() ساخته می‌شه، برای همه‌ی درخواست‌های HTTP مشترکه

async def get_http_session() -> aiohttp.ClientSession:
    """یک aiohttp.ClientSession مشترک برمی‌گردونه (اگه هنوز ساخته نشده، می‌سازدش)."""
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = aiohttp.ClientSession()
    return HTTP_SESSION
def load_assistant():
    default = {
        "mode": "mention",
        "text": "سلام 👋 در حال حاضر آنلاین نیستم. پیامتون رو دیدم، به‌محض امکان جواب می‌دم.",
        "delay": 3,
        "include": [],
        "exclude": [],
        "auto_detect": True,  # اگه False باشه، یعنی کاربر دستی قفلش کرده و تشخیص خودکار دست بهش نمی‌زنه
        "manual_enabled": False,  # فقط وقتی auto_detect=False معتبره
    }
    if os.path.exists(ASSISTANT_FILE):
        with open(ASSISTANT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            default.update({k: data.get(k, v) for k, v in default.items()})
    return default


def save_assistant():
    d = os.path.dirname(ASSISTANT_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    payload = {
        "mode": assistant_state["mode"],
        "text": assistant_state["text"],
        "delay": assistant_state["delay"],
        "include": list(assistant_state["include"]),
        "exclude": list(assistant_state["exclude"]),
        "auto_detect": assistant_state["auto_detect"],
        "manual_enabled": assistant_state["enabled"] if not assistant_state["auto_detect"] else False,
    }
    with open(ASSISTANT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


_assistant_loaded = load_assistant()
assistant_state = {
    # اگه auto_detect خاموش باشه، وضعیت اولیه همون چیزیه که کاربر دستی قفل کرده بود؛
    # وگرنه False می‌مونه تا تسک پس‌زمینه‌ی تشخیص آنلاین/آفلاین خودش تعیینش کنه
    "enabled": _assistant_loaded["manual_enabled"] if not _assistant_loaded["auto_detect"] else False,
    "auto_detect": _assistant_loaded["auto_detect"],
    "mode": _assistant_loaded["mode"],
    "text": _assistant_loaded["text"],
    "delay": _assistant_loaded["delay"],
    "include": set(_assistant_loaded["include"]),
    "exclude": set(_assistant_loaded["exclude"]),
    "replied": set(),  # (chat_id, sender_id) که توی این نشست جواب گرفتن
}
clock_state = {"enabled": True, "base_name": None, "style": "default"}


def load_autopost():
    default = {"enabled": False, "interval_minutes": 5, "text": "", "chats": {}}
    if os.path.exists(AUTOPOST_FILE):
        with open(AUTOPOST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            default.update({k: data.get(k, v) for k, v in default.items()})
    return default


def save_autopost():
    d = os.path.dirname(AUTOPOST_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(AUTOPOST_FILE, "w", encoding="utf-8") as f:
        json.dump(autopost_state, f, ensure_ascii=False, indent=2)


autopost_state = load_autopost()
_autopost_next_run = time.time() + max(autopost_state["interval_minutes"], AUTOPOST_MIN_INTERVAL_MINUTES) * 60
_autopost_force_now = False


def _reset_autopost_timer():
    global _autopost_next_run
    _autopost_next_run = time.time() + max(autopost_state["interval_minutes"], AUTOPOST_MIN_INTERVAL_MINUTES) * 60


# ---------------------------------------------------------------------------
# آمار سلف‌بات
# ---------------------------------------------------------------------------

def load_stats():
    default = {
        "commands_total": 0,
        "commands_by_name": {},          # نام فارسی دستور -> تعداد اجرا
        "messages_total": 0,             # همه‌ی پیام‌های دیده‌شده (ورودی+خروجی)
        "autopost_ok": 0,
        "autopost_fail": 0,
        "errors": 0,                     # فقط خطاهای سیستمی/پس‌زمینه، نه خطاهای ورودی کاربر
        "per_chat": {},                  # chat_id (رشته) -> {"messages": n, "commands": n, "title": ...}
    }
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default.update({k: data.get(k, v) for k, v in default.items()})
        except Exception as e:
            print("خطا در خواندن فایل آمار:", e)
    return default


def save_stats():
    d = os.path.dirname(STATS_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(STATS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("خطا در ذخیره‌ی فایل آمار:", e)


STATS = load_stats()


def _chat_stats(chat_id):
    key = str(chat_id)
    return STATS["per_chat"].setdefault(key, {"messages": 0, "commands": 0, "title": None})


def _record_error():
    STATS["errors"] += 1


def _record_message(event):
    STATS["messages_total"] += 1
    _chat_stats(event.chat_id)["messages"] += 1


def _record_command(event, raw_name):
    canonical = ALL_COMMAND_NAMES.get(raw_name)
    if not canonical:
        return  # پیامی که با پیشوند شروع می‌شه ولی دستور واقعی نیست (تایپ اشتباه)
    STATS["commands_total"] += 1
    STATS["commands_by_name"][canonical] = STATS["commands_by_name"].get(canonical, 0) + 1
    chat = _chat_stats(event.chat_id)
    chat["commands"] += 1


# ---------------------------------------------------------------------------
# ابزارهای کمکی
# ---------------------------------------------------------------------------

ALL_COMMAND_NAMES = {}  # نام‌مستعار (فارسی/انگلیسی) -> نام اصلیِ فارسی؛ توسط pat() پر می‌شه، برای آمار استفاده می‌شه


def pat(name, arg=True):
    """
    ساخت الگوی regex برای دستورات خروجی (پیام‌هایی که خودتون می‌فرستید).
    name می‌تونه یک رشته باشه یا لیستی از نام‌های مترادف برای یک دستور (مثلاً
    نام فارسیِ جدید + نام انگلیسیِ قدیمی، برای سازگاری با عادت قبلی). اولین
    عضو لیست به‌عنوان نام اصلی/نمایشی (برای آمار و راهنما) در نظر گرفته می‌شه.
    """
    names = list(name) if isinstance(name, (list, tuple)) else [name]
    canonical = names[0]
    for n in names:
        ALL_COMMAND_NAMES[n] = canonical
    esc = re.escape(PREFIX)
    alt = "|".join(re.escape(n) for n in names)
    if arg:
        return rf"^{esc}(?:{alt})(?:\s+([\s\S]*))?$"
    return rf"^{esc}(?:{alt})$"


def load_notes():
    if os.path.exists(NOTES_FILE):
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_notes(notes):
    notes_dir = os.path.dirname(NOTES_FILE)
    if notes_dir:
        os.makedirs(notes_dir, exist_ok=True)
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)


_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.FloorDiv: operator.floordiv,
}


def safe_eval(expr):
    """ماشین‌حساب امن - فقط عملیات ریاضی ساده، بدون اجرای کد دلخواه"""
    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError("عبارت نامعتبر")
    tree = ast.parse(expr, mode="eval")
    return _eval(tree.body)


# ---------------------------------------------------------------------------
# فونت‌ها و شکل‌های ساعت زنده
# ---------------------------------------------------------------------------

def _to_persian_digits(s):
    return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _to_fullwidth(s):
    # بلاک Fullwidth Forms: با افزودن 0xFEE0 به کاراکترهای ASCII قابل‌چاپ به‌دست میاد
    return "".join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in s)


def _to_monospace_digits(s):
    # Mathematical Monospace Digits: U+1D7F6 تا U+1D7FF
    return "".join(chr(0x1D7F6 + int(c)) if c.isdigit() else c for c in s)


def _to_doublestruck_digits(s):
    # Mathematical Double-Struck Digits: U+1D7D8 تا U+1D7E1
    return "".join(chr(0x1D7D8 + int(c)) if c.isdigit() else c for c in s)


def _to_circled_digits(s):
    def circ(d):
        d = int(d)
        return chr(0x24EA) if d == 0 else chr(0x2460 + d - 1)  # ⓪①②③...
    return "".join(circ(c) if c.isdigit() else c for c in s)


# آیکون ساعت آنالوگ چرخان بر اساس ساعتِ فعلی (۱۲ تا برای رأس ساعت + ۱۲ تا برای نیم‌ساعت)
_CLOCK_ON_HOUR = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]
_CLOCK_HALF_HOUR = ["🕧", "🕜", "🕝", "🕞", "🕟", "🕠", "🕡", "🕢", "🕣", "🕤", "🕥", "🕦"]


def _rotating_clock_icon(hour, minute):
    h12 = hour % 12
    return _CLOCK_HALF_HOUR[h12] if minute >= 30 else _CLOCK_ON_HOUR[h12]


# --- پاک‌سازی پسوند ساعت قدیمی از نام ---
# چون clock_state فقط توی حافظه‌ست، هر بار که سرویس ری‌استارت/ری‌دیپلوی بشه
# (مثلاً روی Railway) نام فعلیِ زنده‌ی پروفایل که از قبل شامل ساعتِ استایل قبلی
# بوده به‌اشتباه به‌عنوان «نام پایه»ی جدید خونده می‌شه. این تابع، مهم نیست خروجی
# کدوم‌یک از ۱۰ استایل باشه، پسوند ساعتی رو از انتهای نام (حتی اگه چندلایه و
# تکرارشده باشه) پاک می‌کنه تا نام پایه‌ی واقعی برگرده.
_DIGIT_CLASS = r"[0-9\u06F0-\u06F9\uFF10-\uFF19\U0001D7D8-\U0001D7E1\U0001D7F6-\U0001D7FF\u2460-\u2468\u24EA]"
_SEP_CLASS = r"[:\uFF1A]"
_ICON_CLASS = r"(?:[\U0001F550-\U0001F567]|\u23F1)\uFE0F?"

_CLOCK_SUFFIX_RE = re.compile(
    r"(?:\s*\|\s*)?(?:"
    rf"{_ICON_CLASS}\s*{_DIGIT_CLASS}{{2}}{_SEP_CLASS}{_DIGIT_CLASS}{{2}}"   # default / animated
    rf"|{_DIGIT_CLASS}{{2}}{_SEP_CLASS}{_DIGIT_CLASS}{{2}}"                  # persian/fullwidth/monospace/doublestruck/circled/minimal
    r"|『[0-9]{2}:[0-9]{2}』"                                                 # brackets
    rf"|{_ICON_CLASS}\s*[0-9]{{2}}•[0-9]{{2}}"                               # dotstyle
    r")\s*$"
)


def _strip_clock_suffix(name):
    prev = None
    while prev != name:
        prev = name
        name = _CLOCK_SUFFIX_RE.sub("", name).rstrip()
    return name


def _style_default(hour, minute):
    return f"🕐 {hour:02d}:{minute:02d}"


def _style_animated(hour, minute):
    return f"{_rotating_clock_icon(hour, minute)} {hour:02d}:{minute:02d}"


def _style_persian(hour, minute):
    return _to_persian_digits(f"{hour:02d}:{minute:02d}")


def _style_fullwidth(hour, minute):
    return _to_fullwidth(f"{hour:02d}:{minute:02d}")


def _style_monospace(hour, minute):
    return _to_monospace_digits(f"{hour:02d}:{minute:02d}")


def _style_doublestruck(hour, minute):
    return _to_doublestruck_digits(f"{hour:02d}:{minute:02d}")


def _style_circled(hour, minute):
    return _to_circled_digits(f"{hour:02d}:{minute:02d}")


def _style_brackets(hour, minute):
    return f"『{hour:02d}:{minute:02d}』"


def _style_dotstyle(hour, minute):
    return f"⏱ {hour:02d}•{minute:02d}"


def _style_minimal(hour, minute):
    return f"{hour:02d}:{minute:02d}"


# ترتیب نمایش در لیست و چرخش با clockstyle next
CLOCK_STYLE_ORDER = [
    "default", "animated", "persian", "fullwidth",
    "monospace", "doublestruck", "circled", "brackets", "dotstyle", "minimal",
]
CLOCK_STYLES = {
    "default": _style_default,
    "animated": _style_animated,
    "persian": _style_persian,
    "fullwidth": _style_fullwidth,
    "monospace": _style_monospace,
    "doublestruck": _style_doublestruck,
    "circled": _style_circled,
    "brackets": _style_brackets,
    "dotstyle": _style_dotstyle,
    "minimal": _style_minimal,
}

_env_clock_style = os.getenv("CLOCK_STYLE", "default")
clock_state["style"] = _env_clock_style if _env_clock_style in CLOCK_STYLES else "default"


# ---------------------------------------------------------------------------
# ۱) عمومی: ping / alive / id / info
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat(["پینگ", "ping"], arg=False)))
async def ping_handler(event):
    start = time.time()
    msg = await event.edit("🏓 Pinging...")
    delta = (time.time() - start) * 1000
    await msg.edit(f"🏓 Pong!\n⏱ {delta:.0f} ms")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["فعال", "alive"], arg=False)))
async def alive_handler(event):
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    text = (
        "🤖 **سلف‌بات فعال است**\n"
        f"⏳ Uptime: `{uptime}`\n"
        f"🔡 Prefix: `{PREFIX}`\n"
        f"🕐 ساعت زنده: {'روشن' if clock_state['enabled'] else 'خاموش'}"
    )
    await event.edit(text)


@client.on(events.NewMessage(outgoing=True, pattern=pat(["آیدی", "id"], arg=False)))
async def id_handler(event):
    text = f"🆔 Chat ID: `{event.chat_id}`\n"
    if event.is_reply:
        reply = await event.get_reply_message()
        text += f"👤 User ID: `{reply.sender_id}`\n"
        text += f"✉️ Message ID: `{reply.id}`"
    await event.edit(text)


@client.on(events.NewMessage(outgoing=True, pattern=pat(["اطلاعات", "info"], arg=False)))
async def info_handler(event):
    if event.is_reply:
        reply = await event.get_reply_message()
        user = await client.get_entity(reply.sender_id)
    else:
        user = await client.get_me()
    text = (
        f"👤 **نام:** {user.first_name or ''} {user.last_name or ''}\n"
        f"🆔 **آیدی:** `{user.id}`\n"
        f"🔗 **یوزرنیم:** @{user.username if user.username else '---'}"
    )
    await event.edit(text)


# ---------------------------------------------------------------------------
# ۲) ابزار: calc / qr / shorten / weather / tr / google
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat(["حساب", "calc"])))
async def calc_handler(event):
    expr = event.pattern_match.group(1)
    if not expr:
        return await event.edit(f"مثال: `{PREFIX}حساب 5*(3+2)`")
    try:
        result = safe_eval(expr)
        await event.edit(f"🧮 `{expr}` = **{result}**")
    except Exception:
        await event.edit("❌ عبارت ریاضی نامعتبره")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["کیوآر", "qr"])))
async def qr_handler(event):
    import qrcode
    text = event.pattern_match.group(1)
    if not text:
        return await event.edit(f"مثال: `{PREFIX}کیوآر https://example.com`")
    img = qrcode.make(text)
    bio = BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    await event.delete()
    await client.send_file(event.chat_id, bio, caption=f"🔳 QR برای: {text}")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["کوتاه", "shorten"])))
async def shorten_handler(event):
    url = event.pattern_match.group(1)
    if not url:
        return await event.edit(f"مثال: `{PREFIX}کوتاه https://example.com/long-link`")
    try:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=10)
        async with session.get("https://is.gd/create.php",
                                params={"format": "simple", "url": url},
                                timeout=timeout) as r:
            text = await r.text()
        await event.edit(f"🔗 لینک کوتاه‌شده:\n{text}")
    except Exception:
        await event.edit("❌ خطا در کوتاه کردن لینک")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["هوا", "weather"])))
async def weather_handler(event):
    city = event.pattern_match.group(1)
    if not city:
        return await event.edit(f"مثال: `{PREFIX}هوا Tehran`")
    try:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=10)
        async with session.get(f"https://wttr.in/{city}?format=%C+%t+%h+%w",
                                timeout=timeout) as r:
            text = await r.text()
        await event.edit(f"🌤 آب‌وهوای {city}:\n{text}")
    except Exception:
        await event.edit("❌ خطا در دریافت آب‌وهوا")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["ترجمه", "tr"])))
async def translate_handler(event):
    args = event.pattern_match.group(1)
    lang, text = None, None
    if args and " " in args:
        lang, text = args.split(" ", 1)
    elif args and event.is_reply:
        lang = args.strip()
        reply = await event.get_reply_message()
        text = reply.raw_text
    if not lang or not text:
        return await event.edit(f"مثال: `{PREFIX}ترجمه en سلام دنیا` یا با ریپلای: `{PREFIX}ترجمه en`")
    try:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=10)
        async with session.get("https://api.mymemory.translated.net/get",
                                params={"q": text, "langpair": f"auto|{lang}"},
                                timeout=timeout) as r:
            data = await r.json(content_type=None)
        translated = data["responseData"]["translatedText"]
        await event.edit(f"🌐 ترجمه ({lang}):\n{translated}")
    except Exception:
        await event.edit("❌ خطا در ترجمه")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["جستجو", "google"])))
async def google_handler(event):
    q = event.pattern_match.group(1)
    if not q:
        return await event.edit(f"مثال: `{PREFIX}جستجو چطور پایتون یاد بگیرم`")
    link = "https://www.google.com/search?q=" + urlquote(q)
    await event.edit(f"🔍 نتایج گوگل برای: {q}\n{link}")


# ---------------------------------------------------------------------------
# ۳) یادداشت‌ها: note / notes / getnote / delnote
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat(["یادداشت", "note"])))
async def note_handler(event):
    args = event.pattern_match.group(1)
    if not args or " " not in args:
        return await event.edit(f"مثال: `{PREFIX}یادداشت keyname متن یادداشت`")
    key, text = args.split(" ", 1)
    notes = load_notes()
    notes[key] = text
    save_notes(notes)
    await event.edit(f"📝 یادداشت `{key}` ذخیره شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["یادداشت‌ها", "notes"], arg=False)))
async def notes_list_handler(event):
    notes = load_notes()
    if not notes:
        return await event.edit("هیچ یادداشتی وجود نداره")
    text = "📒 لیست یادداشت‌ها:\n" + "\n".join(f"• `{k}`" for k in notes)
    await event.edit(text)


@client.on(events.NewMessage(outgoing=True, pattern=pat(["نمایش‌یادداشت", "getnote"])))
async def getnote_handler(event):
    key = event.pattern_match.group(1)
    notes = load_notes()
    if not key or key not in notes:
        return await event.edit("همچین یادداشتی پیدا نشد")
    await event.edit(f"📝 `{key}`:\n{notes[key]}")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["حذف‌یادداشت", "delnote"])))
async def delnote_handler(event):
    key = event.pattern_match.group(1)
    notes = load_notes()
    if not key or key not in notes:
        return await event.edit("همچین یادداشتی پیدا نشد")
    del notes[key]
    save_notes(notes)
    await event.edit(f"🗑 یادداشت `{key}` حذف شد")


# ---------------------------------------------------------------------------
# ۴) مدیریت پیام: del / purge / pin / unpin
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat(["حذف", "del"], arg=False)))
async def del_handler(event):
    if event.is_reply:
        reply = await event.get_reply_message()
        await reply.delete()
    await event.delete()


@client.on(events.NewMessage(outgoing=True, pattern=pat(["پاکسازی", "purge"])))
async def purge_handler(event):
    count_str = event.pattern_match.group(1)
    if event.is_reply:
        reply = await event.get_reply_message()
        ids = []
        async for m in client.iter_messages(event.chat_id, min_id=reply.id - 1, max_id=event.id):
            ids.append(m.id)
        await client.delete_messages(event.chat_id, ids)
    elif count_str and count_str.isdigit():
        n = int(count_str)
        ids = []
        async for m in client.iter_messages(event.chat_id, limit=n + 1):
            ids.append(m.id)
        await client.delete_messages(event.chat_id, ids)
    else:
        await event.edit(f"مثال: `{PREFIX}پاکسازی 10` یا ریپلای روی پیام + `{PREFIX}پاکسازی`")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["سنجاق", "pin"], arg=False)))
async def pin_handler(event):
    if not event.is_reply:
        return await event.edit("روی یک پیام ریپلای کن")
    reply = await event.get_reply_message()
    await client.pin_message(event.chat_id, reply.id)
    await event.edit("📌 پین شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["برداشتن‌سنجاق", "unpin"], arg=False)))
async def unpin_handler(event):
    if not event.is_reply:
        return await event.edit("روی یک پیام ریپلای کن")
    reply = await event.get_reply_message()
    await client.unpin_message(event.chat_id, reply.id)
    await event.edit("📌 آنپین شد")


# ---------------------------------------------------------------------------
# ۵) سرگرمی: write / type / reverse / mock / dice / coin / random / choose / rps /
#    guess / slot / 8ball / love / wyr
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat(["تایپ‌زنده", "write"])))
async def write_handler(event):
    text = event.pattern_match.group(1)
    if not text:
        return await event.edit(f"مثال: `{PREFIX}تایپ‌زنده سلام دنیا`")
    current = ""
    msg = await event.edit("▌")
    for ch in text:
        current += ch
        try:
            await msg.edit(current + "▌")
            await asyncio.sleep(0.05)
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds)
    await msg.edit(current)


@client.on(events.NewMessage(outgoing=True, pattern=pat(["پیش‌تایپ", "type"])))
async def type_handler(event):
    text = event.pattern_match.group(1)
    if not text:
        return await event.edit(f"مثال: `{PREFIX}پیش‌تایپ سلام`")
    await event.delete()
    async with client.action(event.chat_id, "typing"):
        await asyncio.sleep(min(len(text) * 0.05, 5))
    await client.send_message(event.chat_id, text)


@client.on(events.NewMessage(outgoing=True, pattern=pat(["معکوس", "reverse"])))
async def reverse_handler(event):
    text = event.pattern_match.group(1)
    if not text and event.is_reply:
        reply = await event.get_reply_message()
        text = reply.raw_text
    if not text:
        return await event.edit(f"مثال: `{PREFIX}معکوس سلام`")
    await event.edit(text[::-1])


@client.on(events.NewMessage(outgoing=True, pattern=pat(["طنز", "mock"])))
async def mock_handler(event):
    text = event.pattern_match.group(1)
    if not text and event.is_reply:
        reply = await event.get_reply_message()
        text = reply.raw_text
    if not text:
        return await event.edit(f"مثال: `{PREFIX}طنز متن شما`")
    mocked = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))
    await event.edit(mocked)


DICE_MAX_ATTEMPTS = 60  # سقف تلاش - میانگین لازم ۶ باره، این حاشیه‌ی امن کافیه


async def _roll_real_dice(chat_id):
    """
    یه تاس واقعی می‌فرسته. برای اطمینان از خوندن درستِ عدد نتیجه، به‌جای اتکا
    به آبجکتی که مستقیم از send_file برمی‌گرده (که بعضی‌وقت‌ها media توش کامل
    پر نشده)، پیام رو یک‌بار دیگه از خودِ سرور تلگرام می‌خونیم.
    """
    sent = await client.send_file(chat_id, InputMediaDice("🎲"))
    fresh = await client.get_messages(chat_id, ids=sent.id)
    value = getattr(getattr(fresh, "media", None), "value", None)
    return fresh, value


@client.on(events.NewMessage(outgoing=True, pattern=pat(["تاس", "dice"])))
async def dice_handler(event):
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg.isdigit() or not (1 <= int(arg) <= 6):
        return await event.edit(f"مثال: `{PREFIX}تاس 4` (عدد باید بین ۱ تا ۶ باشه)")
    target = int(arg)
    chat_id = event.chat_id
    await event.delete()

    last_value = None
    for _ in range(DICE_MAX_ATTEMPTS):
        try:
            msg, value = await _roll_real_dice(chat_id)
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            continue
        except Exception as e:
            _record_error()
            return await client.send_message(chat_id, f"❌ خطا در ارسال تاس: {e}")

        last_value = value
        if value == target:
            return  # تاس با عدد درست موند، تمام

        try:
            await msg.delete()
        except Exception:
            pass
        await asyncio.sleep(0.5)

    await client.send_message(
        chat_id,
        f"❌ بعد از {DICE_MAX_ATTEMPTS} تلاش نتونستم عدد {target} رو بیارم "
        f"(آخرین عددی که اومد: {last_value})",
    )


@client.on(events.NewMessage(outgoing=True, pattern=pat(["شیرخط", "coin"], arg=False)))
async def coin_handler(event):
    result = random.choice(["🦁 شیر", "✍️ خط"])
    await event.edit(f"🪙 {result}")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["تصادفی", "random"])))
async def random_handler(event):
    arg = (event.pattern_match.group(1) or "").strip()
    nums = arg.split()
    if len(nums) != 2 or not all(n.lstrip("-").isdigit() for n in nums):
        return await event.edit(f"مثال: `{PREFIX}تصادفی 1 100`")
    lo, hi = int(nums[0]), int(nums[1])
    if lo > hi:
        lo, hi = hi, lo
    await event.edit(f"🎯 عدد تصادفی: **{random.randint(lo, hi)}**")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["انتخاب", "choose"])))
async def choose_handler(event):
    arg = event.pattern_match.group(1)
    if not arg:
        return await event.edit(f"مثال: `{PREFIX}انتخاب پیتزا, برگر, سوشی`")
    options = [o.strip() for o in re.split(r",|\|", arg) if o.strip()]
    if len(options) < 2:
        options = [o.strip() for o in arg.split() if o.strip()]
    if len(options) < 2:
        return await event.edit("حداقل ۲ گزینه لازمه (با کاما یا فاصله جداشون کن)")
    await event.edit(f"🎲 انتخاب شد: **{random.choice(options)}**")


_RPS_CHOICES = {
    "سنگ": "🪨", "rock": "🪨",
    "کاغذ": "📄", "paper": "📄",
    "قیچی": "✂️", "scissors": "✂️",
}
_RPS_CANONICAL = {"سنگ": "سنگ", "rock": "سنگ", "کاغذ": "کاغذ", "paper": "کاغذ", "قیچی": "قیچی", "scissors": "قیچی"}
_RPS_BEATS = {"سنگ": "قیچی", "قیچی": "کاغذ", "کاغذ": "سنگ"}


@client.on(events.NewMessage(outgoing=True, pattern=pat(["سنگ‌کاغذقیچی", "rps"])))
async def rps_handler(event):
    arg = (event.pattern_match.group(1) or "").strip().lower()
    if arg not in _RPS_CANONICAL:
        return await event.edit(f"مثال: `{PREFIX}سنگ‌کاغذقیچی سنگ` (یا کاغذ/قیچی)")
    user_choice = _RPS_CANONICAL[arg]
    bot_choice = random.choice(["سنگ", "کاغذ", "قیچی"])
    if user_choice == bot_choice:
        result = "🤝 مساوی شد!"
    elif _RPS_BEATS[user_choice] == bot_choice:
        result = "🎉 بردی!"
    else:
        result = "😅 باختی!"
    await event.edit(
        f"شما: {_RPS_CHOICES[user_choice]} {user_choice}\n"
        f"من: {_RPS_CHOICES[bot_choice]} {bot_choice}\n\n"
        f"{result}"
    )


GUESS_GAMES = {}  # chat_id -> {"target": int, "max": int, "attempts": int} - بازیِ فعالِ هر چت


@client.on(events.NewMessage(outgoing=True, pattern=pat(["حدس", "guess"])))
async def guess_handler(event):
    arg = (event.pattern_match.group(1) or "").strip()
    chat_id = event.chat_id
    parts = arg.split()
    sub = parts[0].lower() if parts else ""

    if not arg or sub in ("شروع", "start"):
        max_n = 100
        if len(parts) > 1 and parts[1].isdigit():
            max_n = max(10, min(int(parts[1]), 1_000_000))
        GUESS_GAMES[chat_id] = {"target": random.randint(1, max_n), "max": max_n, "attempts": 0}
        return await event.edit(
            f"🎯 یه عدد بین ۱ تا {max_n} توی ذهنم انتخاب کردم.\n"
            f"حدس بزن: `{PREFIX}حدس <عدد>` — برای لغو: `{PREFIX}حدس لغو`"
        )

    if sub in ("لغو", "cancel", "stop"):
        if GUESS_GAMES.pop(chat_id, None) is not None:
            return await event.edit("🚫 بازی لغو شد")
        return await event.edit("بازی‌ای در حال اجرا نیست")

    if not arg.lstrip("-").isdigit():
        return await event.edit(f"مثال: اول `{PREFIX}حدس شروع` بعد `{PREFIX}حدس 50`")

    game = GUESS_GAMES.get(chat_id)
    if not game:
        return await event.edit(f"بازی‌ای شروع نشده. اول بزن: `{PREFIX}حدس شروع`")

    guess = int(arg)
    game["attempts"] += 1
    if guess == game["target"]:
        attempts = game["attempts"]
        del GUESS_GAMES[chat_id]
        return await event.edit(f"🎉 درست حدس زدی! عدد **{guess}** بود (با {attempts} تلاش)")
    if not (1 <= guess <= game["max"]):
        game["attempts"] -= 1  # حدسِ خارج از بازه، به‌عنوان تلاش واقعی حساب نشه
        return await event.edit(f"عدد باید بین ۱ تا {game['max']} باشه")
    hint = "بالاتر برو 🔼" if guess < game["target"] else "پایین‌تر بیا 🔽"
    await event.edit(f"❌ نه. {hint} (تلاش شماره {game['attempts']})")


_SLOT_EMOJIS = ["🍒", "🍋", "🍇", "🍉", "⭐", "7️⃣", "🔔"]


@client.on(events.NewMessage(outgoing=True, pattern=pat(["اسلات", "slot"], arg=False)))
async def slot_handler(event):
    reels = [random.choice(_SLOT_EMOJIS) for _ in range(3)]
    result = " | ".join(reels)
    if reels[0] == reels[1] == reels[2]:
        msg = "🎉 جکپات! هر سه یکی شدن!"
    elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        msg = "✨ دوتاش یکی شدن، یه‌کم شانس آوردی!"
    else:
        msg = "😅 این دفعه نه، شانس بعدی!"
    await event.edit(f"🎰 [ {result} ]\n{msg}")


_MAGIC8BALL_ANSWERS = [
    "بله، مطمئنم ✅", "به احتمال زیاد آره", "علائم می‌گن بله",
    "آره، ولی شک نکن که باید تلاش هم بکنی", "قطعاً همینطوره",
    "بعیده", "من که بهش شک دارم", "نه، فکر نکنم", "قطعاً نه ❌",
    "الان نمی‌تونم بگم، دوباره بپرس 🌀", "روی این حساب نکن",
    "آینده مبهمه، بعداً بپرس", "تمرکز کن و دوباره بپرس",
]


@client.on(events.NewMessage(outgoing=True, pattern=pat(["جادوگر", "8ball"])))
async def magic8ball_handler(event):
    q = event.pattern_match.group(1)
    if not q and event.is_reply:
        reply = await event.get_reply_message()
        q = reply.raw_text
    if not q:
        return await event.edit(f"مثال: `{PREFIX}جادوگر فردا هوا خوبه؟`")
    answer = random.choice(_MAGIC8BALL_ANSWERS)
    await event.edit(f"🔮 سوال: {q}\nپاسخ جادوگر: **{answer}**")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["عشق‌سنج", "love"])))
async def love_calc_handler(event):
    arg = event.pattern_match.group(1)
    if not arg:
        return await event.edit(f"مثال: `{PREFIX}عشق‌سنج علی و سارا`")
    names = re.split(r"\s+و\s+|\s*[+&]\s*", arg, maxsplit=1)
    if len(names) != 2 or not all(n.strip() for n in names):
        words = arg.split()
        if len(words) < 2:
            return await event.edit(f"مثال: `{PREFIX}عشق‌سنج علی و سارا`")
        names = [words[0], " ".join(words[1:])]
    a, b = names[0].strip(), names[1].strip()
    # نتیجه بر اساس هش دو اسم محاسبه می‌شه، پس برای یه جفتِ ثابت همیشه یکسانه
    key = "|".join(sorted([a.lower(), b.lower()]))
    percent = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 101
    if percent >= 80:
        note = "عالیه! 💞"
    elif percent >= 50:
        note = "بدک نیست 🙂"
    elif percent >= 20:
        note = "یه‌کم ضعیفه 😅"
    else:
        note = "شاید دوستیِ ساده بهتر باشه 😬"
    filled = percent // 10
    bar = "❤️" * filled + "🤍" * (10 - filled)
    await event.edit(f"💘 {a} + {b}\n{bar}\n**{percent}%** — {note}")


_WYR_PROMPTS = [
    ("همیشه یک ساعت زودتر همه‌جا برسی", "همیشه یک ساعت دیرتر همه‌جا برسی"),
    ("بتونی پرواز کنی", "بتونی نامرئی بشی"),
    ("همیشه گرمت باشه", "همیشه سردت باشه"),
    ("پول زیاد ولی وقت کم داشته باشی", "وقت زیاد ولی پول کم داشته باشی"),
    ("هر روز پیتزا بخوری", "هر روز سوشی بخوری"),
    ("بتونی گذشته رو ببینی", "بتونی آینده رو ببینی"),
    ("توی جنگل زندگی کنی", "توی وسط شهر شلوغ زندگی کنی"),
    ("همیشه حقیقت رو بشنوی، حتی تلخ", "همیشه چیزی که دوست داری رو بشنوی"),
    ("بتونی ذهن بقیه رو بخونی", "بتونی هر زبونی رو بلد باشی"),
    ("هیچ‌وقت خسته نشی", "هیچ‌وقت گرسنه نشی"),
]


@client.on(events.NewMessage(outgoing=True, pattern=pat(["این‌یا‌اون", "wyr"], arg=False)))
async def wyr_handler(event):
    a, b = random.choice(_WYR_PROMPTS)
    await event.edit(f"🤔 **این یا اون؟**\n\n1️⃣ {a}\n\nیا\n\n2️⃣ {b}")


# ---------------------------------------------------------------------------
# ۶) فونت پیام: انگلیسی (یونیکد ریاضی) / فارسی (تزئینی) / ترکیبی
# ---------------------------------------------------------------------------

def _build_latin_map(upper_start, lower_start, digit_start=None, exceptions=None):
    """
    یه نگاشت A-Z/a-z (و اختیاری ۰-۹) به یه بلاک یونیکد پیوسته می‌سازه.
    exceptions برای حروفی که یونیکد به‌جای بلاک اصلی از نمادهای قدیمی‌تر
    استفاده کرده (مثلاً اسکریپت یا فراکتور) به کار می‌ره.
    """
    exceptions = exceptions or {}
    mapping = {}
    for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        mapping[ch] = exceptions.get(ch, chr(upper_start + i))
    for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
        mapping[ch] = exceptions.get(ch, chr(lower_start + i))
    if digit_start is not None:
        for i, ch in enumerate("0123456789"):
            mapping[ch] = chr(digit_start + i)
    return mapping


_BOLD_MAP = _build_latin_map(0x1D400, 0x1D41A, 0x1D7CE)
_ITALIC_MAP = _build_latin_map(0x1D434, 0x1D44E, exceptions={"h": "\u210E"})
_BOLD_ITALIC_MAP = _build_latin_map(0x1D468, 0x1D482)
_SCRIPT_MAP = _build_latin_map(0x1D49C, 0x1D4B6, exceptions={
    "B": "\u212C", "E": "\u2130", "F": "\u2131", "H": "\u210B", "I": "\u2110",
    "L": "\u2112", "M": "\u2133", "R": "\u211B",
    "e": "\u212F", "g": "\u210A", "o": "\u2134",
})
_FRAKTUR_MAP = _build_latin_map(0x1D504, 0x1D51E, exceptions={
    "C": "\u212D", "H": "\u210C", "I": "\u2111", "R": "\u211C", "Z": "\u2128",
})
_SANS_BOLD_MAP = _build_latin_map(0x1D5D4, 0x1D5EE, 0x1D7EC)
_MONO_MAP = _build_latin_map(0x1D670, 0x1D68A, 0x1D7F6)
_CIRCLED_MAP = _build_latin_map(0x24B6, 0x24D0)

_SMALLCAPS_MAP = {
    "a": "\u1D00", "b": "\u0299", "c": "\u1D04", "d": "\u1D05", "e": "\u1D07",
    "f": "\uA730", "g": "\u0262", "h": "\u029C", "i": "\u026A", "j": "\u1D0A",
    "k": "\u1D0B", "l": "\u029F", "m": "\u1D0D", "n": "\u0274", "o": "\u1D0F",
    "p": "\u1D18", "q": "q", "r": "\u0280", "s": "s", "t": "\u1D1B",
    "u": "\u1D1C", "v": "\u1D20", "w": "\u1D21", "x": "x", "y": "\u028F", "z": "\u1D22",
}
for _c in list(_SMALLCAPS_MAP.keys()):
    _SMALLCAPS_MAP[_c.upper()] = _SMALLCAPS_MAP[_c]

_FLIP_MAP = {
    "a": "ɐ", "b": "q", "c": "ɔ", "d": "p", "e": "ǝ", "f": "ɟ", "g": "ƃ",
    "h": "ɥ", "i": "ᴉ", "j": "ɾ", "k": "ʞ", "l": "l", "m": "ɯ", "n": "u",
    "o": "o", "p": "d", "q": "b", "r": "ɹ", "s": "s", "t": "ʇ", "u": "n",
    "v": "ʌ", "w": "ʍ", "x": "x", "y": "ʎ", "z": "z",
    "0": "0", "1": "Ɩ", "2": "ᄅ", "3": "Ɛ", "4": "ㄣ", "5": "ϛ", "6": "9",
    "7": "ㄥ", "8": "8", "9": "6", "?": "¿", "!": "¡",
}
for _c in list(_FLIP_MAP.keys()):
    if _c.isalpha():
        _FLIP_MAP[_c.upper()] = _FLIP_MAP[_c]


def _apply_map(text, mapping):
    return "".join(mapping.get(ch, ch) for ch in text)


def _flip_text(text):
    return "".join(_FLIP_MAP.get(ch, ch) for ch in text)[::-1]


def _persian_spaced(t):
    return " ".join(list(t))


# حروفی که فقط به حرفِ قبلی می‌چسبن، نه به حرفِ بعدی - نباید بعدشون تطویل بذاریم
_NON_FORWARD_JOIN = set("اآدذرزژو")


def _persian_kashida(t):
    """
    کشیدگی واقعی حروف با تطویل عربی (ـ) - همون تکنیکی که توی چاپ و خوشنویسی
    فارسی/عربی برای کشیده‌کردن کلمات استفاده می‌شه. برخلاف نمادهای تزئینی،
    این یه کاراکتر استاندارد و پشتیبانی‌شده روی همه‌ی گوشی‌هاست.
    """
    chars = list(t)
    out = []
    for i, ch in enumerate(chars):
        out.append(ch)
        is_persian = "\u0600" <= ch <= "\u06FF"
        next_is_persian = i < len(chars) - 1 and "\u0600" <= chars[i + 1] <= "\u06FF"
        if is_persian and ch not in _NON_FORWARD_JOIN and next_is_persian:
            out.append("\u0640")  # ـ تطویل
    return "".join(out)


def _combining_style(t, mark):
    return "".join(ch + mark if ch != " " else ch for ch in t)


def _persian_underline(t):
    return _combining_style(t, "\u0332")  # زیرخط واقعی روی هر حرف


def _persian_strike(t):
    return _combining_style(t, "\u0336")  # خط‌خوردگی واقعی روی هر حرف


_ENGLISH_FONTS = {
    "bold": lambda t: _apply_map(t, _BOLD_MAP),
    "italic": lambda t: _apply_map(t, _ITALIC_MAP),
    "bold_italic": lambda t: _apply_map(t, _BOLD_ITALIC_MAP),
    "script": lambda t: _apply_map(t, _SCRIPT_MAP),
    "fraktur": lambda t: _apply_map(t, _FRAKTUR_MAP),
    "sans_bold": lambda t: _apply_map(t, _SANS_BOLD_MAP),
    "monospace": lambda t: _apply_map(t, _MONO_MAP),
    "smallcaps": lambda t: _apply_map(t, _SMALLCAPS_MAP),
    "circled": lambda t: _apply_map(t, _CIRCLED_MAP),
    "upside_down": _flip_text,
}

_PERSIAN_FONTS = {
    "fa_kashida": _persian_kashida,   # کشیدگی واقعی با تطویل - رندر درست همه‌جا
    "fa_underline": _persian_underline,  # زیرخط واقعی روی تک‌تک حروف
    "fa_strike": _persian_strike,     # خط‌خوردگی واقعی روی تک‌تک حروف
    "fa_spaced": _persian_spaced,
    "fa_stars": lambda t: f"✦ {t} ✦",
    "fa_flowers": lambda t: f"⋆｡°✩ {t} ✩°｡⋆",
    "fa_brackets": lambda t: f"『{t}』",
    "fa_elegant": lambda t: f"⟪ {t} ⟫",
    "fa_ribbon": lambda t: f"⸙ {t} ⸙",
    "fa_diamond": lambda t: f"◈ {t} ◈",
    "fa_wave": lambda t: f"﹋﹋ {t} ﹋﹋",
    "fa_boxed": lambda t: f"【{t}】",
    "fa_hearts": lambda t: f"❀ {t} ❀",
}

_COMBINED_FONTS = {
    "mix_bold": lambda t: f"✦ {_apply_map(t, _BOLD_MAP)} ✦",
    "mix_italic": lambda t: f"『{_apply_map(t, _ITALIC_MAP)}』",
    "mix_script": lambda t: f"⟪ {_apply_map(t, _SCRIPT_MAP)} ⟫",
    "mix_mono": lambda t: f"⌗ {_apply_map(t, _MONO_MAP)} ⌗",
}

FONT_STYLES = {**_ENGLISH_FONTS, **_PERSIAN_FONTS, **_COMBINED_FONTS}


def load_font_state():
    default = {"enabled": False, "style": "bold"}
    if os.path.exists(FONT_STATE_FILE):
        with open(FONT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            default.update({k: data.get(k, v) for k, v in default.items()})
    return default


def save_font_state():
    d = os.path.dirname(FONT_STATE_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(FONT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(font_state, f, ensure_ascii=False, indent=2)


font_state = load_font_state()


@client.on(events.NewMessage(outgoing=True, pattern=pat(["قلم", "font"])))
async def font_handler(event):
    raw = (event.pattern_match.group(1) or "").strip()
    parts = raw.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if not sub or sub in ("فهرست", "list"):
        sample = "Text متن"
        lines = ["🔤 **فونت‌های موجود** (نمونه با «Text متن»):\n", "**انگلیسی:**"]
        for name in _ENGLISH_FONTS:
            lines.append(f"▫️ `{name}` → {FONT_STYLES[name](sample)}")
        lines.append("\n**فارسی:**")
        for name in _PERSIAN_FONTS:
            lines.append(f"▫️ `{name}` → {FONT_STYLES[name](sample)}")
        lines.append("\n**ترکیبی:**")
        for name in _COMBINED_FONTS:
            lines.append(f"▫️ `{name}` → {FONT_STYLES[name](sample)}")
        lines.append(
            f"\nاستفاده‌ی یه‌بار: `{PREFIX}قلم <نام> <متن>` یا ریپلای + `{PREFIX}قلم <نام>`\n"
            f"اعمال خودکار روی همه‌ی پیام‌ها: `{PREFIX}قلم تنظیم <نام>` بعد `{PREFIX}قلم روشن`"
        )
        return await event.edit("\n".join(lines))

    if sub in ("وضعیت", "status"):
        state_fa = "روشن ✅" if font_state["enabled"] else "خاموش ❌"
        return await event.edit(
            f"🔤 **فونت خودکار پیام‌ها**\n\n"
            f"• وضعیت: {state_fa}\n"
            f"• فونت انتخابی: `{font_state['style']}`\n\n"
            f"روشن/خاموش: `{PREFIX}قلم روشن` / `{PREFIX}قلم خاموش`\n"
            f"تغییر فونت: `{PREFIX}قلم تنظیم <نام>`"
        )

    if sub in ("تنظیم", "set"):
        name = rest.strip().lower()
        if name not in FONT_STYLES:
            return await event.edit(f"فونت نامعتبره. برای فهرست: `{PREFIX}قلم فهرست`")
        font_state["style"] = name
        save_font_state()
        return await event.edit(f"✅ فونت پیش‌فرض روی `{name}` تنظیم شد")

    if sub in ("روشن", "on"):
        font_state["enabled"] = True
        save_font_state()
        return await event.edit(
            f"✅ فونت خودکار روشن شد (فونت: `{font_state['style']}`)\n"
            "از الان، هر پیام عادی‌ای که بفرستی (نه دستورها) خودکار با این فونت "
            "ارسال می‌شه. توجه: چون پیام اول واقعی فرستاده می‌شه و بعد ادیت می‌شه، "
            "یه لحظه‌ی خیلی کوتاه متن اصلی قابل‌دیدنه."
        )

    if sub in ("خاموش", "off"):
        font_state["enabled"] = False
        save_font_state()
        return await event.edit("✅ فونت خودکار خاموش شد")

    if sub not in FONT_STYLES:
        return await event.edit(f"فونت نامعتبره. برای فهرست: `{PREFIX}قلم فهرست`")

    text = rest
    if not text and event.is_reply:
        reply = await event.get_reply_message()
        text = reply.raw_text or ""
    if not text:
        return await event.edit(f"مثال: `{PREFIX}قلم {sub} متن شما`")

    await event.edit(FONT_STYLES[sub](text))


@client.on(events.NewMessage(outgoing=True))
async def font_autoapply(event):
    """
    وقتی فونت خودکار روشنه، این هندلر روی *هر* پیام معمولی‌ای که می‌فرستی
    (نه دستورهای ربات که با پیشوند شروع می‌شن) فونت انتخابی رو اعمال می‌کنه.
    """
    if not font_state["enabled"]:
        return
    text = event.raw_text
    if not text or text.startswith(PREFIX):
        return  # دستورهای خودِ ربات رو دست نمی‌زنیم
    style = font_state["style"]
    if style not in FONT_STYLES:
        return
    try:
        await event.edit(FONT_STYLES[style](text))
    except Exception as e:
        _record_error()
        print("خطا در اعمال خودکار فونت:", e)


# ---------------------------------------------------------------------------
# ۷) پروفایل: setbio / setname / setpic / clock
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat(["بیو", "setbio"])))
async def setbio_handler(event):
    bio = event.pattern_match.group(1)
    if not bio:
        return await event.edit(f"مثال: `{PREFIX}بیو بیو جدید`")
    await client(functions.account.UpdateProfileRequest(about=bio))
    await event.edit("✅ بیو بروزرسانی شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["نام", "setname"])))
async def setname_handler(event):
    name = event.pattern_match.group(1)
    if not name:
        return await event.edit(f"مثال: `{PREFIX}نام نام جدید`")
    clock_state["base_name"] = name
    if clock_state["enabled"]:
        await _apply_clock_now()
    else:
        await client(functions.account.UpdateProfileRequest(first_name=name))
    await event.edit("✅ نام پایه بروزرسانی شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["عکس", "setpic"], arg=False)))
async def setpic_handler(event):
    if not event.is_reply:
        return await event.edit("روی یک عکس ریپلای کن")
    reply = await event.get_reply_message()
    if not reply.photo:
        return await event.edit("پیام ریپلای‌شده عکس نیست")
    file_bytes = await client.download_media(reply, file=bytes)
    uploaded = await client.upload_file(file_bytes, file_name="pic.jpg")
    await client(functions.photos.UploadProfilePhotoRequest(file=uploaded))
    await event.edit("✅ عکس پروفایل تغییر کرد")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["ساعت", "clock"])))
async def clock_toggle_handler(event):
    arg = (event.pattern_match.group(1) or "").strip().lower()
    if arg in ("خاموش", "off"):
        clock_state["enabled"] = False
        await event.edit("🕐 ساعت زنده خاموش شد")
    elif arg in ("روشن", "on"):
        clock_state["enabled"] = True
        await event.edit("🕐 ساعت زنده روشن شد (طی چند ثانیه اعمال می‌شه)")
    else:
        await event.edit(f"استفاده: `{PREFIX}ساعت روشن` یا `{PREFIX}ساعت خاموش`")


async def _refresh_base_name():
    """
    نام فعلیِ زنده رو از تلگرام می‌خونه و پسوند ساعت رو ازش پاک می‌کنه. اگه
    کاربر مستقیم توی اپ تلگرام اسمش رو عوض کرده باشه، این تابع همون نام جدید
    رو به‌عنوان نام پایه می‌پذیره - در نتیجه ربات دیگه اسم قدیمی رو روی اسم
    تازه‌ی کاربر بازنویسی نمی‌کنه. فقط توی تسک پس‌زمینه استفاده می‌شه، نه توی
    دستورات فوری مثل setname (تا با نامی که تازه ست کردید تداخل نکنه).
    """
    me = await client.get_me()
    clock_state["base_name"] = _strip_clock_suffix(me.first_name or "")
    return clock_state["base_name"]


async def _apply_clock_now():
    """اعمال فوری استایل/نام روی پروفایل، بدون صبر تا تیک بعدی ساعت"""
    if not clock_state["enabled"]:
        return
    if clock_state["base_name"] is None:
        me = await client.get_me()
        clock_state["base_name"] = _strip_clock_suffix(me.first_name or "")
    now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
    base = clock_state["base_name"][:40]
    clock_part = CLOCK_STYLES[clock_state["style"]](now.hour, now.minute)
    try:
        await client(functions.account.UpdateProfileRequest(first_name=f"{base} | {clock_part}"))
    except Exception as e:
        _record_error()
        print("خطا در اعمال فوری استایل ساعت:", e)


@client.on(events.NewMessage(outgoing=True, pattern=pat(["شکل‌ساعت", "clockstyle"])))
async def clockstyle_handler(event):
    arg = (event.pattern_match.group(1) or "").strip().lower()
    if not arg or arg in ("فهرست", "list"):
        now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        lines = ["🎨 **استایل‌های ساعت زنده:**\n"]
        for name in CLOCK_STYLE_ORDER:
            preview = CLOCK_STYLES[name](now.hour, now.minute)
            marker = "✅" if name == clock_state["style"] else "▫️"
            lines.append(f"{marker} `{name}` → {preview}")
        lines.append(f"\nبرای تغییر: `{PREFIX}شکل‌ساعت <نام>` یا `{PREFIX}شکل‌ساعت بعدی`")
        return await event.edit("\n".join(lines))

    if arg in ("بعدی", "next"):
        idx = CLOCK_STYLE_ORDER.index(clock_state["style"])
        new_style = CLOCK_STYLE_ORDER[(idx + 1) % len(CLOCK_STYLE_ORDER)]
    elif arg in CLOCK_STYLES:
        new_style = arg
    else:
        return await event.edit(f"استایل نامعتبره. برای دیدن فهرست: `{PREFIX}شکل‌ساعت فهرست`")

    clock_state["style"] = new_style
    preview = CLOCK_STYLES[new_style](*(datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)).timetuple()[3:5])
    await event.edit(f"✅ استایل ساعت روی `{new_style}` تنظیم شد\nنمونه: {preview}")
    await _apply_clock_now()


# ---------------------------------------------------------------------------
# ۸) منشی چت: پاسخ خودکار هوشمند با تشخیص آنلاین/آفلاین
# ---------------------------------------------------------------------------

_ASSISTANT_MODE_FA = {
    "auto": "خودکار (همه‌جا)",
    "mention": "فقط با منشن/ریپلای",
    "pm": "فقط پیوی",
    "groups": "فقط گروه‌ها",
}

# ورودیِ کاربر برای «حالت پاسخ» -> کلید داخلیِ همیشگی (auto/mention/pm/groups).
# هم نسخه‌ی فارسی و هم انگلیسیِ قدیمی رو قبول می‌کنه.
_ASSISTANT_MODE_ALIASES = {
    "خودکار": "auto", "auto": "auto",
    "منشن": "mention", "mention": "mention",
    "پیوی": "pm", "pm": "pm",
    "گروه‌ها": "groups", "گروهها": "groups", "groups": "groups",
}


def _assistant_status_text():
    status = "روشن ✅" if assistant_state["enabled"] else "خاموش ❌"
    mode_fa = _ASSISTANT_MODE_FA.get(assistant_state["mode"], assistant_state["mode"])
    if assistant_state["auto_detect"]:
        control_line = f"خودکار (بر اساس آنلاین/آفلاین‌بودنت، هر {ASSISTANT_CHECK_INTERVAL} ثانیه چک می‌شه)"
        footer = (
            f"با `{PREFIX}منشی روشن` یا `{PREFIX}منشی خاموش` می‌تونی دستی قفلش کنی "
            "(از اون به بعد حتی اگه آنلاین/آفلاین بشی، تشخیص خودکار دیگه دست بهش نمی‌زنه)."
        )
    else:
        control_line = "دستی 🔒 (قفل‌شده - تشخیص آنلاین/آفلاین روش تاثیری نداره)"
        footer = f"برای برگردوندن به تشخیص خودکار: `{PREFIX}منشی خودکار`"
    return (
        "🤖 **منشی چت**\n\n"
        f"• وضعیت: {status}\n"
        f"• کنترل: {control_line}\n"
        f"• حالت پاسخ: {mode_fa}\n"
        f"• تأخیر پاسخ: {assistant_state['delay']} ثانیه\n"
        f"• متن: {assistant_state['text'] or '(تنظیم نشده)'}\n"
        f"• چت‌های مستثنی: {len(assistant_state['exclude'])}\n"
        f"• چت‌های همیشه‌فعال: {len(assistant_state['include'])}\n\n"
        f"{footer}"
    )


def _assistant_should_respond(event):
    if event.is_channel and not event.is_group:
        return False  # کانال‌های برادکست رو نادیده بگیر
    chat_id = event.chat_id
    if chat_id in assistant_state["exclude"]:
        return False
    if chat_id in assistant_state["include"]:
        return True
    mode = assistant_state["mode"]
    if mode == "auto":
        return True
    if mode == "pm":
        return event.is_private
    if mode == "groups":
        return event.is_group
    if mode == "mention":
        if event.is_private:
            return True
        return bool(getattr(event.message, "mentioned", False))
    return False


@client.on(events.NewMessage(outgoing=True, pattern=pat(["منشی", "assistant"])))
async def assistant_handler(event):
    raw = (event.pattern_match.group(1) or "").strip()
    parts = raw.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if not sub or sub in ("وضعیت", "status"):
        return await event.edit(_assistant_status_text())

    if sub in ("روشن", "on"):
        assistant_state["enabled"] = True
        assistant_state["auto_detect"] = False  # قفل دستی - تشخیص خودکار دیگه دست بهش نمی‌زنه
        assistant_state["replied"] = set()
        save_assistant()
        return await event.edit(_assistant_status_text())

    if sub in ("خاموش", "off"):
        assistant_state["enabled"] = False
        assistant_state["auto_detect"] = False  # قفل دستی - حتی اگه آفلاین بشی خاموش می‌مونه
        save_assistant()
        return await event.edit(_assistant_status_text())

    if sub in ("خودکار", "auto"):
        assistant_state["auto_detect"] = True
        save_assistant()
        return await event.edit(
            "✅ تشخیص خودکار آنلاین/آفلاین دوباره فعال شد.\n"
            "از این به بعد روشن/خاموش‌بودن منشی خودش بر اساس آنلاین/آفلاین‌بودنت مدیریت می‌شه."
        )

    if sub in ("متن", "text"):
        text = rest
        if not text and event.is_reply:
            reply = await event.get_reply_message()
            text = reply.raw_text or ""
        if not text:
            return await event.edit(f"مثال: `{PREFIX}منشی متن سلام، فعلاً آنلاین نیستم`")
        assistant_state["text"] = text
        save_assistant()
        return await event.edit("✅ متن پاسخ ذخیره شد")

    if sub in ("تأخیر", "تاخیر", "delay"):
        if not rest.strip().isdigit():
            return await event.edit(f"مثال: `{PREFIX}منشی تأخیر 3`")
        assistant_state["delay"] = max(int(rest.strip()), 0)
        save_assistant()
        return await event.edit(f"✅ تأخیر روی {assistant_state['delay']} ثانیه تنظیم شد")

    if sub in ("حالت", "mode"):
        m_raw = rest.strip().lower()
        m = _ASSISTANT_MODE_ALIASES.get(m_raw)
        if not m:
            return await event.edit(f"مثال: `{PREFIX}منشی حالت خودکار` (خودکار/منشن/پیوی/گروه‌ها)")
        assistant_state["mode"] = m
        save_assistant()
        warn = ""
        if m == "auto":
            warn = (
                "\n⚠️ توجه: توی این حالت به همه‌ی پیام‌های هر چتی (حتی بدون تگ/ریپلای) "
                "جواب می‌ده - توی گروه‌های شلوغ ممکنه شبیه اسپم به‌نظر برسه."
            )
        return await event.edit(f"✅ حالت روی `{_ASSISTANT_MODE_FA[m]}` تنظیم شد{warn}")

    if sub in ("مستثنی", "exclude"):
        assistant_state["exclude"].add(event.chat_id)
        assistant_state["include"].discard(event.chat_id)
        save_assistant()
        return await event.edit("🚫 این چت مستثنی شد (منشی اینجا پاسخ نمی‌ده)")

    if sub in ("شامل", "include"):
        assistant_state["include"].add(event.chat_id)
        assistant_state["exclude"].discard(event.chat_id)
        save_assistant()
        return await event.edit("✅ این چت به لیست همیشه‌فعال اضافه شد")

    if sub in ("پاک", "clear"):
        assistant_state["include"].clear()
        assistant_state["exclude"].clear()
        save_assistant()
        return await event.edit("🗑 لیست مستثنی/شامل پاک شد")

    await event.edit(f"دستور نامعتبره. برای وضعیت کامل: `{PREFIX}منشی`")


@client.on(events.NewMessage(incoming=True))
async def assistant_autoreply(event):
    if not assistant_state["enabled"] or not assistant_state["text"]:
        return
    sender_id = event.sender_id
    if sender_id is None or sender_id == SELF_ID:
        return
    if not _assistant_should_respond(event):
        return
    key = (event.chat_id, sender_id)
    if key in assistant_state["replied"]:
        return  # به هر نفر فقط یک‌بار در هر نشست جواب می‌ده، نه هر پیام
    assistant_state["replied"].add(key)
    try:
        delay = assistant_state["delay"]
        if delay > 0:
            async with client.action(event.chat_id, "typing"):
                await asyncio.sleep(delay)
        await event.reply(assistant_state["text"])
    except Exception as e:
        _record_error()
        print("خطا در پاسخ خودکار منشی:", e)


async def assistant_status_watcher():
    """
    هر چند ثانیه یک‌بار (ASSISTANT_CHECK_INTERVAL) لیست سشن‌های فعال اکانت رو
    از تلگرام می‌گیره. اگه سشنی غیر از همین اسکریپت (مثلاً گوشی خودت) به‌تازگی
    فعال بوده باشه، یعنی خودت آنلاینی -> منشی خاموش می‌شه. اگه هیچ سشن دیگه‌ای
    به‌تازگی فعال نبوده -> یعنی آفلاینی -> منشی خودش روشن می‌شه.

    اگه با .assistant on یا .assistant off دستی قفلش کرده باشی (auto_detect
    خاموش)، این تابع اصلاً دست به enabled نمی‌زنه - حتی اگه آفلاین بشی.
    """
    while True:
        if not assistant_state["auto_detect"]:
            await asyncio.sleep(ASSISTANT_CHECK_INTERVAL)
            continue
        try:
            result = await client(functions.account.GetAuthorizationsRequest())
            others = [a for a in result.authorizations if not a.current]
            if others:
                last_active = max(a.date_active for a in others)
                seconds_since = (datetime.now(timezone.utc) - last_active).total_seconds()
                online_elsewhere = seconds_since < ASSISTANT_ONLINE_THRESHOLD
            else:
                online_elsewhere = False  # هیچ سشن دیگه‌ای وصل نیست

            new_enabled = not online_elsewhere
            if new_enabled != assistant_state["enabled"]:
                if new_enabled:
                    assistant_state["replied"] = set()  # نشست تازه = دوباره به همه جواب بده
                assistant_state["enabled"] = new_enabled
        except Exception as e:
            _record_error()
            print("خطا در بررسی وضعیت آنلاین/آفلاین:", e)
        await asyncio.sleep(ASSISTANT_CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# ۹) مدیریت گروه: kick / ban / promote / demote (فقط جایی که ادمین هستید)
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat(["اخراج", "kick"], arg=False)))
async def kick_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    reply = await event.get_reply_message()
    try:
        await client.kick_participant(event.chat_id, reply.sender_id)
        await event.edit("✅ کاربر کیک شد")
    except Exception as e:
        _record_error()
        await event.edit(f"❌ خطا: {e}")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["مسدود", "ban"], arg=False)))
async def ban_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    reply = await event.get_reply_message()
    try:
        await client.edit_permissions(event.chat_id, reply.sender_id, view_messages=False)
        await event.edit("✅ کاربر بن شد")
    except Exception as e:
        _record_error()
        await event.edit(f"❌ خطا: {e}")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["ارتقا", "promote"])))
async def promote_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    title = (event.pattern_match.group(1) or "Admin")[:16]
    reply = await event.get_reply_message()
    try:
        await client.edit_admin(event.chat_id, reply.sender_id, is_admin=True, title=title)
        await event.edit(f"✅ کاربر ادمین شد ({title})")
    except Exception as e:
        _record_error()
        await event.edit(f"❌ خطا: {e}")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["تنزل", "demote"], arg=False)))
async def demote_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    reply = await event.get_reply_message()
    try:
        await client.edit_admin(event.chat_id, reply.sender_id, is_admin=False)
        await event.edit("✅ ادمین کاربر حذف شد")
    except Exception as e:
        _record_error()
        await event.edit(f"❌ خطا: {e}")


# ---------------------------------------------------------------------------
# ۱۰) بکاپ‌گیری: پیام‌ها (متن/JSON)، رسانه‌ها، لیست چت‌ها، و کل تنظیمات بات
# ---------------------------------------------------------------------------

BACKUP_MAX_MESSAGES = 2000   # سقف تعداد پیام قابل‌بررسی در یک بکاپ (جلوگیری از فلود/تایم‌اوت)
BACKUP_MAX_MEDIA = 50        # سقف تعداد فایل رسانه‌ در یک بکاپ رسانه (برای رعایت محدودیت‌های تلگرام)


def _gather_config_snapshot():
    """همه‌ی تنظیمات/وضعیتِ ذخیره‌شدنیِ بات رو توی یک دیکشنری واحد جمع می‌کنه."""
    return {
        "_kind": "selfbot_config_backup",
        "_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "notes": load_notes(),
        "autopost": autopost_state,
        "assistant": {
            "mode": assistant_state["mode"],
            "text": assistant_state["text"],
            "delay": assistant_state["delay"],
            "include": list(assistant_state["include"]),
            "exclude": list(assistant_state["exclude"]),
            "auto_detect": assistant_state["auto_detect"],
            "manual_enabled": assistant_state["enabled"] if not assistant_state["auto_detect"] else False,
        },
        "font": font_state,
        "clock": {"enabled": clock_state["enabled"], "style": clock_state["style"]},
        "stats": STATS,
    }


def _apply_config_snapshot(data):
    """
    یه اسنپ‌شات (خروجیِ _gather_config_snapshot) رو روی وضعیت زنده‌ی بات اعمال
    می‌کنه و فایل‌های مربوطه رو هم روی دیسک به‌روز می‌کنه. کلیدهایی که توی فایل
    بکاپ نباشن دست‌نخورده می‌مونن (merge، نه جایگزینیِ کامل).
    """
    applied = []

    if isinstance(data.get("notes"), dict):
        save_notes(data["notes"])
        applied.append("یادداشت‌ها")

    if isinstance(data.get("autopost"), dict):
        autopost_state.update(data["autopost"])
        save_autopost()
        applied.append("ارسال‌خودکار")

    if isinstance(data.get("assistant"), dict):
        a = data["assistant"]
        assistant_state["mode"] = a.get("mode", assistant_state["mode"])
        assistant_state["text"] = a.get("text", assistant_state["text"])
        assistant_state["delay"] = a.get("delay", assistant_state["delay"])
        assistant_state["include"] = set(a.get("include", []))
        assistant_state["exclude"] = set(a.get("exclude", []))
        assistant_state["auto_detect"] = a.get("auto_detect", assistant_state["auto_detect"])
        if not assistant_state["auto_detect"]:
            assistant_state["enabled"] = a.get("manual_enabled", False)
        save_assistant()
        applied.append("منشی")

    if isinstance(data.get("font"), dict):
        font_state.update(data["font"])
        save_font_state()
        applied.append("فونت")

    if isinstance(data.get("clock"), dict):
        if "enabled" in data["clock"]:
            clock_state["enabled"] = bool(data["clock"]["enabled"])
        if data["clock"].get("style") in CLOCK_STYLES:
            clock_state["style"] = data["clock"]["style"]
        applied.append("ساعت")

    return applied


@client.on(events.NewMessage(outgoing=True, pattern=pat(["پشتیبان", "backup"])))
async def backup_handler(event):
    args = (event.pattern_match.group(1) or "").strip()
    parts = args.split(None, 1)
    sub = parts[0].lower() if parts else ""

    # ---- .پشتیبان تنظیمات : بکاپ کامل تنظیمات/وضعیتِ بات (برای بازگردانی بعد از ری‌دیپلوی) ----
    if sub in ("تنظیمات", "settings", "config"):
        await event.edit("⏳ در حال آماده‌سازی بکاپ تنظیمات...")
        snapshot = _gather_config_snapshot()
        content = json.dumps(snapshot, ensure_ascii=False, indent=2)
        bio = BytesIO(content.encode("utf-8"))
        bio.name = f"selfbot_config_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        await client.send_file(
            "me", bio,
            caption="⚙️ بکاپ تنظیمات سلف‌بات (یادداشت‌ها، منشی، ارسال‌خودکار، فونت، ساعت، آمار)\n"
                    f"برای بازیابی: روی همین فایل ریپلای کن و بنویس `{PREFIX}بازیابی`",
        )
        return await event.edit("✅ بکاپ تنظیمات به Saved Messages ارسال شد")

    # ---- .پشتیبان چت‌ها : بکاپ لیست همه‌ی چت‌ها/دیالوگ‌های اکانت ----
    if sub in ("چت‌ها", "چتها", "chats", "dialogs"):
        await event.edit("⏳ در حال جمع‌آوری لیست چت‌ها...")
        lines = []
        async for d in client.iter_dialogs():
            kind = "کانال" if (d.is_channel and not d.is_group) else ("گروه" if d.is_group else "خصوصی")
            extra = f" — {d.unread_count} خوانده‌نشده" if d.unread_count else ""
            lines.append(f"[{kind}] {d.name} — id={d.id}{extra}")
        content = "\n".join(lines) or "(چتی پیدا نشد)"
        bio = BytesIO(content.encode("utf-8"))
        bio.name = "chats_backup.txt"
        await client.send_file("me", bio, caption=f"📇 بکاپ لیست {len(lines)} چت")
        return await event.edit("✅ بکاپ لیست چت‌ها به Saved Messages ارسال شد")

    # ---- .پشتیبان رسانه <عدد> : دانلود و فوروارد رسانه‌های N پیام آخر به Saved Messages ----
    if sub in ("رسانه", "media"):
        n_str = parts[1].strip() if len(parts) > 1 else ""
        n = int(n_str) if n_str.isdigit() else 200
        n = min(n, BACKUP_MAX_MESSAGES)
        await event.edit(f"⏳ در حال بررسی {n} پیام آخر برای رسانه...")
        sent = 0
        hit_cap = False
        async for m in client.iter_messages(event.chat_id, limit=n):
            if not m.media:
                continue
            if sent >= BACKUP_MAX_MEDIA:
                hit_cap = True
                break
            try:
                date = m.date.strftime("%Y-%m-%d %H:%M")
                await client.send_file("me", m.media, caption=f"🗂 از چت {event.chat_id} — {date}")
                sent += 1
            except Exception as e:
                _record_error()
                print("خطا در بکاپ رسانه:", e)
        note = f" (به سقف {BACKUP_MAX_MEDIA} فایل رسیدیم، بقیه ارسال نشدن)" if hit_cap else ""
        return await event.edit(f"✅ {sent} فایل رسانه به Saved Messages ارسال شد{note}")

    # ---- .پشتیبان json <عدد> : بکاپ ساختاریافته‌ی پیام‌ها (برای پردازش برنامه‌ای) ----
    as_json = sub in ("json", "جیسون")
    n_str = (parts[1].strip() if len(parts) > 1 else "") if as_json else args
    n = int(n_str) if n_str and n_str.isdigit() else 100
    n = min(n, BACKUP_MAX_MESSAGES)
    await event.edit(f"⏳ در حال گرفتن بکاپ {n} پیام آخر...")

    if as_json:
        items = []
        async for m in client.iter_messages(event.chat_id, limit=n):
            sender = await m.get_sender()
            name = getattr(sender, "first_name", None) if sender else None
            items.append({
                "id": m.id,
                "date": m.date.isoformat(),
                "sender_id": m.sender_id,
                "sender_name": name,
                "text": m.raw_text or None,
                "media_type": type(m.media).__name__ if m.media else None,
            })
        items.reverse()
        content = json.dumps(items, ensure_ascii=False, indent=2)
        bio = BytesIO(content.encode("utf-8"))
        bio.name = "backup.json"
    else:
        lines = []
        async for m in client.iter_messages(event.chat_id, limit=n):
            sender = await m.get_sender()
            name = getattr(sender, "first_name", "؟") if sender else "؟"
            date = m.date.strftime("%Y-%m-%d %H:%M")
            lines.append(f"[{date}] {name}: {m.raw_text or '(media)'}")
        lines.reverse()
        content = "\n".join(lines) or "(چتی برای بکاپ پیدا نشد)"
        bio = BytesIO(content.encode("utf-8"))
        bio.name = "backup.txt"

    await client.send_file("me", bio, caption=f"📦 بکاپ {n} پیام از چت {event.chat_id}")
    await event.edit("✅ بکاپ به Saved Messages ارسال شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat(["بازیابی", "restore"], arg=False)))
async def restore_handler(event):
    """با ریپلای روی فایلِ خروجیِ `.پشتیبان تنظیمات`، تنظیمات بات رو برمی‌گردونه."""
    if not event.is_reply:
        return await event.edit(f"روی فایل بکاپِ تنظیمات (خروجیِ `{PREFIX}پشتیبان تنظیمات`) ریپلای کن")
    reply = await event.get_reply_message()
    if not reply.file:
        return await event.edit("پیام ریپلای‌شده فایل نداره")
    await event.edit("⏳ در حال بازیابی تنظیمات...")
    try:
        raw = await client.download_media(reply, file=bytes)
        data = json.loads(raw.decode("utf-8"))
        if data.get("_kind") != "selfbot_config_backup":
            return await event.edit("❌ این فایل، بکاپ تنظیماتِ سلف‌بات نیست")
        applied = _apply_config_snapshot(data)
        if not applied:
            return await event.edit("چیزی برای بازیابی توی این فایل پیدا نشد")
        await event.edit("✅ بازیابی شد: " + "، ".join(applied))
    except json.JSONDecodeError:
        await event.edit("❌ فایل معتبر (JSON) نیست")
    except Exception as e:
        _record_error()
        await event.edit(f"❌ خطا در بازیابی: {e}")


# ---------------------------------------------------------------------------
# ۱۱) ارسال خودکار متن به گروه (autopost)
# ---------------------------------------------------------------------------

def _autopost_status_text():
    status = "روشن ✅" if autopost_state["enabled"] else "خاموش ❌"
    n = autopost_state["interval_minutes"]
    chats = autopost_state["chats"]
    if chats:
        chat_lines = "\n".join(f"   – {title} (`{cid}`)" for cid, title in chats.items())
        dest_line = f"{len(chats)} گروهِ مشخص\n{chat_lines}"
    else:
        dest_line = f"هیچ‌کدام (اول با `{PREFIX}ارسال‌خودکار افزودن` اضافه کن)"
    text_preview = autopost_state["text"] or "(تنظیم نشده)"
    return (
        "🔁 **ارسال خودکار متن**\n\n"
        f"• وضعیت: {status}\n"
        f"• فاصله: {n} دقیقه\n"
        f"• گروه‌های مقصد: {dest_line}\n"
        f"• متن: {text_preview}\n\n"
        f"راهنما: `{PREFIX}ارسال‌خودکار روشن/خاموش` ، `{PREFIX}ارسال‌خودکار فاصله <عدد>` ، "
        f"`{PREFIX}ارسال‌خودکار متن <متن>` ، `{PREFIX}ارسال‌خودکار افزودن/حذف` ، `{PREFIX}ارسال‌خودکار فوری`"
    )


@client.on(events.NewMessage(outgoing=True, pattern=pat(["ارسال‌خودکار", "autopost"])))
async def autopost_handler(event):
    global _autopost_force_now

    raw = (event.pattern_match.group(1) or "").strip()
    parts = raw.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if not sub:
        return await event.edit(_autopost_status_text())

    if sub in ("روشن", "on"):
        if not autopost_state["text"]:
            return await event.edit(f"❌ اول یه متن ست کن: `{PREFIX}ارسال‌خودکار متن <متن>`")
        if not autopost_state["chats"]:
            return await event.edit(f"❌ اول حداقل یه گروه اضافه کن: `{PREFIX}ارسال‌خودکار افزودن` (داخل خود گروه بفرست)")
        autopost_state["enabled"] = True
        _reset_autopost_timer()
        save_autopost()
        return await event.edit(_autopost_status_text())

    if sub in ("خاموش", "off"):
        autopost_state["enabled"] = False
        save_autopost()
        return await event.edit(_autopost_status_text())

    if sub in ("فاصله", "interval"):
        if not rest.strip().isdigit():
            return await event.edit(f"مثال: `{PREFIX}ارسال‌خودکار فاصله 5`")
        n = max(int(rest.strip()), AUTOPOST_MIN_INTERVAL_MINUTES)
        autopost_state["interval_minutes"] = n
        _reset_autopost_timer()
        save_autopost()
        warn = "" if n >= 5 else "\n⚠️ فاصله‌ی کمتر از ۵ دقیقه ریسک محدودیت از طرف تلگرام رو بالا می‌بره."
        return await event.edit(f"✅ فاصله روی {n} دقیقه تنظیم شد{warn}")

    if sub in ("متن", "text"):
        text = rest
        if not text and event.is_reply:
            reply = await event.get_reply_message()
            text = reply.raw_text or ""
        if not text:
            return await event.edit(
                f"مثال: `{PREFIX}ارسال‌خودکار متن میو` یا ریپلای روی یه پیام + `{PREFIX}ارسال‌خودکار متن`"
            )
        autopost_state["text"] = text
        save_autopost()
        return await event.edit("✅ متن ارسال خودکار ذخیره شد")

    if sub in ("افزودن", "add"):
        chat_id = int(rest.strip()) if rest.strip().lstrip("-").isdigit() else event.chat_id
        try:
            chat = await client.get_entity(chat_id)
        except Exception as e:
            _record_error()
            return await event.edit(f"❌ خطا در پیداکردن چت: {e}")
        title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)
        autopost_state["chats"][str(chat_id)] = title
        save_autopost()
        return await event.edit(f"✅ «{title}» به لیست مقصدها اضافه شد")

    if sub in ("حذف", "remove"):
        chat_id = int(rest.strip()) if rest.strip().lstrip("-").isdigit() else event.chat_id
        removed = autopost_state["chats"].pop(str(chat_id), None)
        save_autopost()
        if removed:
            return await event.edit(f"🗑 «{removed}» از لیست مقصدها حذف شد")
        return await event.edit("این چت توی لیست مقصدها نبود")

    if sub in ("پاک", "clear"):
        autopost_state["chats"].clear()
        save_autopost()
        return await event.edit("🗑 همه‌ی مقصدها پاک شدن")

    if sub in ("فوری", "now"):
        _autopost_force_now = True
        return await event.edit("⏩ ارسال فوری توی صف قرار گرفت (تا ۵ ثانیه دیگه)")

    await event.edit(f"دستور نامعتبره. برای وضعیت کامل: `{PREFIX}ارسال‌خودکار`")


async def autopost_worker():
    global _autopost_force_now
    while True:
        await asyncio.sleep(5)
        if not autopost_state["enabled"] or not autopost_state["chats"] or not autopost_state["text"]:
            continue
        if _autopost_force_now or time.time() >= _autopost_next_run:
            _autopost_force_now = False
            for chat_id_str in list(autopost_state["chats"].keys()):
                try:
                    await client.send_message(int(chat_id_str), autopost_state["text"])
                    STATS["autopost_ok"] += 1
                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    print(f"خطا در ارسال خودکار به {chat_id_str}:", e)
                    STATS["autopost_fail"] += 1
                    _record_error()
            _reset_autopost_timer()
            save_stats()


# ---------------------------------------------------------------------------
# ۱۲) آمار سلف‌بات
# ---------------------------------------------------------------------------

@client.on(events.NewMessage())
async def stats_collector(event):
    """
    یه هندلر عمومیِ کم‌هزینه که روی *هر* پیامی (ورودی یا خروجی، دستور یا معمولی)
    اجرا می‌شه تا آمار کلی رو جمع کنه - بدون نیاز به دست‌کاری تک‌تک هندلرهای
    بالا. تشخیص «دستور واقعی» با چک‌کردن اولین کلمه‌ی بعد از پیشوند در
    ALL_COMMAND_NAMES انجام می‌شه (همون دیکشنری‌ای که pat() موقع ثبت هر
    دستور پر می‌کنه)، پس تایپ‌های اشتباه با پیشوند به‌اشتباه به‌عنوان دستورِ
    اجراشده شمرده نمی‌شن.
    """
    _record_message(event)
    if event.out and event.raw_text and event.raw_text.startswith(PREFIX):
        rest = event.raw_text[len(PREFIX):]
        first_word = rest.split(None, 1)[0] if rest.strip() else ""
        if first_word:
            _record_command(event, first_word)


def _format_uptime():
    return str(timedelta(seconds=int(time.time() - START_TIME)))


def _stats_summary_text():
    top_commands = sorted(STATS["commands_by_name"].items(), key=lambda kv: kv[1], reverse=True)[:5]
    if top_commands:
        cmd_lines = "\n".join(f"   {i+1}. `{name}` — {n} بار" for i, (name, n) in enumerate(top_commands))
    else:
        cmd_lines = "   (هنوز دستوری اجرا نشده)"

    per_chat = STATS["per_chat"]
    top_chats = sorted(per_chat.items(), key=lambda kv: kv[1]["messages"] + kv[1]["commands"], reverse=True)[:5]
    if top_chats:
        chat_lines = "\n".join(
            f"   – {info.get('title') or cid}: {info['messages']} پیام، {info['commands']} دستور"
            for cid, info in top_chats
        )
    else:
        chat_lines = "   (هنوز پیامی ثبت نشده)"

    return (
        "📊 **آمار سلف‌بات**\n\n"
        f"⏳ زمان فعالیت: `{_format_uptime()}`\n"
        f"⚙️ دستورات اجراشده: **{STATS['commands_total']}**\n"
        f"✉️ پیام‌های پردازش‌شده: **{STATS['messages_total']}**\n"
        f"🔁 ارسال‌خودکار موفق/ناموفق: **{STATS['autopost_ok']}** / **{STATS['autopost_fail']}**\n"
        f"❌ خطاهای سیستمی: **{STATS['errors']}**\n\n"
        f"🏆 پراستفاده‌ترین دستورها:\n{cmd_lines}\n\n"
        f"💬 فعال‌ترین چت‌ها:\n{chat_lines}\n\n"
        f"جزئیات کامل هر چت: `{PREFIX}آمار چت‌ها`\n"
        f"پاک‌کردن و شروع دوباره‌ی شمارش: `{PREFIX}آمار بازنشانی`"
    )


async def _stats_chats_text():
    per_chat = STATS["per_chat"]
    if not per_chat:
        return "هنوز آماری برای هیچ چتی ثبت نشده"
    ordered = sorted(per_chat.items(), key=lambda kv: kv[1]["messages"] + kv[1]["commands"], reverse=True)
    lines = ["💬 **آمار به‌تفکیک چت:**\n"]
    for cid, info in ordered[:20]:
        title = info.get("title")
        if not title:
            try:
                chat = await client.get_entity(int(cid))
                title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or cid
                info["title"] = title
            except Exception:
                title = cid
        lines.append(f"▫️ **{title}** — {info['messages']} پیام، {info['commands']} دستور")
    if len(ordered) > 20:
        lines.append(f"\n… و {len(ordered) - 20} چت دیگر")
    return "\n".join(lines)


@client.on(events.NewMessage(outgoing=True, pattern=pat(["آمار", "stats"])))
async def stats_handler(event):
    raw = (event.pattern_match.group(1) or "").strip().lower()
    sub = raw.split(maxsplit=1)[0] if raw else ""

    if not sub:
        save_stats()
        return await event.edit(_stats_summary_text())

    if sub in ("چت‌ها", "چتها", "chats"):
        return await event.edit(await _stats_chats_text())

    if sub in ("بازنشانی", "ریست", "reset"):
        STATS.update({
            "commands_total": 0,
            "commands_by_name": {},
            "messages_total": 0,
            "autopost_ok": 0,
            "autopost_fail": 0,
            "errors": 0,
            "per_chat": {},
        })
        save_stats()
        return await event.edit("🗑 آمار پاک شد و شمارش از نو شروع شد")

    await event.edit(f"دستور نامعتبره. برای دیدن آمار: `{PREFIX}آمار`")


async def stats_saver():
    """هر چند ثانیه یک‌بار آمار رو روی دیسک ذخیره می‌کنه تا با ری‌استارت/ری‌دیپلوی از دست نره."""
    while True:
        await asyncio.sleep(STATS_SAVE_INTERVAL)
        save_stats()


# ---------------------------------------------------------------------------
# ۱۳) راهنما
# ---------------------------------------------------------------------------

def build_help_text():
    return f"""📋 **لیست دستورات سلف‌بات** (پیشوند: `{PREFIX}`)
ℹ️ نام‌های انگلیسیِ قدیمی (مثل `{PREFIX}ping`, `{PREFIX}help`) هم برای سازگاری هنوز کار می‌کنن.

**عمومی**
{PREFIX}پینگ — تست پینگ
{PREFIX}فعال — وضعیت بات
{PREFIX}آیدی — آیدی چت/کاربر/پیام
{PREFIX}اطلاعات — اطلاعات کاربر (ریپلای اختیاری)

**ابزار**
{PREFIX}حساب <عبارت> — ماشین‌حساب
{PREFIX}کیوآر <متن> — ساخت کیو‌آر کد
{PREFIX}کوتاه <لینک> — کوتاه‌کردن لینک
{PREFIX}هوا <شهر> — آب‌وهوا
{PREFIX}ترجمه <زبان> <متن> — ترجمه
{PREFIX}جستجو <عبارت> — جستجوی گوگل

**یادداشت**
{PREFIX}یادداشت <کلید> <متن> — ذخیره یادداشت
{PREFIX}یادداشت‌ها — لیست یادداشت‌ها
{PREFIX}نمایش‌یادداشت <کلید> — نمایش یادداشت
{PREFIX}حذف‌یادداشت <کلید> — حذف یادداشت

**مدیریت پیام**
{PREFIX}حذف — حذف پیام ریپلای‌شده
{PREFIX}پاکسازی <عدد> — حذف چند پیام آخر (یا ریپلای)
{PREFIX}سنجاق / {PREFIX}برداشتن‌سنجاق — پین/آنپین پیام ریپلای‌شده

**سرگرمی**
{PREFIX}تایپ‌زنده <متن> — افکت تایپ زنده
{PREFIX}پیش‌تایپ <متن> — شبیه‌سازی تایپ قبل از ارسال
{PREFIX}معکوس <متن> — معکوس کردن متن
{PREFIX}طنز <متن> — تبدیل به حروف بزرگ‌وکوچکِ متناوب (طنزآمیز)
{PREFIX}تاس <۱ تا ۶> — انداختن تاس واقعی تا رسیدن به همون عدد
{PREFIX}شیرخط — شیر یا خط
{PREFIX}تصادفی <min> <max> — عدد تصادفی بین دو عدد
{PREFIX}انتخاب <گزینه۱, گزینه۲, ...> — انتخاب تصادفی بین چند گزینه
{PREFIX}سنگ‌کاغذقیچی <سنگ/کاغذ/قیچی> — بازی سنگ‌کاغذقیچی با بات
{PREFIX}حدس شروع <سقف اختیاری> — شروع بازیِ حدسِ عدد
{PREFIX}حدس <عدد> — حدس‌زدن توی بازیِ فعال (راهنمای بالاتر/پایین‌تر می‌ده)
{PREFIX}حدس لغو — لغو بازیِ فعال
{PREFIX}اسلات — ماشین اسلات (سه تا نماد تصادفی)
{PREFIX}جادوگر <سوال> — پاسخ تصادفیِ توپ جادویی (بله/خیر)
{PREFIX}عشق‌سنج <اسم۱> و <اسم۲> — درصد عشق‌سنجِ شوخی بین دو اسم
{PREFIX}این‌یا‌اون — یه سوالِ «این یا اون» تصادفی

**فونت پیام**
{PREFIX}قلم فهرست — لیست فونت‌های موجود (انگلیسی/فارسی/ترکیبی)
{PREFIX}قلم <نام> <متن> — تبدیل یه‌بارِ متن به فونت انتخابی
{PREFIX}قلم تنظیم <نام> — تنظیم فونت پیش‌فرض برای حالت خودکار
{PREFIX}قلم روشن/خاموش — روشن/خاموش کردن اعمال خودکار فونت روی همه‌ی پیام‌ها
{PREFIX}قلم وضعیت — وضعیت فونت خودکار

**پروفایل**
{PREFIX}بیو <متن> — تغییر بیو
{PREFIX}نام <متن> — تغییر نام پایه (زیربنای ساعت زنده)
{PREFIX}عکس — تغییر عکس پروفایل (ریپلای روی عکس)
{PREFIX}ساعت روشن/خاموش — روشن/خاموش‌کردن ساعت زنده در نام
{PREFIX}شکل‌ساعت — لیست استایل‌های ساعت (فونت/شکل)
{PREFIX}شکل‌ساعت <نام>/بعدی — تغییر استایل ساعت

**🤖 منشی چت**
{PREFIX}منشی روشن/خاموش — روشن/خاموش کردن دستی (قفل می‌شه، تشخیص خودکار غیرفعال می‌شه)
{PREFIX}منشی خودکار — برگشت به تشخیص خودکار آنلاین/آفلاین
{PREFIX}منشی وضعیت — نمایش وضعیت منشی
{PREFIX}منشی متن <متن> — تنظیم پیام پاسخ
{PREFIX}منشی تأخیر <ثانیه> — تنظیم تأخیر
{PREFIX}منشی حالت <خودکار/منشن/پیوی/گروه‌ها> — تعیین حالت پاسخ
{PREFIX}منشی مستثنی — عدم پاسخ در چت فعلی
{PREFIX}منشی شامل — فعال‌سازی برای چت فعلی
{PREFIX}منشی پاک — حذف لیست چت‌ها

**مدیریت گروه** (فقط جایی که ادمین هستید)
{PREFIX}اخراج / {PREFIX}مسدود / {PREFIX}ارتقا / {PREFIX}تنزل — با ریپلای روی کاربر

**بکاپ‌گیری**
{PREFIX}پشتیبان <عدد> — بکاپ متنی از پیام‌های چت به Saved Messages
{PREFIX}پشتیبان json <عدد> — همون، ولی خروجی JSON ساختاریافته
{PREFIX}پشتیبان رسانه <عدد> — دانلود/فوروارد رسانه‌های N پیام آخر
{PREFIX}پشتیبان چت‌ها — بکاپ لیست همه‌ی چت‌ها/دیالوگ‌های اکانت
{PREFIX}پشتیبان تنظیمات — بکاپ کامل تنظیمات بات (یادداشت، منشی، ارسال‌خودکار، فونت، ساعت، آمار)
{PREFIX}بازیابی — با ریپلای روی فایل بکاپِ تنظیمات، همه‌چیز رو برمی‌گردونه

**ارسال خودکار متن**
{PREFIX}ارسال‌خودکار — نمایش وضعیت کامل
{PREFIX}ارسال‌خودکار روشن/خاموش — روشن/خاموش
{PREFIX}ارسال‌خودکار فاصله <دقیقه> — تنظیم فاصله
{PREFIX}ارسال‌خودکار متن <متن> — تنظیم متن (یا ریپلای)
{PREFIX}ارسال‌خودکار افزودن/حذف — افزودن/حذف گروه فعلی از مقصدها
{PREFIX}ارسال‌خودکار پاک — پاک‌کردن همه‌ی مقصدها
{PREFIX}ارسال‌خودکار فوری — ارسال فوری (تست)

**📊 آمار**
{PREFIX}آمار — نمایش آمار کلی (دستورات، پیام‌ها، ارسال‌خودکار، خطاها، زمان فعالیت)
{PREFIX}آمار چت‌ها — آمار به‌تفکیک هر چت
{PREFIX}آمار بازنشانی — پاک‌کردن همه‌ی آمار و شروع دوباره

{PREFIX}راهنما — همین راهنما
"""


@client.on(events.NewMessage(outgoing=True, pattern=pat(["راهنما", "help"], arg=False)))
async def help_handler(event):
    await event.edit(build_help_text())


# ---------------------------------------------------------------------------
# ساعت زنده در نام پروفایل (فیچر پس‌زمینه)
# ---------------------------------------------------------------------------

async def clock_updater():
    """
    برخلاف نسخه‌ی قبلی که هر ۶۰ ثانیه از لحظه‌ی روشن‌شدن ربات صبر می‌کرد (و همین
    باعث می‌شد ساعت تا ۱ دقیقه عقب بیفته)، این نسخه همیشه دقیقاً سر شروع هر
    دقیقه (ثانیه صفر) بیدار می‌شه و پروفایل رو آپدیت می‌کنه. CLOCK_INTERVAL حالا
    یعنی «هر چند دقیقه یک‌بار آپدیت بشه» (پیش‌فرض هر ۱ دقیقه). همچنین هر تیک،
    نام زنده رو با تلگرام هماهنگ می‌کنه تا اگه کاربر مستقیم توی اپ اسمش رو
    عوض کرده باشه، ربات روش بازنویسی نکنه.
    """
    interval_minutes = max(CLOCK_INTERVAL // 60, 1)
    last_sent = None  # tuple (style, base, hour, minute) برای تشخیص تغییر واقعی
    while True:
        now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        if clock_state["enabled"] and now.minute % interval_minutes == 0:
            try:
                base = (await _refresh_base_name())[:40]
                key = (clock_state["style"], base, now.hour, now.minute)
                if key != last_sent:
                    clock_part = CLOCK_STYLES[clock_state["style"]](now.hour, now.minute)
                    new_name = f"{base} | {clock_part}"
                    await client(functions.account.UpdateProfileRequest(first_name=new_name))
                    last_sent = key
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception as e:
                _record_error()
                print("خطا در بروزرسانی ساعت:", e)
        # صبر تا دقیقاً لحظه‌ی شروع دقیقه‌ی بعدی (نه یک فاصله‌ی ثابت و بی‌ربط به ساعت واقعی)
        now2 = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        await asyncio.sleep(max(60 - now2.second, 1))


# ---------------------------------------------------------------------------
# اجرا
# ---------------------------------------------------------------------------

async def main():
    global SELF_ID
    await get_http_session()  # ساخت ClientSession مشترک قبل از شروع کار
    me = await client.get_me()
    SELF_ID = me.id
    print(f"✅ سلف‌بات با اکانت {me.first_name} روشن شد")
    client.loop.create_task(clock_updater())
    client.loop.create_task(autopost_worker())
    client.loop.create_task(assistant_status_watcher())
    client.loop.create_task(stats_saver())
    try:
        await client.run_until_disconnected()
    finally:
        if HTTP_SESSION is not None and not HTTP_SESSION.closed:
            await HTTP_SESSION.close()


with client:
    client.loop.run_until_complete(main())
