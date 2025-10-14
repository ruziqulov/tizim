#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yordam
Professional Attendance Bot (Full, Robust, Single File)
Author: Copilot (for ruziqulov)
Description:
 - Admin-only Telegram bot for school attendance
 - pyTelegramBotAPI, dotenv, JSON persistence
 - All buttons (orqaga, reports, group selection, etc.) work correctly
 - Robust flow, unique callback prefixes, strong state handling
 - All commands (/start, /cancel, /admins, /backup, /restore_from, /sample, /set_log_chat) work
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta, date
from functools import wraps
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv
import telebot
from telebot import types

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("attendance_bot")

# --- ENV ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not set in .env")
    sys.exit(1)

ADMIN_IDS = []
RAW_ADMINS = os.getenv("ADMIN_IDS", "")
if RAW_ADMINS:
    for p in RAW_ADMINS.split(","):
        try:
            ADMIN_IDS.append(int(p.strip()))
        except Exception:
            logger.warning("Invalid ADMIN_IDS entry ignored: %r", p)

DB_FILE = os.getenv("DB_FILE", "db.json")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

TEMP: Dict[int, Dict[str, Any]] = {}

# --- Helpers ---
def is_admin(uid): return uid in ADMIN_IDS

def admin_only_message(func):
    @wraps(func)
    def wrapper(m, *a, **k):
        if not is_admin(m.from_user.id):
            safe_send(m.from_user.id, "Bu bot faqat adminlar uchun!")
            return
        return func(m, *a, **k)
    return wrapper

def admin_only_callback(func):
    @wraps(func)
    def wrapper(call, *a, **k):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "Bu tugma faqat adminlarga!", show_alert=True)
            return
        return func(call, *a, **k)
    return wrapper

def safe_send(chat_id, text, reply_markup=None):
    try: bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception: logger.exception("safe_send failed")

def safe_edit(chat_id, message_id, text, reply_markup=None):
    try: bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except Exception:
        logger.exception("safe_edit fallback send_message")
        try: bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception: logger.exception("safe_edit fallback failed")

def safe_edit_reply_markup(chat_id, message_id, reply_markup):
    try: bot.edit_message_reply_markup(chat_id, message_id, reply_markup=reply_markup)
    except Exception: logger.exception("safe_edit_reply_markup failed")

def encode_cb(s): return str(s).replace("%","%%").replace(" ","_~_").replace("\n","__nl__")
def decode_cb(s): return s.replace("__nl__","\n").replace("_~_"," ").replace("%%","%")

# --- DB ---
def ensure_db():
    if not os.path.exists(DB_FILE):
        base = {
            "groups": {},
            "attendance": {},
            "settings": {"log_chat_id": None},
            "meta": {"created": datetime.now().isoformat()}
        }
        with open(DB_FILE,"w",encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)

