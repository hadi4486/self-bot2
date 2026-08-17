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
import operator
import asyncio
import requests
from io import BytesIO
from datetime import datetime, timedelta

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

if SESSION_STRING:
    from telethon.sessions import StringSession
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    client = TelegramClient("selfbot_session", API_ID, API_HASH)

START_TIME = time.time()
afk_state = {"active": False, "reason": "", "replied": set(), "since": None}
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
# ابزارهای کمکی
# ---------------------------------------------------------------------------

def pat(name, arg=True):
    """ساخت الگوی regex برای دستورات خروجی (پیام‌هایی که خودتون می‌فرستید)"""
    esc = re.escape(PREFIX)
    if arg:
        return rf"^{esc}{name}(?:\s+([\s\S]*))?$"
    return rf"^{esc}{name}$"


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

@client.on(events.NewMessage(outgoing=True, pattern=pat("ping", arg=False)))
async def ping_handler(event):
    start = time.time()
    msg = await event.edit("🏓 Pinging...")
    delta = (time.time() - start) * 1000
    await msg.edit(f"🏓 Pong!\n⏱ {delta:.0f} ms")


@client.on(events.NewMessage(outgoing=True, pattern=pat("alive", arg=False)))
async def alive_handler(event):
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    text = (
        "🤖 **سلف‌بات فعال است**\n"
        f"⏳ Uptime: `{uptime}`\n"
        f"🔡 Prefix: `{PREFIX}`\n"
        f"🕐 ساعت زنده: {'روشن' if clock_state['enabled'] else 'خاموش'}"
    )
    await event.edit(text)


@client.on(events.NewMessage(outgoing=True, pattern=pat("id", arg=False)))
async def id_handler(event):
    text = f"🆔 Chat ID: `{event.chat_id}`\n"
    if event.is_reply:
        reply = await event.get_reply_message()
        text += f"👤 User ID: `{reply.sender_id}`\n"
        text += f"✉️ Message ID: `{reply.id}`"
    await event.edit(text)


@client.on(events.NewMessage(outgoing=True, pattern=pat("info", arg=False)))
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

@client.on(events.NewMessage(outgoing=True, pattern=pat("calc")))
async def calc_handler(event):
    expr = event.pattern_match.group(1)
    if not expr:
        return await event.edit(f"مثال: `{PREFIX}calc 5*(3+2)`")
    try:
        result = safe_eval(expr)
        await event.edit(f"🧮 `{expr}` = **{result}**")
    except Exception:
        await event.edit("❌ عبارت ریاضی نامعتبره")


@client.on(events.NewMessage(outgoing=True, pattern=pat("qr")))
async def qr_handler(event):
    import qrcode
    text = event.pattern_match.group(1)
    if not text:
        return await event.edit(f"مثال: `{PREFIX}qr https://example.com`")
    img = qrcode.make(text)
    bio = BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    await event.delete()
    await client.send_file(event.chat_id, bio, caption=f"🔳 QR برای: {text}")


@client.on(events.NewMessage(outgoing=True, pattern=pat("shorten")))
async def shorten_handler(event):
    url = event.pattern_match.group(1)
    if not url:
        return await event.edit(f"مثال: `{PREFIX}shorten https://example.com/long-link`")
    try:
        r = requests.get("https://is.gd/create.php",
                          params={"format": "simple", "url": url}, timeout=10)
        await event.edit(f"🔗 لینک کوتاه‌شده:\n{r.text}")
    except Exception:
        await event.edit("❌ خطا در کوتاه کردن لینک")


@client.on(events.NewMessage(outgoing=True, pattern=pat("weather")))
async def weather_handler(event):
    city = event.pattern_match.group(1)
    if not city:
        return await event.edit(f"مثال: `{PREFIX}weather Tehran`")
    try:
        r = requests.get(f"https://wttr.in/{city}?format=%C+%t+%h+%w", timeout=10)
        await event.edit(f"🌤 آب‌وهوای {city}:\n{r.text}")
    except Exception:
        await event.edit("❌ خطا در دریافت آب‌وهوا")


