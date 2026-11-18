import asyncio
import html
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import uvicorn

BASE = Path(__file__).resolve().parent
DB_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "sardor_clean.sqlite3"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "1967721013"))
DEVELOPER_ID = int(os.getenv("DEVELOPER_ID", "8799714877"))
SITE_URL = os.getenv("SITE_URL", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sardor-clean")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if BOT_TOKEN else None
dp = Dispatcher()

class Lead(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=5, max_length=40)
    details: str = Field(default="", max_length=1000)

def bold(value: str) -> str:
    return f"<b>{value}</b>"

def safe(value) -> str:
    return html.escape(str(value or ""))

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT NOT NULL,
            details TEXT DEFAULT '', created_at TEXT NOT NULL, status TEXT DEFAULT 'new')""")
        con.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, role TEXT NOT NULL)")
        con.execute("INSERT OR REPLACE INTO admins(user_id,role) VALUES(?,?)", (OWNER_ID, "owner"))
        con.execute("INSERT OR REPLACE INTO admins(user_id,role) VALUES(?,?)", (DEVELOPER_ID, "developer"))

def role(uid: int):
    if uid == DEVELOPER_ID:
        return "developer"
    if uid == OWNER_ID:
        return "owner"
    return None

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Заявки", callback_data="requests:0"), InlineKeyboardButton(text="Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="Открыть сайт", url=SITE_URL)] if SITE_URL.startswith("https://") else [InlineKeyboardButton(text="Сайт пока не подключён", callback_data="noop")],
        [InlineKeyboardButton(text="Настройки", callback_data="settings")]
    ])

def home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Главное меню", callback_data="home")]])

@dp.message(CommandStart())
async def start(message: Message):
    access = role(message.from_user.id)
    if access:
        label = "Разработчик" if access == "developer" else "Владелец"
        await message.answer(bold(f"Добро пожаловать в панель Sardor Clean.\nУровень доступа: {label}."), reply_markup=admin_kb())
    else:
        await message.answer(bold("Здравствуйте! Это бот Sardor Clean. Оставить заявку можно на сайте."))

@dp.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer("Ссылка на сайт появится после публикации.", show_alert=True)

@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if not role(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    with db() as con:
        total = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        new = con.execute("SELECT COUNT(*) FROM requests WHERE status='new'").fetchone()[0]
        done_count = con.execute("SELECT COUNT(*) FROM requests WHERE status='done'").fetchone()[0]
    text = f"Статистика Sardor Clean\n\nВсего заявок: {total}\nНовых: {new}\nОбработано: {done_count}"
    await call.message.edit_text(bold(text), reply_markup=admin_kb())
    await call.answer()

@dp.callback_query(F.data.startswith("requests:"))
async def requests(call: CallbackQuery):
    if not role(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    page = max(0, int(call.data.split(":")[1]))
    with db() as con:
        rows = con.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 8 OFFSET ?", (page * 8,)).fetchall()
        total = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    if not rows:
        await call.message.edit_text(bold("Заявок пока нет."), reply_markup=admin_kb())
    else:
        lines = ["Заявки\n"]
        buttons = []
        for r in rows:
            status = "Новая" if r["status"] == "new" else "Обработана"
            item = (f"Заявка #{r['id']} · {safe(r['name'])}\nТелефон: {safe(r['phone'])}\n"
                    f"Время: {safe(r['created_at'])}\nСтатус: {status}")
            if r["details"]:
                item += f"\nКомментарий: {safe(r['details'])}"
            lines.append(item)
            if r["status"] == "new":
                buttons.append([InlineKeyboardButton(text=f"Закрыть заявку #{r['id']}", callback_data=f"done:{r['id']}:{page}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="Назад", callback_data=f"requests:{page-1}"))
        if (page + 1) * 8 < total:
            nav.append(InlineKeyboardButton(text="Далее", callback_data=f"requests:{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="Главное меню", callback_data="home")])
        await call.message.edit_text(bold("\n\n".join(lines)), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@dp.callback_query(F.data.startswith("done:"))
async def done(call: CallbackQuery):
    if not role(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    _, rid, page = call.data.split(":")
    with db() as con:
        con.execute("UPDATE requests SET status='done' WHERE id=?", (int(rid),))
    await call.answer("Заявка отмечена обработанной")
    call.data = f"requests:{page}"
    await requests(call)

@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery):
    if not role(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    await call.message.edit_text(bold("Панель управления Sardor Clean"), reply_markup=admin_kb())
    await call.answer()

@dp.callback_query(F.data == "settings")
async def settings(call: CallbackQuery):
    if not role(call.from_user.id):
        return await call.answer("Нет доступа", show_alert=True)
    text = ("Настройки\n\nДоступ выдаётся автоматически по Telegram ID из переменных "
            "OWNER_ID и DEVELOPER_ID.\nТокен хранится только в переменной BOT_TOKEN на хостинге.")
    await call.message.edit_text(bold(text), reply_markup=home_kb())
    await call.answer()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = None
    if bot:
        task = asyncio.create_task(dp.start_polling(bot))
        log.info("Telegram bot polling started")
    else:
        log.warning("BOT_TOKEN is empty: website API is running, bot is disabled")
    yield
    if task:
        await dp.stop_polling()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if bot:
        await bot.session.close()

app = FastAPI(title="Sardor Clean API", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def index():
    return FileResponse(BASE / "index.html")

@app.post("/api/requests")
async def create_request(lead: Lead):
    name, phone, details = lead.name.strip(), lead.phone.strip(), lead.details.strip()
    if not name or not phone:
        raise HTTPException(400, "Заполните имя и телефон")
    created = datetime.now().strftime("%d.%m.%Y %H:%M")
    with db() as con:
        cur = con.execute("INSERT INTO requests(name,phone,details,created_at) VALUES(?,?,?,?)", (name, phone, details, created))
        rid = cur.lastrowid
    if bot:
        text = (f"<b>Новая заявка Sardor Clean #{rid}</b>\n\n"
                f"<b>Имя:</b> {safe(name)}\n<b>Телефон:</b> {safe(phone)}\n<b>Время:</b> {safe(created)}")
        if details:
            text += f"\n<b>Комментарий:</b> {safe(details)}"
        text += "\n\n<b>Откройте /start в боте, чтобы управлять заявками.</b>"
        for uid in (OWNER_ID, DEVELOPER_ID):
            try:
                await bot.send_message(uid, text)
            except Exception as e:
                log.warning("Cannot notify %s: %s", uid, e)
    return {"ok": True, "request_id": rid, "message": "Заявка отправлена! Мы скоро свяжемся с вами."}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
