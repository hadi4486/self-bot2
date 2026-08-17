"""
این اسکریپت رو فقط یک‌بار اجرا کن تا Session String بگیری.
بعدش مقدارش رو توی .env (یا Replit Secrets) با نام SESSION_STRING ذخیره کن
تا سلف‌بات دیگه نیازی به لاگین دوباره نداشته باشه.

اجرا: python generate_session.py
موقع اجرا شماره تلفن و کد تاییدی که تلگرام برات می‌فرسته رو وارد کن.
"""
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\n✅ لاگین موفق بود.\n")
    print("این Session String شماست - آن را در .env در متغیر SESSION_STRING قرار دهید:\n")
    print(client.session.save())
    print("\n⚠️ این رشته معادل رمز عبور اکانتته - جایی به اشتراک نذارش.")