@client.on(events.NewMessage(outgoing=True, pattern=pat("tr")))
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
        return await event.edit(f"مثال: `{PREFIX}tr en سلام دنیا` یا با ریپلای: `{PREFIX}tr en`")
    try:
        r = requests.get("https://api.mymemory.translated.net/get",
                          params={"q": text, "langpair": f"auto|{lang}"}, timeout=10)
        translated = r.json()["responseData"]["translatedText"]
        await event.edit(f"🌐 ترجمه ({lang}):\n{translated}")
    except Exception:
        await event.edit("❌ خطا در ترجمه")


@client.on(events.NewMessage(outgoing=True, pattern=pat("google")))
async def google_handler(event):
    q = event.pattern_match.group(1)
    if not q:
        return await event.edit(f"مثال: `{PREFIX}google چطور پایتون یاد بگیرم`")
    link = "https://www.google.com/search?q=" + requests.utils.quote(q)
    await event.edit(f"🔍 نتایج گوگل برای: {q}\n{link}")


# ---------------------------------------------------------------------------
# ۳) یادداشت‌ها: note / notes / getnote / delnote
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat("note")))
async def note_handler(event):
    args = event.pattern_match.group(1)
    if not args or " " not in args:
        return await event.edit(f"مثال: `{PREFIX}note keyname متن یادداشت`")
    key, text = args.split(" ", 1)
    notes = load_notes()
    notes[key] = text
    save_notes(notes)
    await event.edit(f"📝 یادداشت `{key}` ذخیره شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat("notes", arg=False)))
async def notes_list_handler(event):
    notes = load_notes()
    if not notes:
        return await event.edit("هیچ یادداشتی وجود نداره")
    text = "📒 لیست یادداشت‌ها:\n" + "\n".join(f"• `{k}`" for k in notes)
    await event.edit(text)


@client.on(events.NewMessage(outgoing=True, pattern=pat("getnote")))
async def getnote_handler(event):
    key = event.pattern_match.group(1)
    notes = load_notes()
    if not key or key not in notes:
        return await event.edit("همچین یادداشتی پیدا نشد")
    await event.edit(f"📝 `{key}`:\n{notes[key]}")


@client.on(events.NewMessage(outgoing=True, pattern=pat("delnote")))
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

@client.on(events.NewMessage(outgoing=True, pattern=pat("del", arg=False)))
async def del_handler(event):
    if event.is_reply:
        reply = await event.get_reply_message()
        await reply.delete()
    await event.delete()


@client.on(events.NewMessage(outgoing=True, pattern=pat("purge")))
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
        await event.edit(f"مثال: `{PREFIX}purge 10` یا ریپلای روی پیام + `{PREFIX}purge`")


@client.on(events.NewMessage(outgoing=True, pattern=pat("pin", arg=False)))
async def pin_handler(event):
    if not event.is_reply:
        return await event.edit("روی یک پیام ریپلای کن")
    reply = await event.get_reply_message()
    await client.pin_message(event.chat_id, reply.id)
    await event.edit("📌 پین شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat("unpin", arg=False)))
async def unpin_handler(event):
    if not event.is_reply:
        return await event.edit("روی یک پیام ریپلای کن")
    reply = await event.get_reply_message()
    await client.unpin_message(event.chat_id, reply.id)
    await event.edit("📌 آنپین شد")


# ---------------------------------------------------------------------------
# ۵) سرگرمی: write / type / reverse / mock
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat("write")))
async def write_handler(event):
    text = event.pattern_match.group(1)
    if not text:
        return await event.edit(f"مثال: `{PREFIX}write سلام دنیا`")
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


@client.on(events.NewMessage(outgoing=True, pattern=pat("type")))
async def type_handler(event):
    text = event.pattern_match.group(1)
    if not text:
        return await event.edit(f"مثال: `{PREFIX}type سلام`")
    await event.delete()
    async with client.action(event.chat_id, "typing"):
        await asyncio.sleep(min(len(text) * 0.05, 5))
    await client.send_message(event.chat_id, text)


@client.on(events.NewMessage(outgoing=True, pattern=pat("reverse")))
async def reverse_handler(event):
    text = event.pattern_match.group(1)
    if not text and event.is_reply:
        reply = await event.get_reply_message()
        text = reply.raw_text
    if not text:
        return await event.edit(f"مثال: `{PREFIX}reverse سلام`")
    await event.edit(text[::-1])


@client.on(events.NewMessage(outgoing=True, pattern=pat("mock")))
async def mock_handler(event):
    text = event.pattern_match.group(1)
    if not text and event.is_reply:
        reply = await event.get_reply_message()
        text = reply.raw_text
    if not text:
        return await event.edit(f"مثال: `{PREFIX}mock متن شما`")
    mocked = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))
    await event.edit(mocked)


