"""
Телеграм-бот магазин (aiogram 3.x) — профиль, баланс, пополнение, каталог
Установка:  pip install aiogram
Запуск:     python shop_bot.py
"""
import asyncio
import logging
import os
import sqlite3
import time
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN", "8900240410:AAEL7VHL4CeMNPtHTI-nmGh8ue3qJhfNgt4")
ADMIN_ID = 8759747461   # единственный администратор (только этот Telegram ID)
SUPPORT = "@Sahkapq"                    # контакт поддержки
MIN_TOPUP = 50                                # минимальное пополнение, ₽

# Реквизиты для пополнения через крипто-бот (меняй на свои)
PAY_BOT = "CryptoBot (@send)"        # через какой бот принимаешь
PAY_LINK = "https://t.me/send?start=IVXbX0mcEeBt"  # ссылка на счёт в CryptoBot
PAY_ACCOUNT = "@Sahkapq"             # твой @username в крипто-боте
PAY_COIN = "USDT"                    # монета
USDT_RATE = 90                       # курс: сколько ₽ в 1 USDT (для пересчёта в счёте)

# ───────── Каталог: id -> (название, цена ₽, описание, остаток) ─────────
PRODUCTS = {
    "gu70":  ["ГУ 70+",      0, "Описание товара ГУ 70+",      99],
    "gu50":  ["ГУ 50+",      0, "Описание товара ГУ 50+",      99],
    "gugk":  ["ГУ под ГК",   0, "Описание товара ГУ под ГК",   99],
    "gut2":  ["ГУ под Т2",   0, "Описание товара ГУ под Т2",   99],
    "gumts": ["ГУ под МТС",  0, "Описание товара ГУ под МТС",  99],
    "gubil": ["ГУ под Бил",  0, "Описание товара ГУ под Бил",  99],
    "gumeg": ["ГУ под Мега", 0, "Описание товара ГУ под Мега", 99],
}

# Категории каталога: ключ -> (название кнопки, список id товаров)
CATEGORIES = {
    "gos": ("🏛 Госсы",      ["gu70", "gu50"]),
    "sim": ("📱 Сим карты",  ["gugk", "gut2", "gumts", "gubil", "gumeg"]),
}


def cat_of(pid: str) -> str:
    return next(k for k, (_, ids) in CATEGORIES.items() if pid in ids)


LINE = "━━━━━━━━━━━━━━━"