def load_db():
    ensure_db()
    with open(DB_FILE,"r",encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE,"w",encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def backup_db():
    try:
        ensure_db()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = os.path.join(BACKUP_DIR, f"db_backup_{ts}.json")
        with open(DB_FILE, "r", encoding="utf-8") as src, open(fname, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        logger.info("Backup created: %s", fname)
        return fname
    except Exception:
        logger.exception("backup failed")
        return None

def restore_db(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        save_db(data)
        logger.info("DB restored from %s", file_path)
        return True
    except Exception:
        logger.exception("restore failed")
        return False

def get_groups(): return load_db().get("groups", {})

def add_group(name, students, code):
    db = load_db()
    db.setdefault("groups", {})
    db["groups"][name] = {"code": code, "students": students}
    save_db(db)
    logger.info("Group added: %s (%d)", name, len(students))

def update_group(name, students=None, code=None):
    db = load_db()
    db.setdefault("groups", {})
    if name not in db["groups"]: db["groups"][name] = {}
    if students is not None: db["groups"][name]["students"] = students
    if code is not None: db["groups"][name]["code"] = code
    save_db(db)
    logger.info("Group updated: %s", name)

def delete_group(name):
    db = load_db()
    if "groups" in db and name in db["groups"]:
        del db["groups"][name]
        save_db(db)
        logger.info("Group deleted: %s", name)
        return True
    return False

def get_settings(): return load_db().get("settings", {})

def set_log_chat(chat_id):
    db = load_db()
    db.setdefault("settings", {})
    db["settings"]["log_chat_id"] = chat_id
    save_db(db)
    logger.info("Log chat set to %s", chat_id)

def clear_log_chat():
    db = load_db()
    db.setdefault("settings", {})
    db["settings"]["log_chat_id"] = None
    save_db(db)
    logger.info("Log chat cleared")

def record_attendance(date_key, group, para, status_map, recorder):
    db = load_db()
    db.setdefault("attendance", {})
    db["attendance"].setdefault(date_key, [])
    present = [s for s, st in status_map.items() if st == "present"]
    sababsiz = [s for s, st in status_map.items() if st == "sababsiz"]
    sababli = [s for s, st in status_map.items() if st == "sababli"]
    rec = {
        "group": group,
        "para": para,
        "present": present,
        "sababsiz": sababsiz,
        "sababli": sababli,
        "status_map": status_map,
        "recorder": recorder,
        "timestamp": datetime.now().isoformat()
    }
    db["attendance"][date_key].append(rec)
    save_db(db)
    logger.info("Attendance saved for %s on %s para=%s", group, date_key, para)

def get_attendance_by_date(date_key): return load_db().get("attendance", {}).get(date_key, [])

def get_attendance_in_range(start_date, end_date):
    attendance = load_db().get("attendance", {})
    out = []
    s = datetime.strptime(start_date, "%Y-%m-%d").date()
    e = datetime.strptime(end_date, "%Y-%m-%d").date()
    cur = s
    while cur <= e:
        out.extend(attendance.get(cur.strftime("%Y-%m-%d"), []))
        cur += timedelta(days=1)
    return out

# --- Ensure Sample Group ---
def ensure_sample():
    groups = get_groups()
    if "Avto 13-24" not in groups:
        add_group("Avto 13-24",
            [
                "Abdulatiboyev Hasanjon","Abdurashidov Behruzbek","Abdug‘amuev Sunnatillo",
                "Adhamjonov Ibrohim","G‘ofurjonov Boburjon","Qaxxarov","Komilov Mohirjon",
                "Mirazimov Azamat","Mirzayev Muhammadamin","Nigmonov Alisherbek",
                "Numonjonov Abdulloh","Obidov Abdulmalik","Oblakulov Jahongir",
                "O‘ngarov Muhriddin","Ro‘ziqulov Zoyirjon","Sadullayev Jasur",
                "Sayidbekov Mo‘min","Shagijyev Tursunjon","Sharipov Umar",
                "To‘laganov Faxriddin","Turg‘unjonov Abdulloh","Urokov Umidjon",
                "Valiyev Abdurasil","Xudoyqulov Sayfulloh","Xolmurodov Azizbek","Zokirov Bobur",
            ], "2025")
        logger.info("Sample group Avto 13-24 added")
ensure_sample()

# ----------- UI Keyboards -----------
def main_start_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📝 Davomat olish", callback_data="menu_attend"),
        types.InlineKeyboardButton("📊 Hisobotlar", callback_data="menu_reports"),
        types.InlineKeyboardButton("ℹ️ Yordam", callback_data="menu_help")
    )
    return kb

def attend_group_kb():
    groups = get_groups()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for g in sorted(groups.keys()):
        kb.add(types.InlineKeyboardButton(f"👥 {g}", callback_data=f"attend_select_group::{encode_cb(g)}"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_main"))
    return kb

def student_kb(group, status_map):
    students = get_groups()[group]["students"]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for s in students:
        st = status_map.get(s, "present")
        label = "✅ " + s if st == "present" else ("❌ " + s if st == "sababsiz" else "⚠️ " + s)
        kb.add(types.InlineKeyboardButton(label, callback_data=f"toggle_student::{encode_cb(s)}"))
    kb.row(
        types.InlineKeyboardButton("Barchasini kelgan deb belgilash", callback_data="bulk::present"),
        types.InlineKeyboardButton("Barchasini kelmagan deb belgilash", callback_data="bulk::sababsiz")
    )
    kb.row(
        types.InlineKeyboardButton("✅ Tasdiqlash", callback_data="confirm_students"),
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_attend_groups")
    )
    return kb

def para_kb():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("1-para", callback_data="para::1"),
        types.InlineKeyboardButton("2-para", callback_data="para::2"),
        types.InlineKeyboardButton("3-para", callback_data="para::3"),
        types.InlineKeyboardButton("4-para", callback_data="para::4"),
        types.InlineKeyboardButton("Butun kun", callback_data="para::all"),
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_attend_students")
    )
    return kb

def final_confirm_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Tasdiqlash", callback_data="final_confirm"),
        types.InlineKeyboardButton("❌ Bekor qilish", callback_data="final_cancel"),
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_attend_para")
    )
    return kb

def reports_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📅 Kunlik", callback_data="report::daily"),
        types.InlineKeyboardButton("📆 Haftalik", callback_data="report::weekly"),
        types.InlineKeyboardButton("🗓️ Oylik", callback_data="report::monthly"),
        types.InlineKeyboardButton("📈 Yillik", callback_data="report::yearly"),
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_main")
    )
    return kb

def report_group_kb():
    groups = get_groups()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for g in sorted(groups.keys()):
        kb.add(types.InlineKeyboardButton(f"👥 {g}", callback_data=f"report_select_group::{encode_cb(g)}"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_reports"))
    return kb

def month_kb():
    months = [
        ("Yanvar", 1), ("Fevral", 2), ("Mart", 3), ("Aprel", 4), ("May", 5),
        ("Sentabr", 9), ("Oktabr", 10), ("Noyabr", 11), ("Dekabr", 12)
    ]
    kb = types.InlineKeyboardMarkup(row_width=3)
    for name, idx in months:
        kb.add(types.InlineKeyboardButton(name, callback_data=f"month::{idx}"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_report_group"))
    return kb

START_TEXT = (
    "🏫 Avto 13-24 Guruhi Davomat Tizimiga Xush Kelibsiz!\n"
    "Assalomu alaykum, barcha o'quvchilar!\n"
    "Bu bot orqali siz quyidagi amallarni bajarishingiz mumkin:\n"
)

# --- Handlers: Start/Main ---
@bot.message_handler(commands=["start"])
def cmd_start(m: types.Message):
    if is_admin(m.from_user.id):
        safe_send(m.from_user.id, START_TEXT, reply_markup=main_start_kb())
    else:
        safe_send(m.from_user.id, "Bu bot faqat adminlar uchun!")

@bot.callback_query_handler(func=lambda c: c.data == "menu_help")
@admin_only_callback
def cb_help(call):
    bot.answer_callback_query(call.id)
    text = (
    "ℹ️ **Yordam: Davomat Botidan Foydalanish Qo‘llanmasi** ℹ️\n\n"
    "🔹 **Bot haqida**\n"
    "Ushbu bot Politexnikumdagi Avto 13–24 guruhi uchun davomatni tez, qulay va aniq yuritish uchun mo‘ljallangan. "
    "Barcha ma’lumotlar xavfsiz saqlanadi, hisobotlar esa avtomatik shakllantiriladi.\n\n"
    "🔹 **Asosiy funksiyalar**\n"
    "• Guruh tanlash va davomat olish (✅ Keldi, ❌ Kelmadi, ⚠️ Sababli)\n"
    "• Bulk tugmalari bilan barchani bir bosishda belgilash\n"
    "• Kunlik, haftalik, oylik va yillik hisobotlar\n"
    "• Davomat natijasini log chatga yuborish\n"
    "• Ma’lumotlarni /backup va /restore_from orqali zaxiralash yoki tiklash\n\n"
    "🔹 **Foydalanish tartibi**\n"
    "1️⃣ /start buyrug‘ini yuboring\n"
    "2️⃣ “📝 Davomat olish” tugmasini bosing\n"
    "3️⃣ Guruhni tanlang (Avto 13–24)\n"
    "4️⃣ Talabalar holatini belgilang va “✅ Tasdiqlash”ni bosing\n"
    "5️⃣ Para (dars)ni tanlang va yakuniy natijani tasdiqlang\n"
    "📊 Natijalar avtomatik saqlanadi va log chatga yuboriladi\n\n"
    "🔹 **Admin va foydalanuvchilar**\n"
    "Botdan faqat adminlar foydalanishi mumkin. Adminlar ro‘yxatini /admins orqali ko‘rish mumkin. "
    "Adminlar guruhi va log chat sozlamalari /set_log_chat orqali o‘rnatiladi.\n\n"
    "🔹 **Xatoliklar va savollar**\n"
    "- Tugmalar ishlamasa, /cancel yuboring va qayta boshlang\n"
    "- Zarurat bo‘lsa, botni qayta ishga tushiring\n"
    "- Savollar uchun: @Z_Ruziqulovv\n\n"
    "🔹 **Qo‘shimcha**\n"
    "• Ma’lumotlar JSON faylda saqlanadi\n"
    "• Hisobotlar to‘liq va oson o‘qiladigan formatda\n"
    "• Orqaga (⬅️) tugmalari har doim to‘g‘ri ishlaydi\n\n"
    "✅ **Botdan foydalanishda muammo bo‘lsa yoki yangi imkoniyatlar kerak bo‘lsa, dasturchiga murojaat qiling.**"
)


    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=main_start_kb())

# --- Flow: Attend ---
@bot.callback_query_handler(func=lambda c: c.data == "menu_attend")
@admin_only_callback
def cb_menu_attend(call):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Guruhni tanlang:", reply_markup=attend_group_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("attend_select_group::"))
@admin_only_callback
def cb_attend_select_group(call):
    bot.answer_callback_query(call.id)
    group = decode_cb(call.data.split("::",1)[1])
    students = get_groups()[group]["students"]
    status_map = {s: "present" for s in students}
    TEMP[call.from_user.id] = {"flow": "attend", "group": group, "status_map": status_map}
    safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {group}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=student_kb(group, status_map))

@bot.callback_query_handler(func=lambda c: c.data == "back_attend_groups")
@admin_only_callback
def cb_back_attend_groups(call):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Guruhni tanlang:", reply_markup=attend_group_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("toggle_student::"))
@admin_only_callback
def cb_toggle_student(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend": return
    student = decode_cb(call.data.split("::",1)[1])
    current = state["status_map"].get(student, "present")
    nxt = "sababsiz" if current == "present" else "sababli" if current == "sababsiz" else "present"
    state["status_map"][student] = nxt
    kb = student_kb(state["group"], state["status_map"])
    safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {state['group']}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)
    bot.answer_callback_query(call.id, f"{student}: {nxt}")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("bulk::"))
@admin_only_callback
def cb_bulk(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    action = call.data.split("::",1)[1]
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend": return
    students = get_groups()[state["group"]]["students"]
    status_map = {s: "present" if action=="present" else "sababsiz" for s in students}
    state["status_map"] = status_map
    kb = student_kb(state["group"], status_map)
    safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {state['group']}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)
    bot.answer_callback_query(call.id, "Barcha holatlar yangilandi.")

@bot.callback_query_handler(func=lambda c: c.data == "confirm_students")
@admin_only_callback
def cb_confirm_students(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend": return
    safe_edit(call.message.chat.id, call.message.message_id, "Nechanchi para uchun davomat?", reply_markup=para_kb())

@bot.callback_query_handler(func=lambda c: c.data == "back_attend_students")
@admin_only_callback
def cb_back_attend_students(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state: return
    kb = student_kb(state["group"], state["status_map"])
    safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {state['group']}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("para::"))
@admin_only_callback
def cb_para(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend": return
    para = call.data.split("::",1)[1]
    group = state["group"]
    status_map = state["status_map"]
    present = [s for s,st in status_map.items() if st == "present"]
    sababsiz = [s for s,st in status_map.items() if st == "sababsiz"]
    sababli = [s for s,st in status_map.items() if st == "sababli"]
    hr_date = datetime.now().strftime("%d-%B, %Y")
    para_label = "Butun kun" if para == "all" else f"{para}-para"
    preview = (
        "📝 Davomat Tasdiqlash\n\n"
        f"🏫 Sinf: {group}\n"
        f"⏰ Soat: ({para_label})\n"
        f"📅 Sana: {hr_date}\n\n"
        f"👥 Darsga Kirmagan o'quvchilar ({len(sababsiz)+len(sababli)} ta):\n"
    )
    if sababsiz:
        preview += "\nSababsiz:\n" + "\n".join(f"- {p}" for p in sababsiz) + "\n"
    if sababli:
        preview += "\nSababli:\n" + "\n".join(f"- {p}" for p in sababli) + "\n"
    preview += "\nTasdiqlaysizmi?"
    state["selected_para"] = para
    state["preview"] = preview
    safe_edit(call.message.chat.id, call.message.message_id, preview, reply_markup=final_confirm_kb())

@bot.callback_query_handler(func=lambda c: c.data == "back_attend_para")
@admin_only_callback
def cb_back_attend_para(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state: return
    safe_edit(call.message.chat.id, call.message.message_id, "Nechanchi para uchun davomat?", reply_markup=para_kb())

@bot.callback_query_handler(func=lambda c: c.data == "final_cancel")
@admin_only_callback
def cb_final_cancel(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    if uid in TEMP:
        state = TEMP[uid]
        kb = student_kb(state["group"], state["status_map"])
        safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {state['group']}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "final_confirm")
@admin_only_callback
def cb_final_confirm(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state: return
    group = state["group"]
    status_map = state["status_map"]
    para = state.get("selected_para", "all")
    date_key = datetime.now().strftime("%Y-%m-%d")
    user = call.from_user
    recorder = {
        "id": user.id,
        "name": (user.first_name or "") + (" " + user.last_name if user.last_name else ""),
        "username": user.username or ""
    }
    record_attendance(date_key, group, para, status_map, recorder)
    present = [s for s,st in status_map.items() if st == "present"]
    sababsiz = [s for s,st in status_map.items() if st == "sababsiz"]
    sababli = [s for s,st in status_map.items() if st == "sababli"]
    hr_date = datetime.now().strftime("%d-%B, %Y")
    para_label = "Butun kun" if para == "all" else f"{para}-para"
    final = (
        f"🏫 Sinf: {group}\n"
        f"⏰ Soat: {para_label}\n"
        f"📅 Sana: {hr_date}\n\n"
        f"👥 Sababsiz o'quvchilar ({len(sababsiz)} ta):\n"
        + ("\n".join(f"- {s}" for s in sababsiz) if sababsiz else "—") +
        "\n\n👥 Sababli o'quvchilar (" + str(len(sababli)) + " ta):\n" +
        ("\n".join(f"- {s}" for s in sababli) if sababli else "—") +
        f"\n\n✅ Jami kelganlar: {len(present)}\n\n"
    )
    final += f"Davomat @{recorder.get('username','')} tomonidan olindi.\n\n"
    if para != "all":
        final += f"⚠️ Ushbu o'quvchilar faqat {para_label} darsiga kelishmadi! ⚠️"
    else:
        final += "⚠️ Ushbu o'quvchilar darsga kelishmadi! ⚠️"
    settings = get_settings()
    log_chat = settings.get("log_chat_id")
    try:
        if log_chat: bot.send_message(log_chat, final)
        else: safe_send(uid, final)
    except Exception:
        logger.exception("Failed to send final message to log or admin")
        safe_send(uid, final)
    TEMP.pop(uid, None)
    safe_send(uid, "Asosiy menyu:", reply_markup=main_start_kb())

# --- Flow: Reports ---
@bot.callback_query_handler(func=lambda c: c.data == "menu_reports")
@admin_only_callback
def cb_menu_reports(call):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Hisobotlar — tanlang:", reply_markup=reports_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("report::"))
@admin_only_callback
def cb_report_type(call):
    bot.answer_callback_query(call.id)
    mode = call.data.split("::",1)[1]
    TEMP[call.from_user.id] = {"flow": "report", "mode": mode}
    safe_edit(call.message.chat.id, call.message.message_id, "Guruhni tanlang:", reply_markup=report_group_kb())

@bot.callback_query_handler(func=lambda c: c.data == "back_reports")
@admin_only_callback
def cb_back_reports(call):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Hisobotlar — tanlang:", reply_markup=reports_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("report_select_group::"))
@admin_only_callback
def cb_report_select_group(call):
    bot.answer_callback_query(call.id)
    group = decode_cb(call.data.split("::",1)[1])
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "report": return
    mode = state.get("mode")
    if mode=="daily":
        day = datetime.now().strftime("%Y-%m-%d")
        arr = get_attendance_by_date(day)
        arr_group = [r for r in arr if r.get("group")==group]
        if not arr_group:
            safe_edit(call.message.chat.id, call.message.message_id, f"{day} uchun {group} bo'yicha davomat topilmadi.")
            TEMP.pop(uid, None)
            return
        lines = [f"📅 Davomat — {day} — {group}"]
        for rec in arr_group:
            lines.append(f"\n📚 Para: {rec.get('para')}")
            lines.append("✅ Kelganlar ("+str(len(rec.get("present",[])))+"): "+(", ".join(rec.get("present",[])) if rec.get("present") else "—"))
            lines.append("❌ Sababsiz ("+str(len(rec.get("sababsiz",[])))+"): "+(", ".join(rec.get("sababsiz",[])) if rec.get("sababsiz") else "—"))
        safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
        TEMP.pop(uid, None)
    elif mode=="weekly":
        end = datetime.now().date()
        start = end-timedelta(days=6)
        arr = get_attendance_in_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        arr_group = [r for r in arr if r.get("group")==group]
        if not arr_group:
            safe_edit(call.message.chat.id, call.message.message_id, f"{start}-{end}: {group} uchun davomat topilmadi.")
            TEMP.pop(uid, None)
            return
        lines = [f"📅 Davomat — {start} — {end} — {group}"]
        for rec in arr_group:
            lines.append(f"\n📆 {rec.get('timestamp','')} — Para: {rec.get('para')}")
            lines.append("✅ Kelganlar ("+str(len(rec.get("present",[])))+"): "+(", ".join(rec.get("present",[])) if rec.get("present") else "—"))
            lines.append("❌ Sababsiz ("+str(len(rec.get("sababsiz",[])))+"): "+(", ".join(rec.get("sababsiz",[])) if rec.get("sababsiz") else "—"))
        safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
        TEMP.pop(uid, None)
    elif mode=="monthly":
        TEMP[uid]["group_for_report"] = group
        safe_edit(call.message.chat.id, call.message.message_id, "Oyni tanlang:", reply_markup=month_kb())
    elif mode=="yearly":
        year = datetime.now().year
        start = date(year,1,1)
        end = date(year,12,31)
        arr = get_attendance_in_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        arr_group = [r for r in arr if r.get("group")==group]
        if not arr_group:
            safe_edit(call.message.chat.id, call.message.message_id, f"{year} uchun {group} bo'yicha davomat topilmadi.")
            TEMP.pop(uid, None)
            return
        lines = [f"📈 Yillik Davomat — {year} — {group}"]
        for rec in arr_group:
            lines.append(f"\n📆 {rec.get('timestamp','')} — Para: {rec.get('para')}")
            lines.append("✅ Kelganlar ("+str(len(rec.get("present",[])))+"): "+(", ".join(rec.get("present",[])) if rec.get("present") else "—"))
        safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
        TEMP.pop(uid, None)

@bot.callback_query_handler(func=lambda c: c.data == "back_report_group")
@admin_only_callback
def cb_back_report_group(call):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Guruhni tanlang:", reply_markup=report_group_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("month::"))
@admin_only_callback
def cb_month(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow")!="report" or state.get("mode")!="monthly": return
    month_idx = int(call.data.split("::",1)[1])
    group = state.get("group_for_report")
    year = datetime.now().year
    start = date(year, month_idx, 1)
    end = date(year, month_idx+1, 1)-timedelta(days=1) if month_idx!=12 else date(year,12,31)
    arr = get_attendance_in_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    arr_group = [r for r in arr if r.get("group")==group]
    if not arr_group:
        safe_edit(call.message.chat.id, call.message.message_id, f"{start.strftime('%B %Y')} uchun {group} bo'yicha davomat topilmadi.")
        TEMP.pop(uid, None)
        return
    lines = [f"📅 Oylik Davomat — {start.strftime('%B %Y')} — {group}"]
    for rec in arr_group:
        lines.append(f"\n📆 {rec.get('timestamp','')} — Para: {rec.get('para')}")
        lines.append("✅ Kelganlar ("+str(len(rec.get("present",[])))+"): "+(", ".join(rec.get("present",[])) if rec.get("present") else "—"))
        lines.append("❌ Sababsiz ("+str(len(rec.get("sababsiz",[])))+"): "+(", ".join(rec.get("sababsiz",[])) if rec.get("sababsiz") else "—"))
    safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
    TEMP.pop(uid, None)

# --- All 'back' to main ---
@bot.callback_query_handler(func=lambda c: c.data == "back_main")
@admin_only_callback
def cb_back_main(call):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Asosiy menyu:", reply_markup=main_start_kb())

# --- Admin Commands ---
@bot.message_handler(commands=["set_log_chat"])
@admin_only_message
def cmd_set_log_chat(m: types.Message):
    if m.chat.type in ("group","supergroup","channel"):
        set_log_chat(m.chat.id)
        bot.reply_to(m, f"This chat ({m.chat.title or m.chat.id}) will receive attendance summaries.")
    else:
        parts = m.text.split()
        if len(parts)>=2:
            try:
                tid = int(parts[1])
                set_log_chat(tid)
                bot.reply_to(m, f"Log chat set to id {tid}")
            except Exception:
                bot.reply_to(m, "Iltimos chat id raqam formatida kiriting.")
        else:
            bot.reply_to(m, "Guruhda yuboring yoki: /set_log_chat <chat_id>")

@bot.message_handler(commands=["get_log_chat"])
@admin_only_message
def cmd_get_log_chat(m: types.Message):
    lc = get_settings().get("log_chat_id")
    bot.reply_to(m, f"Current log chat id: {lc}")

@bot.message_handler(commands=["clear_log_chat"])
@admin_only_message
def cmd_clear_log_chat(m: types.Message):
    clear_log_chat()
    bot.reply_to(m, "Log chat cleared.")

@bot.message_handler(commands=["backup"])
@admin_only_message
def cmd_backup(m: types.Message):
    path = backup_db()
    if path:
        try:
            with open(path,"rb") as f: bot.send_document(m.chat.id, f, caption="DB backup")
            bot.reply_to(m, "Backup yaratildi va yuborildi.")
        except Exception:
            logger.exception("send backup failed")
            bot.reply_to(m, "Backup yaratildi, ammo yuborishda xato.")
    else:
        bot.reply_to(m, "Backup yaratishda xato.")

@bot.message_handler(commands=["restore_from"])
@admin_only_message
def cmd_restore_from(m: types.Message):
    parts = m.text.split()
    if len(parts)<2:
        bot.reply_to(m, "Foydalanish: /restore_from <backup_filename>")
        return
    fname = parts[1].strip()
    path = os.path.join(BACKUP_DIR, fname)
    if not os.path.exists(path):
        bot.reply_to(m, "Backup topilmadi.")
        return
    ok = restore_db(path)
    bot.reply_to(m, "DB muvaffaqiyatli tiklandi." if ok else "DB tiklashda xato.")

@bot.message_handler(commands=["sample"])
@admin_only_message
def cmd_sample(m: types.Message):
    try:
        add_group("Demo Group A", [f"Demo Student {i}" for i in range(1,21)], "1111")
        add_group("Demo Group B", [f"DemoB Student {i}" for i in range(1,16)], "2222")
        bot.reply_to(m, "Sample groups added.")
    except Exception:
        logger.exception("sample failed")
        bot.reply_to(m, "Sample creation failed.")

@bot.message_handler(commands=["list_groups"])
@admin_only_message
def cmd_list_groups(m: types.Message):
    groups = get_groups()
    if not groups:
        bot.reply_to(m, "Guruhlar topilmadi.")
        return
    lines = ["Guruhlar:"]
    for name,v in sorted(groups.items()):
        lines.append(f"- {name} ({len(v.get('students',[]))} students) code: {v.get('code','')}")
    bot.reply_to(m, "\n".join(lines))

@bot.message_handler(commands=["cancel"])
def cmd_cancel(m: types.Message):
    uid = m.from_user.id
    if uid in TEMP:
        TEMP.pop(uid, None)
        bot.reply_to(m, "Jarayon bekor qilindi.")
    else:
        bot.reply_to(m, "Hech qanday jarayon yo'q.")

@bot.message_handler(commands=["admins"])
def cmd_admins(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "Bu komanda faqat adminlarga.")
        return
    bot.reply_to(m, f"Admins: {ADMIN_IDS}")

# --- Fallback ---
@bot.message_handler(func=lambda m: True)
def fallback(m: types.Message):
    if is_admin(m.from_user.id):
        safe_send(m.from_user.id, "Admin panel:\n/start — bosh menyu\n/help — yordam", reply_markup=main_start_kb())
    else:
        safe_send(m.from_user.id, "Bu bot faqat adminlar uchun!")

# --- Polling ---
def run():
    logger.info("Attendance Bot starting...")
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty; set ADMIN_IDS in .env")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception:
            logger.exception("Polling crashed; restarting in 5s")
            time.sleep(5)

if __name__ == "__main__":
    run()