DICE_MAX_ATTEMPTS = 60  # سقف تلاش - میانگین لازم ۶ باره، این حاشیه‌ی امن کافیه


@client.on(events.NewMessage(outgoing=True, pattern=pat("تاس")))
async def dice_handler(event):
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg.isdigit() or not (1 <= int(arg) <= 6):
        return await event.edit(f"مثال: `{PREFIX}تاس 4` (عدد باید بین ۱ تا ۶ باشه)")
    target = int(arg)
    await event.delete()

    for _ in range(DICE_MAX_ATTEMPTS):
        try:
            msg = await client.send_message(event.chat_id, file=InputMediaDice("🎲"))
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            continue
        except Exception as e:
            return await client.send_message(event.chat_id, f"❌ خطا در ارسال تاس: {e}")

        value = getattr(msg.media, "value", None)
        if value == target:
            return  # تاس با عدد درست موند، تمام

        try:
            await msg.delete()
        except Exception:
            pass
        await asyncio.sleep(0.3)

    await client.send_message(
        event.chat_id, f"❌ بعد از {DICE_MAX_ATTEMPTS} تلاش نتونستم عدد {target} رو بیارم"
    )


# ---------------------------------------------------------------------------
# ۶) پروفایل: setbio / setname / setpic / clock
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat("setbio")))
async def setbio_handler(event):
    bio = event.pattern_match.group(1)
    if not bio:
        return await event.edit(f"مثال: `{PREFIX}setbio بیو جدید`")
    await client(functions.account.UpdateProfileRequest(about=bio))
    await event.edit("✅ بیو بروزرسانی شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat("setname")))
async def setname_handler(event):
    name = event.pattern_match.group(1)
    if not name:
        return await event.edit(f"مثال: `{PREFIX}setname نام جدید`")
    clock_state["base_name"] = name
    if clock_state["enabled"]:
        await _apply_clock_now()
    else:
        await client(functions.account.UpdateProfileRequest(first_name=name))
    await event.edit("✅ نام پایه بروزرسانی شد")


@client.on(events.NewMessage(outgoing=True, pattern=pat("setpic", arg=False)))
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


@client.on(events.NewMessage(outgoing=True, pattern=pat("clock")))
async def clock_toggle_handler(event):
    arg = (event.pattern_match.group(1) or "").strip().lower()
    if arg == "off":
        clock_state["enabled"] = False
        await event.edit("🕐 ساعت زنده خاموش شد")
    elif arg == "on":
        clock_state["enabled"] = True
        await event.edit("🕐 ساعت زنده روشن شد (طی چند ثانیه اعمال می‌شه)")
    else:
        await event.edit(f"استفاده: `{PREFIX}clock on` یا `{PREFIX}clock off`")


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
        print("خطا در اعمال فوری استایل ساعت:", e)


@client.on(events.NewMessage(outgoing=True, pattern=pat("clockstyle")))
async def clockstyle_handler(event):
    arg = (event.pattern_match.group(1) or "").strip().lower()
    if not arg or arg == "list":
        now = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        lines = ["🎨 **استایل‌های ساعت زنده:**\n"]
        for name in CLOCK_STYLE_ORDER:
            preview = CLOCK_STYLES[name](now.hour, now.minute)
            marker = "✅" if name == clock_state["style"] else "▫️"
            lines.append(f"{marker} `{name}` → {preview}")
        lines.append(f"\nبرای تغییر: `{PREFIX}clockstyle <نام>` یا `{PREFIX}clockstyle next`")
        return await event.edit("\n".join(lines))

    if arg == "next":
        idx = CLOCK_STYLE_ORDER.index(clock_state["style"])
        new_style = CLOCK_STYLE_ORDER[(idx + 1) % len(CLOCK_STYLE_ORDER)]
    elif arg in CLOCK_STYLES:
        new_style = arg
    else:
        return await event.edit(f"استایل نامعتبره. برای دیدن لیست: `{PREFIX}clockstyle list`")

    clock_state["style"] = new_style
    preview = CLOCK_STYLES[new_style](*(datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)).timetuple()[3:5])
    await event.edit(f"✅ استایل ساعت روی `{new_style}` تنظیم شد\nنمونه: {preview}")
    await _apply_clock_now()


