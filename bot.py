#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Professional Attendance Bot - Complete single-file implementation
Author: Generated for the user (ruziqulov)
Description:
 - Admin-only Telegram bot for school attendance using pyTelegramBotAPI (telebot)
 - JSON persistence (db.json) for groups, attendance, and settings
 - Prepopulated sample group "Avto 13-24"
 - Numeric keypad for code entry (0-9, backspace, submit)
 - Student status toggles (present / sababsiz / sababli) with simple cycle
 - Bulk controls: mark all present / mark all absent
 - Confirmation preview, para (lesson) selection, final confirmation
 - Final attendance summary saved to JSON and optionally sent to a configured group chat
 - Reports: daily / weekly / monthly / yearly (month selection excludes Jun/Jul/Aug)
 - Backup/restore, sample data, and admin commands
 - Robust error handling; no global Markdown parsing to avoid "can't parse entities" errors
 - Use .env for BOT_TOKEN and ADMIN_IDS (comma separated)
Requirements:
 - pip install pyTelegramBotAPI python-dotenv
Start:
 - Create .env with BOT_TOKEN and ADMIN_IDS
 - Run: python bot.py
"""

import os
import sys
import json
import csv
import time
import logging
from datetime import datetime, timedelta, date
from functools import wraps
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv
import telebot
from telebot import types

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("attendance_bot")

# -------------------------
# Load env
# -------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not set in .env")
    raise RuntimeError("BOT_TOKEN not set in .env")

RAW_ADMINS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: List[int] = []
if RAW_ADMINS:
    for p in RAW_ADMINS.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            ADMIN_IDS.append(int(p))
        except Exception:
            logger.warning("Invalid ADMIN_IDS entry ignored: %r", p)

DB_FILE = os.getenv("DB_FILE", "db.json")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# -------------------------
# Bot init (no global parse_mode)
# -------------------------
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# -------------------------
# TEMP state for admins (per-admin ephemeral)
# -------------------------
TEMP: Dict[int, Dict[str, Any]] = {}

# -------------------------
# Utility helpers
# -------------------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def admin_only_message(func):
    @wraps(func)
    def wrapper(m, *a, **k):
        uid = m.from_user.id
        if not is_admin(uid):
            try:
                bot.send_message(uid, "Bu bot faqat adminlar uchun. Iltimos, admin bilan bog'laning.")
            except Exception:
                logger.exception("Failed to inform non-admin")
            return
        return func(m, *a, **k)
    return wrapper

def admin_only_callback(func):
    @wraps(func)
    def wrapper(call, *a, **k):
        uid = call.from_user.id
        if not is_admin(uid):
            try:
                bot.answer_callback_query(call.id, "Bu tugma faqat adminlarga ochiq.", show_alert=True)
            except Exception:
                logger.exception("answer_callback_query failed")
            return
        return func(call, *a, **k)
    return wrapper

def safe_send(chat_id: int, text: str, reply_markup: Optional[types.InlineKeyboardMarkup] = None):
    try:
        bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception("safe_send failed")

def safe_edit(chat_id: int, message_id: int, text: str, reply_markup: Optional[types.InlineKeyboardMarkup] = None):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except Exception:
        logger.exception("safe_edit failed; falling back to send_message")
        try:
            bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            logger.exception("fallback send failed")

def safe_edit_reply_markup(chat_id: int, message_id: int, reply_markup: Optional[types.InlineKeyboardMarkup]):
    try:
        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=reply_markup)
    except Exception:
        logger.exception("safe_edit_reply_markup failed")

def encode_cb(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    return s.replace("%", "%%").replace(" ", "_~_").replace("\n", "__nl__")

def decode_cb(s: str) -> str:
    return s.replace("__nl__", "\n").replace("_~_", " ").replace("%%", "%")

# -------------------------
# DB helpers (JSON)
# -------------------------
def ensure_db():
    if not os.path.exists(DB_FILE):
        base = {
            "groups": {},
            "attendance": {},
            "settings": {"log_chat_id": None},
            "meta": {"created": datetime.now().isoformat()}
        }
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)

def load_db() -> Dict[str, Any]:
    ensure_db()
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db: Dict[str, Any]):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def backup_db() -> Optional[str]:
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

def restore_db(file_path: str) -> bool:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        save_db(data)
        logger.info("DB restored from %s", file_path)
        return True
    except Exception:
        logger.exception("restore failed")
        return False

# High-level DB operations
def get_groups() -> Dict[str, Any]:
    db = load_db()
    return db.get("groups", {})

def add_group(name: str, students: List[str], code: str):
    db = load_db()
    db.setdefault("groups", {})
    db["groups"][name] = {"code": code, "students": students}
    save_db(db)
    logger.info("Group added: %s (%d)", name, len(students))

def update_group(name: str, students: Optional[List[str]] = None, code: Optional[str] = None):
    db = load_db()
    db.setdefault("groups", {})
    if name not in db["groups"]:
        db["groups"][name] = {}
    if students is not None:
        db["groups"][name]["students"] = students
    if code is not None:
        db["groups"][name]["code"] = code
    save_db(db)
    logger.info("Group updated: %s", name)

def delete_group(name: str) -> bool:
    db = load_db()
    if "groups" in db and name in db["groups"]:
        del db["groups"][name]
        save_db(db)
        logger.info("Group deleted: %s", name)
        return True
    return False

def get_settings() -> Dict[str, Any]:
    db = load_db()
    return db.get("settings", {})

def set_log_chat(chat_id: int):
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

def record_attendance(date_key: str, group: str, para: str, status_map: Dict[str, str], recorder: Dict[str, str]):
    db = load_db()
    db.setdefault("attendance", {})
    db["attendance"].setdefault(date_key, [])
    # Normalize
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
    logger.info("Attendance saved for %s on %s para=%s by %s", group, date_key, para, recorder.get("name"))

def get_attendance_by_date(date_key: str) -> List[Dict[str, Any]]:
    db = load_db()
    return db.get("attendance", {}).get(date_key, [])

def get_attendance_in_range(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    db = load_db()
    attendance = db.get("attendance", {})
    out = []
    s = datetime.strptime(start_date, "%Y-%m-%d").date()
    e = datetime.strptime(end_date, "%Y-%m-%d").date()
    cur = s
    while cur <= e:
        out.extend(attendance.get(cur.strftime("%Y-%m-%d"), []))
        cur += timedelta(days=1)
    return out

# -------------------------
# Ensure sample group "Avto 13-24"
# -------------------------
def ensure_sample():
    groups = get_groups()
    if "Avto 13-24" not in groups:
        students = [
    "Abdulatiboyev Hasanjon","Abdurashidov Behruzbek","Abdug‘amuev Sunnatillo",
    "Adhamjonov Ibrohim","G‘ofurjonov Boburjon","Qaxxarov","Komilov Mohirjon",
    "Mirazimov Azamat","Mirzayev Muhammadamin","Nigmonov Alisherbek",
    "Numonjonov Abdulloh","Obidov Abdulmalik","Oblakulov Jahongir",
    "O‘ngarov Muhriddin","Ro‘ziqulov Zoyirjon","Sadullayev Jasur",
    "Sayidbekov Mo‘min","Shagijyev Tursunjon","Sharipov Umar",
    "To‘laganov Faxriddin","Turg‘unjonov Abdulloh","Urokov Umidjon",
    "Valiyev Abdurasil","Xudoyqulov Sayfulloh","Xolmurodov Azizbek","Zokirov Bobur",
  ]
        add_group("Avto 13-24", students, "2025")
        logger.info("Sample group Avto 13-24 added")

ensure_sample()

# -------------------------
# UI: keyboards
# -------------------------
def main_start_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📝 Davomat olish", callback_data="menu_davomat"),
        types.InlineKeyboardButton("📊 Hisobotlar", callback_data="menu_reports"),
        types.InlineKeyboardButton("ℹ️ Yordam", callback_data="menu_help")
    )
    return kb

def group_kb(back_cb: str = "back_main"):
    groups = get_groups()
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not groups:
        kb.add(types.InlineKeyboardButton("— Guruh topilmadi —", callback_data="noop"))
    else:
        # Show Avto 13-24 first if exists
        ordered = sorted(groups.keys())
        if "Avto 13-24" in ordered:
            ordered.remove("Avto 13-24")
            ordered.insert(0, "Avto 13-24")
        for g in ordered:
            kb.add(types.InlineKeyboardButton(f"👥 {g}", callback_data=f"select_group::{encode_cb(g)}"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data=back_cb))
    return kb

def student_kb(group: str, status_map: Dict[str, str]):
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
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_groups")
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
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_students")
    )
    return kb

def final_confirm_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Tasdiqlash", callback_data="final_confirm"),
        types.InlineKeyboardButton("❌ Bekor qilish", callback_data="final_cancel"),
        types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_para")
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

def month_kb():
    # Exclude June(6), July(7), August(8) per user request
    months = [
        ("Yanvar", 1), ("Fevral", 2), ("Mart", 3), ("Aprel", 4), ("May", 5),
        ("Sentabr", 9), ("Oktabr", 10), ("Noyabr", 11), ("Dekabr", 12)
    ]
    kb = types.InlineKeyboardMarkup(row_width=3)
    for name, idx in months:
        kb.add(types.InlineKeyboardButton(name, callback_data=f"month::{idx}"))
    kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_reports"))
    return kb

# -------------------------
# START message (exact per user)
# -------------------------
START_TEXT = (
    "🏫 Avto 13-24 Guruhi Davomat Tizimiga Xush Kelibsiz!\n"
    "Assalomu alaykum, barcha o'quvchilar!\n"
    "Bu bot orqali siz quyidagi amallarni bajarishingiz mumkin:\n"
)

# -------------------------
# Handlers: start / main
# -------------------------
@bot.message_handler(commands=["start"])
def cmd_start(m: types.Message):
    uid = m.from_user.id
    if is_admin(uid):
        try:
            bot.send_message(uid, START_TEXT, reply_markup=main_start_kb())
        except Exception:
            logger.exception("start send failed")
            safe_send(uid, START_TEXT, reply_markup=main_start_kb())
    else:
        safe_send(uid, "Bu bot faqat adminlar tomonidan boshqariladi. Iltimos, admin bilan bog'laning.")

@bot.callback_query_handler(func=lambda c: c.data == "menu_help")
@admin_only_callback
def cb_help(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    text = (
        "Yordam:\n"
        "- Davomat: guruh tanlang -> kodni kiriting yoki tugmalardan foydalaning -> o'quvchilarni belgilang -> para tanlang -> tasdiqlang\n"
        "- Hisobotlar: Kunlik/Haftalik/Oylik/Yillik\n"
        "- /set_log_chat - bu bot tasdiqlangan davomatlarni qaysi guruhga yuborishini sozlash\n"
    )
    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=main_start_kb())

# -------------------------
# Menu: Davomat
# -------------------------
@bot.callback_query_handler(func=lambda c: c.data == "menu_davomat")
@admin_only_callback
def cb_menu_davomat(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    # show only Avto 13-24 first per user's request
    groups = get_groups()
    if "Avto 13-24" in groups:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("👥 Avto 13-24", callback_data=f"select_group::{encode_cb('Avto 13-24')}"))
        kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="back_main"))
        safe_edit(call.message.chat.id, call.message.message_id, "Davomat olish — guruhni tanlang:", reply_markup=kb)
    else:
        safe_edit(call.message.chat.id, call.message.message_id, "Davomat olish — guruhni tanlang:", reply_markup=group_kb("back_main"))

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("select_group::"))
@admin_only_callback
def cb_select_group(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    payload = call.data.split("::",1)[1]
    group = decode_cb(payload)
    groups = get_groups()
    if group not in groups:
        bot.answer_callback_query(call.id, "Guruh topilmadi.")
        return
    uid = call.from_user.id
    # Initialize status map: default all present
    students = groups[group]["students"]
    status_map = {s: "present" for s in students}
    TEMP[uid] = {"flow": "attend", "group": group, "status_map": status_map}
    text = f"Sinf: {group}\nO'quvchilar ro'yxati (✅ — kelgan):\n\n(istalgan ism ustiga bosib holatini o'zgartiring)"
    kb = student_kb(group, status_map)
    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "back_groups")
@admin_only_callback
def cb_back_groups(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Guruhlarni tanlang:", reply_markup=group_kb("back_main"))

# -------------------------
# Student toggle / bulk
# -------------------------
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("toggle_student::"))
@admin_only_callback
def cb_toggle_student(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend":
        bot.answer_callback_query(call.id, "Jarayon topilmadi.")
        return
    student = decode_cb(call.data.split("::",1)[1])
    status_map = state["status_map"]
    current = status_map.get(student, "present")
    # cycle
    nxt = "present"
    if current == "present":
        nxt = "sababsiz"
    elif current == "sababsiz":
        nxt = "sababli"
    elif current == "sababli":
        nxt = "present"
    status_map[student] = nxt
    state["status_map"] = status_map
    # update UI (keyboard)
    kb = student_kb(state["group"], status_map)
    try:
        safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {state['group']}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)
    except Exception:
        safe_edit_reply_markup(call.message.chat.id, call.message.message_id, kb)
    bot.answer_callback_query(call.id, f"{student}: {nxt}")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("bulk::"))
@admin_only_callback
def cb_bulk(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    action = call.data.split("::",1)[1]  # 'present' or 'sababsiz'
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend":
        bot.answer_callback_query(call.id, "Jarayon topilmadi.")
        return
    group = state["group"]
    students = get_groups()[group]["students"]
    if action == "present":
        status_map = {s: "present" for s in students}
    else:
        status_map = {s: "sababsiz" for s in students}
    state["status_map"] = status_map
    kb = student_kb(group, status_map)
    safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {group}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)
    bot.answer_callback_query(call.id, "Barcha holatlar yangilandi.")

# -------------------------
# Confirm students -> para selection
# -------------------------
@bot.callback_query_handler(func=lambda c: c.data == "confirm_students")
@admin_only_callback
def cb_confirm_students(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend":
        bot.answer_callback_query(call.id, "Jarayon topilmadi.")
        return
    safe_edit(call.message.chat.id, call.message.message_id, "Nechanchi para uchun davomatni kiritmoqchisiz?", reply_markup=para_kb())

@bot.callback_query_handler(func=lambda c: c.data == "back_students")
@admin_only_callback
def cb_back_students(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state:
        bot.answer_callback_query(call.id, "Holat topilmadi.")
        return
    kb = student_kb(state["group"], state["status_map"])
    safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {state['group']}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)

# -------------------------
# Para selection -> preview
# -------------------------
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("para::"))
@admin_only_callback
def cb_para(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "attend":
        bot.answer_callback_query(call.id, "Jarayon topilmadi.")
        return
    para = call.data.split("::",1)[1]  # '1','2','3','4','all'
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

# -------------------------
# Final confirm / cancel / back
# -------------------------
@bot.callback_query_handler(func=lambda c: c.data == "final_cancel")
@admin_only_callback
def cb_final_cancel(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    if uid in TEMP:
        state = TEMP[uid]
        kb = student_kb(state["group"], state["status_map"])
        safe_edit(call.message.chat.id, call.message.message_id, f"Sinf: {state['group']}\nO'quvchilar ro'yxati (✅ — kelgan):", reply_markup=kb)
    else:
        bot.answer_callback_query(call.id, "Jarayon topilmadi.")

@bot.callback_query_handler(func=lambda c: c.data == "back_para")
@admin_only_callback
def cb_back_para(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state:
        bot.answer_callback_query(call.id, "Jarayon topilmadi.")
        return
    safe_edit(call.message.chat.id, call.message.message_id, "Nechanchi para uchun davomatni kiritmoqchisiz?", reply_markup=para_kb())

@bot.callback_query_handler(func=lambda c: c.data == "final_confirm")
@admin_only_callback
def cb_final_confirm(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state:
        bot.answer_callback_query(call.id, "Jarayon topilmadi.")
        return
    group = state["group"]
    status_map = state["status_map"]
    para = state.get("selected_para", "all")
    date_key = datetime.now().strftime("%Y-%m-%d")
    # recorder info
    user = call.from_user
    recorder = {
        "id": user.id,
        "name": (user.first_name or "") + (" " + user.last_name if user.last_name else ""),
        "username": user.username or ""
    }
    # save to db
    try:
        record_attendance(date_key, group, para, status_map, recorder)
    except Exception:
        logger.exception("Failed to record attendance")
        bot.answer_callback_query(call.id, "Saqlashda xato yuz berdi.")
        return
    # build final summary
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
    recorder_label = recorder.get("username") and f"https://t.me/{recorder['username']}" or recorder.get("name") or str(recorder.get("id"))
    final += f"Davomat @doniyorovic1 tomonidan olindi.\n\n"
    if para != "all":
        final += f"⚠️ Ushbu o'quvchilar faqat {para_label} darsiga kelishmadi! ⚠️"
    else:
        final += "⚠️ Ushbu o'quvchilar darsga kelishmadi! ⚠️"
    # Send to configured log chat if present, else send to admin who performed
    settings = get_settings()
    log_chat = settings.get("log_chat_id")
    try:
        if log_chat:
            bot.send_message(log_chat, final)
        else:
            # send to admin that confirmed
            safe_send(uid, final)
    except Exception:
        logger.exception("Failed to send final message to log or admin; at least sending to admin")
        safe_send(uid, final)
    # clear TEMP
    TEMP.pop(uid, None)
    # return to main menu
    safe_send(uid, "Asosiy menyu:", reply_markup=main_start_kb())

# -------------------------
# Reports flow: menu + interactions
# -------------------------
@bot.callback_query_handler(func=lambda c: c.data == "menu_reports")
@admin_only_callback
def cb_menu_reports(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Hisobotlar — tanlang:", reply_markup=reports_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("report::"))
@admin_only_callback
def cb_report_type(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    mode = call.data.split("::",1)[1]  # daily, weekly, monthly, yearly
    uid = call.from_user.id
    TEMP[uid] = {"flow": "report", "mode": mode}
    # ask to choose a group
    safe_edit(call.message.chat.id, call.message.message_id, "Guruhni tanlang:", reply_markup=group_kb("back_reports"))

@bot.callback_query_handler(func=lambda c: c.data == "back_reports")
@admin_only_callback
def cb_back_reports(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    safe_edit(call.message.chat.id, call.message.message_id, "Hisobotlar bo'limi:", reply_markup=reports_kb())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("month::"))
@admin_only_callback
def cb_month(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "report" or state.get("mode") != "monthly":
        bot.answer_callback_query(call.id, "Holat mos emas.")
        return
    month_idx = int(call.data.split("::",1)[1])
    group = state.get("group_for_report")
    year = datetime.now().year
    start = date(year, month_idx, 1)
    if month_idx == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month_idx+1, 1) - timedelta(days=1)
    arr = get_attendance_in_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    arr_group = [r for r in arr if r.get("group") == group]
    if not arr_group:
        safe_edit(call.message.chat.id, call.message.message_id, f"{start.strftime('%B %Y')} uchun {group} bo'yicha davomat topilmadi.")
        TEMP.pop(uid, None)
        return
    lines = [f"📅 Oylik Davomat — {start.strftime('%B %Y')} — {group}"]
    for rec in arr_group:
        lines.append(f"\n📆 {rec.get('timestamp','')} — Para: {rec.get('para')}")
        lines.append("✅ Kelganlar (" + str(len(rec.get("present",[]))) + "): " + (", ".join(rec.get("present",[])) if rec.get("present") else "—"))
        lines.append("❌ Sababsiz (" + str(len(rec.get("sababsiz",[]))) + "): " + (", ".join(rec.get("sababsiz",[])) if rec.get("sababsiz") else "—"))
    safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
    TEMP.pop(uid, None)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("select_group::"))
@admin_only_callback
def cb_select_group_report(call: types.CallbackQuery):
    # This handler also used by reports: detect report flow
    bot.answer_callback_query(call.id)
    payload = call.data.split("::",1)[1]
    group = decode_cb(payload)
    uid = call.from_user.id
    state = TEMP.get(uid)
    if not state or state.get("flow") != "report":
        # Not in report flow; ignore (other handler handles general selection)
        return
    mode = state.get("mode")
    if mode == "daily":
        day = datetime.now().strftime("%Y-%m-%d")
        arr = get_attendance_by_date(day)
        arr_group = [r for r in arr if r.get("group") == group]
        if not arr_group:
            safe_edit(call.message.chat.id, call.message.message_id, f"{day} uchun {group} bo'yicha davomat topilmadi.")
            TEMP.pop(uid, None)
            return
        lines = [f"📅 Davomat — {day} — {group}"]
        for rec in arr_group:
            lines.append(f"\n📚 Para: {rec.get('para')}")
            lines.append("✅ Kelganlar (" + str(len(rec.get("present",[]))) + "): " + (", ".join(rec.get("present",[])) if rec.get("present") else "—"))
            lines.append("❌ Sababsiz (" + str(len(rec.get("sababsiz",[]))) + "): " + (", ".join(rec.get("sababsiz",[])) if rec.get("sababsiz") else "—"))
        safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
        TEMP.pop(uid, None)
        return
    elif mode == "weekly":
        end = datetime.now().date()
        start = end - timedelta(days=6)
        arr = get_attendance_in_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        arr_group = [r for r in arr if r.get("group") == group]
        if not arr_group:
            safe_edit(call.message.chat.id, call.message.message_id, f"{start} — {end}: {group} uchun davomat topilmadi.")
            TEMP.pop(uid, None)
            return
        lines = [f"📅 Davomat — {start} — {end} — {group}"]
        for rec in arr_group:
            lines.append(f"\n📆 {rec.get('timestamp','')} — Para: {rec.get('para')}")
            lines.append("✅ Kelganlar (" + str(len(rec.get("present",[]))) + "): " + (", ".join(rec.get("present",[])) if rec.get("present") else "—"))
            lines.append("❌ Sababsiz (" + str(len(rec.get("sababsiz",[]))) + "): " + (", ".join(rec.get("sababsiz",[])) if rec.get("sababsiz") else "—"))
        safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
        TEMP.pop(uid, None)
        return
    elif mode == "monthly":
        # set group_for_report and ask month
        TEMP[uid]["group_for_report"] = group
        safe_edit(call.message.chat.id, call.message.message_id, "Oyni tanlang (iyun,iyul,avgust ko'rsatilmaydi):", reply_markup=month_kb())
        return
    elif mode == "yearly":
        year = datetime.now().year
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        arr = get_attendance_in_range(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        arr_group = [r for r in arr if r.get("group") == group]
        if not arr_group:
            safe_edit(call.message.chat.id, call.message.message_id, f"{year} uchun {group} bo'yicha davomat topilmadi.")
            TEMP.pop(uid, None)
            return
        lines = [f"📈 Yillik Davomat — {year} — {group}"]
        for rec in arr_group:
            lines.append(f"\n📆 {rec.get('timestamp','')} — Para: {rec.get('para')}")
            lines.append("✅ Kelganlar (" + str(len(rec.get("present",[]))) + "): " + (", ".join(rec.get("present",[])) if rec.get("present") else "—"))
        safe_edit(call.message.chat.id, call.message.message_id, "\n".join(lines))
        TEMP.pop(uid, None)
        return

# -------------------------
# Admin helper commands for log chat setting
# -------------------------
@bot.message_handler(commands=["set_log_chat"])
@admin_only_message
def cmd_set_log_chat(m: types.Message):
    # If used in a group, set that group as log target; if used in private with ID param, set that ID
    if m.chat.type in ("group", "supergroup", "channel"):
        set_log_chat(m.chat.id)
        bot.reply_to(m, f"This chat ({m.chat.title or m.chat.id}) will receive attendance summaries.")
    else:
        parts = m.text.split()
        if len(parts) >= 2:
            try:
                tid = int(parts[1])
                set_log_chat(tid)
                bot.reply_to(m, f"Log chat set to id {tid}")
            except Exception:
                bot.reply_to(m, "Iltimos chat id raqam formatida kiriting.")
        else:
            bot.reply_to(m, "Agar bu komandani guruh ichida yuborsangiz, bot shu guruhni log chat sifatida sozlaydi. Yoki /set_log_chat <chat_id>")

@bot.message_handler(commands=["get_log_chat"])
@admin_only_message
def cmd_get_log_chat(m: types.Message):
    settings = get_settings()
    lc = settings.get("log_chat_id")
    bot.reply_to(m, f"Current log chat id: {lc}")

@bot.message_handler(commands=["clear_log_chat"])
@admin_only_message
def cmd_clear_log_chat(m: types.Message):
    clear_log_chat()
    bot.reply_to(m, "Log chat cleared.")

# -------------------------
# Backup / Restore endpoints
# -------------------------
@bot.message_handler(commands=["backup"])
@admin_only_message
def cmd_backup(m: types.Message):
    path = backup_db()
    if path:
        try:
            with open(path, "rb") as f:
                bot.send_document(m.chat.id, f, caption="DB backup fayli")
            bot.reply_to(m, "Backup yaratildi va yuborildi.")
        except Exception:
            logger.exception("send backup failed")
            bot.reply_to(m, "Backup yaratildi, ammo yuborishda xato yuz berdi.")
    else:
        bot.reply_to(m, "Backup yaratishda xato yuz berdi.")

@bot.message_handler(commands=["restore_from"])
@admin_only_message
def cmd_restore_from(m: types.Message):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "Foydalanish: /restore_from <backup_filename>")
        return
    fname = parts[1].strip()
    path = os.path.join(BACKUP_DIR, fname)
    if not os.path.exists(path):
        bot.reply_to(m, "Backup topilmadi.")
        return
    ok = restore_db(path)
    if ok:
        bot.reply_to(m, "DB muvaffaqiyatli tiklandi.")
    else:
        bot.reply_to(m, "DB tiklashda xato yuz berdi.")

# -------------------------
# Sample data command
# -------------------------
@bot.message_handler(commands=["sample"])
@admin_only_message
def cmd_sample(m: types.Message):
    try:
        add_group("Demo Group A", [f"Demo Student {i}" for i in range(1, 21)], "1111")
        add_group("Demo Group B", [f"DemoB Student {i}" for i in range(1, 16)], "2222")
        bot.reply_to(m, "Sample groups added.")
    except Exception:
        logger.exception("sample failed")
        bot.reply_to(m, "Sample creation failed.")

# -------------------------
# Listing groups, cancel, admins
# -------------------------
@bot.message_handler(commands=["list_groups"])
@admin_only_message
def cmd_list_groups(m: types.Message):
    groups = get_groups()
    if not groups:
        bot.reply_to(m, "Guruhlar topilmadi.")
        return
    lines = ["Guruhlar:"]
    for name, v in sorted(groups.items()):
        lines.append(f"- {name} ({len(v.get('students', []))} students) code: {v.get('code','')}")
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
    uid = m.from_user.id
    if not is_admin(uid):
        bot.reply_to(m, "Bu komanda faqat adminlarga.")
        return
    bot.reply_to(m, f"Admins: {ADMIN_IDS}")

# -------------------------
# Fallback / catch-all
# -------------------------
@bot.message_handler(func=lambda m: True)
def fallback(m: types.Message):
    uid = m.from_user.id
    if is_admin(uid):
        safe_send(uid, "Admin panel:\n/start — bosh menyu\n/help — yordam", reply_markup=main_start_kb())
    else:
        safe_send(uid, "Bu bot faqat adminlar uchun. Iltimos, admin bilan bog'laning.")

# -------------------------
# Polling runner
# -------------------------
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



    