# ───────── База данных ─────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")
db = sqlite3.connect(DB_PATH)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0,
    joined TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, product TEXT,
    price REAL, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS topups(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
    status TEXT DEFAULT 'created',
    created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
""")
# миграция: в старых версиях базы нет колонки price у заказов
if "price" not in [r[1] for r in db.execute("PRAGMA table_info(orders)")]:
    db.execute("ALTER TABLE orders ADD COLUMN price REAL DEFAULT 0")
db.commit()

db.execute("""CREATE TABLE IF NOT EXISTS products(
    id TEXT PRIMARY KEY, name TEXT, price INTEGER, descr TEXT,
    stock INTEGER, cat TEXT, pos INTEGER)""")
if not db.execute("SELECT 1 FROM products").fetchone():
    for _pos, (_pid, (_n, _pr, _d, _st)) in enumerate(PRODUCTS.items()):
        db.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?)",
                   (_pid, _n, _pr, _d, _st, cat_of(_pid), _pos))
db.commit()


def load_products():
    """Загружает товары из базы в память (PRODUCTS и CATEGORIES)."""
    PRODUCTS.clear()
    for _, ids in CATEGORIES.values():
        ids.clear()
    for pid, name, price, descr, stock, cat in db.execute(
            "SELECT id, name, price, descr, stock, cat FROM products ORDER BY pos"):
        PRODUCTS[pid] = [name, price, descr, stock]
        CATEGORIES[cat][1].append(pid)


def pname(pid: str) -> str:
    return PRODUCTS[pid][0] if pid in PRODUCTS else "(товар удалён)"


load_products()

dp = Dispatcher()


class TopUp(StatesGroup):
    amount = State()


def get_user(u) -> tuple:
    db.execute("INSERT OR IGNORE INTO users(id, username) VALUES(?,?)", (u.id, u.username or "-"))
    db.commit()
    return db.execute("SELECT balance FROM users WHERE id=?", (u.id,)).fetchone()


def fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


# ───────── Клавиатуры ─────────
def main_menu(uid=None):
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Каталог", callback_data="cat")
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="📦 Мои заказы", callback_data="my")
    kb.button(text="💬 Поддержка", callback_data="sup")
    if uid == ADMIN_ID:
        kb.button(text="🛠 Админ-панель", callback_data="adm")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def back_kb(to="menu", text="⬅️ В меню"):
    kb = InlineKeyboardBuilder()
    kb.button(text=text, callback_data=to)
    return kb.as_markup()


def catalog_kb():
    kb = InlineKeyboardBuilder()
    for key, (title, _) in CATEGORIES.items():
        kb.button(text=title, callback_data=f"c:{key}")
    kb.button(text="⬅️ В меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def category_kb(key: str):
    kb = InlineKeyboardBuilder()
    for pid in CATEGORIES[key][1]:
        name, price, _, stock = PRODUCTS[pid]
        kb.button(text=f"{name} • {fmt(price)} ₽" if stock > 0 else f"{name} • нет в наличии",
                  callback_data=f"p:{pid}")
    kb.button(text="⬅️ Назад", callback_data="cat")
    kb.adjust(1)
    return kb.as_markup()


# ───────── Меню и каталог ─────────
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    (bal,) = get_user(m.from_user)
    await m.answer(f"👋 <b>Привет, {escape(m.from_user.first_name or 'друг')}!</b>\n{LINE}\n"
                   f"💰 Баланс: <b>{fmt(bal)} ₽</b>\n{LINE}\nВыбери раздел 👇",
                   reply_markup=main_menu(m.from_user.id))


@dp.callback_query(F.data == "menu")
async def menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    (bal,) = get_user(c.from_user)
    await c.message.edit_text(f"🏠 <b>Главное меню</b>\n{LINE}\n💰 Баланс: <b>{fmt(bal)} ₽</b>",
                              reply_markup=main_menu(c.from_user.id))


@dp.callback_query(F.data == "cat")
async def catalog(c: CallbackQuery):
    await c.message.edit_text(f"🛒 <b>Каталог</b>\n{LINE}\nВыбери раздел:", reply_markup=catalog_kb())


@dp.callback_query(F.data.startswith("c:"))
async def category(c: CallbackQuery):
    key = c.data[2:]
    await c.message.edit_text(f"{CATEGORIES[key][0]}\n{LINE}\nВыбери товар:", reply_markup=category_kb(key))


@dp.callback_query(F.data.startswith("p:"))
async def product(c: CallbackQuery):
    pid = c.data[2:]
    name, price, desc, stock = PRODUCTS[pid]
    kb = InlineKeyboardBuilder()
    if stock > 0:
        kb.button(text=f"💳 Купить за {fmt(price)} ₽", callback_data=f"buy:{pid}")
    kb.button(text="⬅️ Назад", callback_data=f"c:{cat_of(pid)}")
    kb.adjust(1)
    await c.message.edit_text(
        f"🛍 <b>{name}</b>\n{LINE}\n{desc}\n{LINE}\n"
        f"💰 Цена: <b>{fmt(price)} ₽</b>\n📦 В наличии: <b>{stock}</b>",
        reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("buy:"))
async def buy(c: CallbackQuery):
    pid = c.data[4:]
    name, price, _, stock = PRODUCTS[pid]
    (bal,) = get_user(c.from_user)
    if stock <= 0:
        return await c.answer("Товара нет в наличии", show_alert=True)
    if bal < price:
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Пополнить баланс", callback_data="topup")
        kb.button(text="⬅️ Назад", callback_data=f"p:{pid}")
        kb.adjust(1)
        return await c.message.edit_text(
            f"😕 <b>Недостаточно средств</b>\n{LINE}\n"
            f"Цена: {fmt(price)} ₽\nБаланс: {fmt(bal)} ₽\n"
            f"Не хватает: <b>{fmt(price - bal)} ₽</b>", reply_markup=kb.as_markup())
    db.execute("UPDATE users SET balance=balance-? WHERE id=?", (price, c.from_user.id))
    cur = db.execute("INSERT INTO orders(user_id, product, price) VALUES(?,?,?)",
                     (c.from_user.id, pid, price))
    db.commit()
    PRODUCTS[pid][3] -= 1
    db.execute("UPDATE products SET stock=? WHERE id=?", (PRODUCTS[pid][3], pid))
    db.commit()
    oid = cur.lastrowid
    await c.message.edit_text(
        f"🎉 <b>Заказ #{oid} оплачен!</b>\n{LINE}\n"
        f"Товар: {name}\nСписано: {fmt(price)} ₽\nОстаток: {fmt(bal - price)} ₽\n{LINE}\n"
        f"Менеджер скоро передаст товар. Вопросы: {SUPPORT}", reply_markup=main_menu())
    if ADMIN_ID:
        u = c.from_user
        await c.bot.send_message(ADMIN_ID, f"🆕 <b>Заказ #{oid}</b> (оплачен с баланса)\n"
                                 f"Товар: {name} — {fmt(price)} ₽\nКлиент: @{u.username or '-'} (id {u.id})")


# ───────── Профиль ─────────
@dp.callback_query(F.data == "profile")
async def profile(c: CallbackQuery, state: FSMContext):
    await state.clear()
    u = c.from_user
    (bal,) = get_user(u)
    n, spent = db.execute("SELECT COUNT(*), COALESCE(SUM(price),0) FROM orders WHERE user_id=?",
                          (u.id,)).fetchone()
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Пополнить баланс", callback_data="topup")
    kb.button(text="📦 Мои заказы", callback_data="my")
    kb.button(text="⬅️ В меню", callback_data="menu")
    kb.adjust(1)
    await c.message.edit_text(
        f"👤 <b>Мой профиль</b>\n{LINE}\n"
        f"🆔 ID: <code>{u.id}</code>\n"
        f"👤 Имя: {escape(u.first_name or '-')}\n{LINE}\n"
        f"💰 Баланс: <b>{fmt(bal)} ₽</b>\n"
        f"🛍 Покупок: <b>{n}</b>\n"
        f"💸 Потрачено: <b>{fmt(spent)} ₽</b>", reply_markup=kb.as_markup())


# ───────── Пополнение баланса ─────────
@dp.callback_query(F.data == "topup")
async def topup(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    for a in (100, 300, 500, 1000, 2000):
        kb.button(text=f"{fmt(a)} ₽", callback_data=f"ta:{a}")
    kb.button(text="✏️ Другая сумма", callback_data="ta:custom")
    kb.button(text="⬅️ Назад", callback_data="profile")
    kb.adjust(3, 2, 1, 1)
    await c.message.edit_text(f"➕ <b>Пополнение баланса</b>\n{LINE}\nВыбери сумму:",
                              reply_markup=kb.as_markup())


@dp.callback_query(F.data == "ta:custom")
async def topup_custom(c: CallbackQuery, state: FSMContext):
    await state.set_state(TopUp.amount)
    await c.message.edit_text(f"✏️ Введи сумму пополнения числом (от {MIN_TOPUP} ₽):",
                              reply_markup=back_kb("topup", "⬅️ Назад"))


@dp.message(TopUp.amount)
async def topup_amount(m: Message, state: FSMContext):
    txt = (m.text or "").replace(" ", "")
    if not txt.isdigit() or int(txt) < MIN_TOPUP or int(txt) > 100000:
        return await m.answer(f"⚠️ Введи целое число от {MIN_TOPUP} до 100 000.")
    await state.clear()
    await send_invoice(m, int(txt), m.from_user.id)


@dp.callback_query(F.data.regexp(r"^ta:\d+$"))
async def topup_fixed(c: CallbackQuery):
    await send_invoice(c.message, int(c.data[3:]), c.from_user.id, edit=True)


async def send_invoice(msg: Message, amount: int, uid: int, edit=False):
    cur = db.execute("INSERT INTO topups(user_id, amount) VALUES(?,?)", (uid, amount))
    db.commit()
    tid = cur.lastrowid
    kb = InlineKeyboardBuilder()
    kb.button(text=f"💸 Оплатить в CryptoBot", url=PAY_LINK)
    kb.button(text="✅ Я отправил(а) средства", callback_data=f"paid:{tid}")
    kb.button(text="❌ Отмена", callback_data=f"tcancel:{tid}")
    kb.adjust(1)
    text = (f"🧾 <b>Счёт на пополнение #{tid}</b>\n{LINE}\n"
            f"💵 Сумма к оплате: <b>{fmt(amount)} ₽</b>\n{LINE}\n"
            f"🪙 Оплата в {PAY_COIN}: <b>≈ {amount / USDT_RATE:.2f} {PAY_COIN}</b>\n{LINE}\n"
            f"🤖 Крипто-бот: {PAY_BOT}\n"
            f"👤 Счёт получателя: <code>{PAY_ACCOUNT}</code>\n"
            f"📝 Комментарий к переводу: <code>#{tid}</code>\n{LINE}\n"
            f"1️⃣ Отправь {PAY_COIN} на счёт выше (в крипто-боте)\n2️⃣ Нажми «Я отправил(а) средства»\n"
            f"3️⃣ После проверки баланс пополнится")
    await (msg.edit_text if edit else msg.answer)(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("tcancel:"))
async def topup_cancel(c: CallbackQuery):
    db.execute("UPDATE topups SET status='cancel' WHERE id=? AND status='created'", (c.data[8:],))
    db.commit()
    await c.message.edit_text("❌ Счёт отменён.", reply_markup=back_kb("profile", "👤 В профиль"))


@dp.callback_query(F.data.startswith("paid:"))
async def topup_paid(c: CallbackQuery):
    tid = c.data[5:]
    row = db.execute("SELECT amount, status FROM topups WHERE id=? AND user_id=?",
                     (tid, c.from_user.id)).fetchone()
    if not row or row[1] != "created":
        return await c.answer("Счёт уже обработан", show_alert=True)
    db.execute("UPDATE topups SET status='pending' WHERE id=?", (tid,))
    db.commit()
    await c.message.edit_text(
        f"⏳ <b>Заявка #{tid} отправлена!</b>\n{LINE}\n"
        f"Сумма: {fmt(row[0])} ₽\nОжидай подтверждения — обычно несколько минут.\n"
        f"Вопросы: {SUPPORT}", reply_markup=back_kb("profile", "👤 В профиль"))
    if ADMIN_ID:
        u = c.from_user
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Зачислить", callback_data=f"tok:{tid}")
        kb.button(text="❌ Отклонить", callback_data=f"tno:{tid}")
        await c.bot.send_message(
            ADMIN_ID, f"💳 <b>Заявка на пополнение #{tid}</b>\n{LINE}\n"
            f"Сумма: <b>{fmt(row[0])} ₽</b>\nКлиент: @{u.username or '-'} (id {u.id})",
            reply_markup=kb.as_markup())


@dp.callback_query(F.data.regexp(r"^t(ok|no):\d+$"))
async def topup_admin(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("Нет доступа", show_alert=True)
    act, tid = c.data.split(":")
    row = db.execute("SELECT user_id, amount, status FROM topups WHERE id=?", (tid,)).fetchone()
    if not row or row[2] != "pending":
        return await c.answer("Заявка уже обработана", show_alert=True)
    uid, amount, _ = row
    if act == "tok":
        db.execute("UPDATE topups SET status='done' WHERE id=?", (tid,))
        db.execute("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))
        db.commit()
        (bal,) = db.execute("SELECT balance FROM users WHERE id=?", (uid,)).fetchone()
        await c.message.edit_text(f"{c.message.text}\n\n✅ Зачислено")
        await c.bot.send_message(uid, f"✅ <b>Баланс пополнен на {fmt(amount)} ₽</b>\n"
                                 f"💰 Текущий баланс: <b>{fmt(bal)} ₽</b>", reply_markup=main_menu())
    else:
        db.execute("UPDATE topups SET status='rejected' WHERE id=?", (tid,))
        db.commit()
        await c.message.edit_text(f"{c.message.text}\n\n❌ Отклонено")
        await c.bot.send_message(uid, f"❌ Заявка #{tid} отклонена — оплата не найдена.\n"
                                 f"Если это ошибка, напиши: {SUPPORT}")


# ───────── Заказы / поддержка / админ ─────────
@dp.callback_query(F.data == "my")
async def my_orders(c: CallbackQuery):
    rows = db.execute("SELECT id, product, price FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
                      (c.from_user.id,)).fetchall()
    body = "\n".join(f"#{i} • {pname(p)} • {fmt(pr)} ₽" for i, p, pr in rows) or "Пока пусто."
    await c.message.edit_text(f"📦 <b>Мои заказы</b>\n{LINE}\n{body}", reply_markup=back_kb())


@dp.callback_query(F.data == "sup")
async def support(c: CallbackQuery):
    await c.message.edit_text(f"💬 <b>Поддержка</b>\n{LINE}\nПиши: {SUPPORT}", reply_markup=back_kb())


@dp.message(Command("addbalance"))
async def add_balance(m: Message):
    """Админ: /addbalance <user_id> <сумма>"""
    if m.from_user.id != ADMIN_ID:
        return
    try:
        uid, amt = int(m.text.split()[1]), float(m.text.split()[2])
    except (IndexError, ValueError):
        return await m.answer("Формат: /addbalance <user_id> <сумма>")
    db.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, uid))
    db.commit()
    await m.answer("Готово ✅")


# ───────── Админ-панель ─────────
ADM = F.from_user.id == ADMIN_ID


class Adm(StatesGroup):
    price = State()
    stock = State()
    name = State()
    aprice = State()
    adesc = State()
    cat = State()


def adm_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Изменить цену", callback_data="adm:price")
    kb.button(text="📦 Изменить остаток", callback_data="adm:stock")
    kb.button(text="➕ Добавить товар", callback_data="adm:add")
    kb.button(text="🗑 Удалить товар", callback_data="adm:del")
    kb.button(text="⬅️ В меню", callback_data="menu")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def adm_text() -> str:
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    orders, money = db.execute("SELECT COUNT(*), COALESCE(SUM(price),0) FROM orders").fetchone()
    return (f"🛠 <b>Админ-панель</b>\n{LINE}\n"
            f"👥 Пользователей: <b>{users}</b>\n🛍 Заказов: <b>{orders}</b>\n"
            f"💰 Продано на: <b>{fmt(money)} ₽</b>\n📦 Товаров: <b>{len(PRODUCTS)}</b>\n{LINE}\nВыбери действие:")


def plist_kb(prefix: str):
    kb = InlineKeyboardBuilder()
    for pid, (name, price, _, stock) in PRODUCTS.items():
        kb.button(text=f"{name} • {fmt(price)} ₽ • {stock} шт", callback_data=f"{prefix}:{pid}")
    kb.button(text="⬅️ Назад", callback_data="adm")
    kb.adjust(1)
    return kb.as_markup()


@dp.message(Command("admin"), ADM)
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(adm_text(), reply_markup=adm_kb())


@dp.callback_query(F.data == "adm", ADM)
async def admin_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text(adm_text(), reply_markup=adm_kb())


@dp.callback_query(F.data.in_({"adm:price", "adm:stock", "adm:del"}), ADM)
async def admin_pick(c: CallbackQuery):
    title, prefix = {"adm:price": ("💰 Выбери товар для смены цены:", "ap"),
                     "adm:stock": ("📦 Выбери товар для смены остатка:", "as"),
                     "adm:del": ("🗑 Выбери товар для удаления:", "ad")}[c.data]
    if not PRODUCTS:
        return await c.answer("Товаров нет", show_alert=True)
    await c.message.edit_text(f"{title}", reply_markup=plist_kb(prefix))


# --- смена цены / остатка ---
@dp.callback_query(F.data.regexp(r"^(ap|as):"), ADM)
async def admin_edit_ask(c: CallbackQuery, state: FSMContext):
    kind, pid = c.data.split(":", 1)
    if pid not in PRODUCTS:
        return await c.answer("Товар не найден", show_alert=True)
    await state.set_state(Adm.price if kind == "ap" else Adm.stock)
    await state.update_data(pid=pid)
    cur = PRODUCTS[pid][1] if kind == "ap" else PRODUCTS[pid][3]
    what = "новую цену в ₽" if kind == "ap" else "новый остаток (шт)"
    await c.message.edit_text(f"✏️ <b>{PRODUCTS[pid][0]}</b>\nСейчас: <b>{fmt(cur)}</b>\n\nВведи {what} числом:",
                              reply_markup=back_kb("adm", "⬅️ Отмена"))


async def _save_num(m: Message, state: FSMContext, field: str, idx: int):
    txt = (m.text or "").replace(" ", "")
    if not txt.isdigit():
        return await m.answer("⚠️ Введи целое число, например 250.")
    pid = (await state.get_data())["pid"]
    if pid not in PRODUCTS:
        await state.clear()
        return await m.answer("Товар не найден.", reply_markup=adm_kb())
    db.execute(f"UPDATE products SET {field}=? WHERE id=?", (int(txt), pid))
    db.commit()
    PRODUCTS[pid][idx] = int(txt)
    await state.clear()
    await m.answer(f"✅ Готово: <b>{PRODUCTS[pid][0]}</b> → {fmt(int(txt))}", reply_markup=adm_kb())


@dp.message(Adm.price, ADM)
async def admin_set_price(m: Message, state: FSMContext):
    await _save_num(m, state, "price", 1)


@dp.message(Adm.stock, ADM)
async def admin_set_stock(m: Message, state: FSMContext):
    await _save_num(m, state, "stock", 3)


# --- удаление ---
@dp.callback_query(F.data.startswith("ad:"), ADM)
async def admin_del_ask(c: CallbackQuery):
    pid = c.data[3:]
    if pid not in PRODUCTS:
        return await c.answer("Товар не найден", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data=f"adc:{pid}")
    kb.button(text="⬅️ Отмена", callback_data="adm:del")
    kb.adjust(1)
    await c.message.edit_text(f"🗑 Удалить <b>{PRODUCTS[pid][0]}</b>?\nЭто действие нельзя отменить.",
                              reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("adc:"), ADM)
async def admin_del_do(c: CallbackQuery):
    pid = c.data[4:]
    name = pname(pid)
    db.execute("DELETE FROM products WHERE id=?", (pid,))
    db.commit()
    load_products()
    await c.message.edit_text(f"✅ Товар <b>{name}</b> удалён.\n\n{adm_text()}", reply_markup=adm_kb())


# --- добавление: название -> цена -> описание -> категория ---
@dp.callback_query(F.data == "adm:add", ADM)
async def admin_add_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.name)
    await c.message.edit_text("➕ <b>Новый товар</b>\n\n1/4. Введи название:",
                              reply_markup=back_kb("adm", "⬅️ Отмена"))


@dp.message(Adm.name, ADM)
async def admin_add_name(m: Message, state: FSMContext):
    if not m.text or len(m.text) > 60:
        return await m.answer("⚠️ Название — текст до 60 символов.")
    await state.update_data(name=escape(m.text.strip()))
    await state.set_state(Adm.aprice)
    await m.answer("2/4. Введи цену в ₽ (числом):")


@dp.message(Adm.aprice, ADM)
async def admin_add_price(m: Message, state: FSMContext):
    txt = (m.text or "").replace(" ", "")
    if not txt.isdigit():
        return await m.answer("⚠️ Введи целое число.")
    await state.update_data(price=int(txt))
    await state.set_state(Adm.adesc)
    await m.answer("3/4. Введи описание (или «-» чтобы пропустить):")


@dp.message(Adm.adesc, ADM)
async def admin_add_desc(m: Message, state: FSMContext):
    d = (m.text or "").strip()
    await state.update_data(descr="" if d == "-" else escape(d)[:500])
    await state.set_state(Adm.cat)
    kb = InlineKeyboardBuilder()
    for key, (title, _) in CATEGORIES.items():
        kb.button(text=title, callback_data=f"ac:{key}")
    kb.button(text="⬅️ Отмена", callback_data="adm")
    kb.adjust(1)
    await m.answer("4/4. В какой раздел добавить?", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("ac:"), Adm.cat, ADM)
async def admin_add_cat(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    key = c.data[3:]
    pos = (db.execute("SELECT COALESCE(MAX(pos),0)+1 FROM products").fetchone())[0]
    pid = f"n{int(time.time())}"
    db.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?)",
               (pid, d["name"], d["price"], d["descr"] or "Описание скоро появится", 99, key, pos))
    db.commit()
    load_products()
    await state.clear()
    await c.message.edit_text(
        f"✅ Добавлено: <b>{d['name']}</b> — {fmt(d['price'])} ₽\nРаздел: {CATEGORIES[key][0]}\n"
        f"Остаток по умолчанию: 99 (меняется в панели).\n\n{adm_text()}", reply_markup=adm_kb())


async def on_error(event):
    """Не даём боту падать: 'message is not modified' и прочие ошибки просто логируем."""
    if "message is not modified" not in str(event.exception):
        logging.exception("Ошибка в обработчике: %s", event.exception)
    cq = event.update.callback_query
    if cq:
        try:
            await cq.answer()
        except Exception:
            pass
    return True


try:
    dp.errors.register(on_error)
except Exception:
    pass


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