# ---------------------------------------------------------------------------
# ۷) AFK: افک خودکار با پاسخ به منشن/پیوی
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat("afk")))
async def afk_handler(event):
    reason = event.pattern_match.group(1) or "بدون دلیل خاص"
    afk_state["active"] = True
    afk_state["reason"] = reason
    afk_state["since"] = datetime.now()
    afk_state["replied"] = set()
    await event.edit(f"😴 وضعیت AFK فعال شد\nدلیل: {reason}")


@client.on(events.NewMessage(outgoing=True, pattern=pat("unafk", arg=False)))
async def unafk_handler(event):
    afk_state["active"] = False
    afk_state["replied"] = set()
    await event.edit("✅ از حالت AFK خارج شدید")


@client.on(events.NewMessage(incoming=True))
async def afk_autoreply(event):
    if not afk_state["active"]:
        return
    me = await client.get_me()
    is_mention = bool(me.username) and event.raw_text and f"@{me.username}" in event.raw_text
    if event.is_private or is_mention:
        sender_id = event.sender_id
        if sender_id is None or sender_id == me.id or sender_id in afk_state["replied"]:
            return
        afk_state["replied"].add(sender_id)
        since = afk_state["since"].strftime("%H:%M")
        await event.reply(
            f"😴 در حال حاضر AFK هستم (از ساعت {since})\nدلیل: {afk_state['reason']}"
        )


# ---------------------------------------------------------------------------
# ۸) مدیریت گروه: kick / ban / promote / demote (فقط جایی که ادمین هستید)
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat("kick", arg=False)))
async def kick_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    reply = await event.get_reply_message()
    try:
        await client.kick_participant(event.chat_id, reply.sender_id)
        await event.edit("✅ کاربر کیک شد")
    except Exception as e:
        await event.edit(f"❌ خطا: {e}")


@client.on(events.NewMessage(outgoing=True, pattern=pat("ban", arg=False)))
async def ban_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    reply = await event.get_reply_message()
    try:
        await client.edit_permissions(event.chat_id, reply.sender_id, view_messages=False)
        await event.edit("✅ کاربر بن شد")
    except Exception as e:
        await event.edit(f"❌ خطا: {e}")


@client.on(events.NewMessage(outgoing=True, pattern=pat("promote")))
async def promote_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    title = (event.pattern_match.group(1) or "Admin")[:16]
    reply = await event.get_reply_message()
    try:
        await client.edit_admin(event.chat_id, reply.sender_id, is_admin=True, title=title)
        await event.edit(f"✅ کاربر ادمین شد ({title})")
    except Exception as e:
        await event.edit(f"❌ خطا: {e}")


@client.on(events.NewMessage(outgoing=True, pattern=pat("demote", arg=False)))
async def demote_handler(event):
    if not event.is_reply:
        return await event.edit("روی پیام کاربر ریپلای کن")
    reply = await event.get_reply_message()
    try:
        await client.edit_admin(event.chat_id, reply.sender_id, is_admin=False)
        await event.edit("✅ ادمین کاربر حذف شد")
    except Exception as e:
        await event.edit(f"❌ خطا: {e}")


# ---------------------------------------------------------------------------
# ۹) بکاپ گرفتن از پیام‌های یک چت
# ---------------------------------------------------------------------------

@client.on(events.NewMessage(outgoing=True, pattern=pat("backup")))
async def backup_handler(event):
    n_str = event.pattern_match.group(1)
    n = int(n_str) if n_str and n_str.isdigit() else 100
    n = min(n, 1000)
    await event.edit(f"⏳ در حال گرفتن بکاپ {n} پیام آخر...")
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


# ---------------------------------------------------------------------------
# ۱۰) ارسال خودکار متن به گروه (autopost)
# ---------------------------------------------------------------------------

def _autopost_status_text():
    status = "روشن ✅" if autopost_state["enabled"] else "خاموش ❌"
    n = autopost_state["interval_minutes"]
    chats = autopost_state["chats"]
    if chats:
        chat_lines = "\n".join(f"   – {title} (`{cid}`)" for cid, title in chats.items())
        dest_line = f"{len(chats)} گروهِ مشخص\n{chat_lines}"
    else:
        dest_line = f"هیچ‌کدام (اول با `{PREFIX}autopost add` اضافه کن)"
    text_preview = autopost_state["text"] or "(تنظیم نشده)"
    return (
        "🔁 **ارسال خودکار متن**\n\n"
        f"• وضعیت: {status}\n"
        f"• فاصله: {n} دقیقه\n"
        f"• گروه‌های مقصد: {dest_line}\n"
        f"• متن: {text_preview}\n\n"
        f"راهنما: `{PREFIX}autopost on/off` ، `{PREFIX}autopost interval <عدد>` ، "
        f"`{PREFIX}autopost text <متن>` ، `{PREFIX}autopost add/remove` ، `{PREFIX}autopost now`"
    )


@client.on(events.NewMessage(outgoing=True, pattern=pat("autopost")))
async def autopost_handler(event):
    global _autopost_force_now

    raw = (event.pattern_match.group(1) or "").strip()
    parts = raw.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if not sub:
        return await event.edit(_autopost_status_text())

    if sub == "on":
        if not autopost_state["text"]:
            return await event.edit(f"❌ اول یه متن ست کن: `{PREFIX}autopost text <متن>`")
        if not autopost_state["chats"]:
            return await event.edit(f"❌ اول حداقل یه گروه اضافه کن: `{PREFIX}autopost add` (داخل خود گروه بفرست)")
        autopost_state["enabled"] = True
        _reset_autopost_timer()
        save_autopost()
        return await event.edit(_autopost_status_text())

    if sub == "off":
        autopost_state["enabled"] = False
        save_autopost()
        return await event.edit(_autopost_status_text())

    if sub == "interval":
        if not rest.strip().isdigit():
            return await event.edit(f"مثال: `{PREFIX}autopost interval 5`")
        n = max(int(rest.strip()), AUTOPOST_MIN_INTERVAL_MINUTES)
        autopost_state["interval_minutes"] = n
        _reset_autopost_timer()
        save_autopost()
        warn = "" if n >= 5 else "\n⚠️ فاصله‌ی کمتر از ۵ دقیقه ریسک محدودیت از طرف تلگرام رو بالا می‌بره."
        return await event.edit(f"✅ فاصله روی {n} دقیقه تنظیم شد{warn}")

    if sub == "text":
        text = rest
        if not text and event.is_reply:
            reply = await event.get_reply_message()
            text = reply.raw_text or ""
        if not text:
            return await event.edit(
                f"مثال: `{PREFIX}autopost text میو` یا ریپلای روی یه پیام + `{PREFIX}autopost text`"
            )
        autopost_state["text"] = text
        save_autopost()
        return await event.edit("✅ متن ارسال خودکار ذخیره شد")

    if sub == "add":
        chat_id = int(rest.strip()) if rest.strip().lstrip("-").isdigit() else event.chat_id
        try:
            chat = await client.get_entity(chat_id)
        except Exception as e:
            return await event.edit(f"❌ خطا در پیداکردن چت: {e}")
        title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(chat_id)
        autopost_state["chats"][str(chat_id)] = title
        save_autopost()
        return await event.edit(f"✅ «{title}» به لیست مقصدها اضافه شد")

    if sub == "remove":
        chat_id = int(rest.strip()) if rest.strip().lstrip("-").isdigit() else event.chat_id
        removed = autopost_state["chats"].pop(str(chat_id), None)
        save_autopost()
        if removed:
            return await event.edit(f"🗑 «{removed}» از لیست مقصدها حذف شد")
        return await event.edit("این چت توی لیست مقصدها نبود")

    if sub == "clear":
        autopost_state["chats"].clear()
        save_autopost()
        return await event.edit("🗑 همه‌ی مقصدها پاک شدن")

    if sub == "now":
        _autopost_force_now = True
        return await event.edit("⏩ ارسال فوری توی صف قرار گرفت (تا ۵ ثانیه دیگه)")

    await event.edit(f"دستور نامعتبره. برای وضعیت کامل: `{PREFIX}autopost`")


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
                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    print(f"خطا در ارسال خودکار به {chat_id_str}:", e)
            _reset_autopost_timer()


# ---------------------------------------------------------------------------
# ۱۱) راهنما
# ---------------------------------------------------------------------------

def build_help_text():
    return f"""📋 **لیست دستورات سلف‌بات** (پیشوند: `{PREFIX}`)

**عمومی**
{PREFIX}ping — تست پینگ
{PREFIX}alive — وضعیت بات
{PREFIX}id — آیدی چت/کاربر/پیام
{PREFIX}info — اطلاعات کاربر (ریپلای اختیاری)

**ابزار**
{PREFIX}calc <عبارت> — ماشین‌حساب
{PREFIX}qr <متن> — ساخت کیو‌آر کد
{PREFIX}shorten <لینک> — کوتاه‌کردن لینک
{PREFIX}weather <شهر> — آب‌وهوا
{PREFIX}tr <زبان> <متن> — ترجمه
{PREFIX}google <عبارت> — جستجوی گوگل

**یادداشت**
{PREFIX}note <کلید> <متن> — ذخیره یادداشت
{PREFIX}notes — لیست یادداشت‌ها
{PREFIX}getnote <کلید> — نمایش یادداشت
{PREFIX}delnote <کلید> — حذف یادداشت

**مدیریت پیام**
{PREFIX}del — حذف پیام ریپلای‌شده
{PREFIX}purge <عدد> — حذف چند پیام آخر (یا ریپلای)
{PREFIX}pin / {PREFIX}unpin — پین/آنپین پیام ریپلای‌شده

**سرگرمی**
{PREFIX}write <متن> — افکت تایپ زنده
{PREFIX}type <متن> — شبیه‌سازی تایپ قبل از ارسال
{PREFIX}reverse <متن> — معکوس کردن متن
{PREFIX}mock <متن> — mOcKiNg CaSe
{PREFIX}تاس <۱ تا ۶> — انداختن تاس واقعی تا رسیدن به همون عدد

**پروفایل**
{PREFIX}setbio <متن> — تغییر بیو
{PREFIX}setname <متن> — تغییر نام پایه (زیربنای ساعت زنده)
{PREFIX}setpic — تغییر عکس پروفایل (ریپلای روی عکس)
{PREFIX}clock on/off — روشن/خاموش‌کردن ساعت زنده در نام
{PREFIX}clockstyle — لیست استایل‌های ساعت (فونت/شکل)
{PREFIX}clockstyle <نام>/next — تغییر استایل ساعت

**AFK**
{PREFIX}afk <دلیل> — فعال‌سازی AFK
{PREFIX}unafk — خروج از AFK

**مدیریت گروه** (فقط جایی که ادمین هستید)
{PREFIX}kick / {PREFIX}ban / {PREFIX}promote / {PREFIX}demote — با ریپلای روی کاربر

**دیگر**
{PREFIX}backup <عدد> — بکاپ از پیام‌های چت به Saved Messages

**ارسال خودکار متن**
{PREFIX}autopost — نمایش وضعیت کامل
{PREFIX}autopost on/off — روشن/خاموش
{PREFIX}autopost interval <دقیقه> — تنظیم فاصله
{PREFIX}autopost text <متن> — تنظیم متن (یا ریپلای)
{PREFIX}autopost add/remove — افزودن/حذف گروه فعلی از مقصدها
{PREFIX}autopost clear — پاک‌کردن همه‌ی مقصدها
{PREFIX}autopost now — ارسال فوری (تست)

{PREFIX}help — همین راهنما
"""


@client.on(events.NewMessage(outgoing=True, pattern=pat("help", arg=False)))
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
            base = (await _refresh_base_name())[:40]
            key = (clock_state["style"], base, now.hour, now.minute)
            if key != last_sent:
                clock_part = CLOCK_STYLES[clock_state["style"]](now.hour, now.minute)
                new_name = f"{base} | {clock_part}"
                try:
                    await client(functions.account.UpdateProfileRequest(first_name=new_name))
                    last_sent = key
                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    print("خطا در بروزرسانی ساعت:", e)
        # صبر تا دقیقاً لحظه‌ی شروع دقیقه‌ی بعدی (نه یک فاصله‌ی ثابت و بی‌ربط به ساعت واقعی)
        now2 = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        await asyncio.sleep(max(60 - now2.second, 1))


# ---------------------------------------------------------------------------
# اجرا
# ---------------------------------------------------------------------------

async def main():
    me = await client.get_me()
    print(f"✅ سلف‌بات با اکانت {me.first_name} روشن شد")
    client.loop.create_task(clock_updater())
    client.loop.create_task(autopost_worker())
    await client.run_until_disconnected()


with client:
    client.loop.run_until_complete(main())
