"""
achievement_handler.py
هندلر کامل Achievement Maker برای بات ماینکرافت
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ─── مسیر processor ────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_PROC = _ROOT / "processor"
if str(_PROC) not in sys.path:
    sys.path.insert(0, str(_PROC))

from achievement_processor import (
    PRESET_ICONS,
    create_achievement_base,
    create_achievement_hd,
)

# ─── FSM States ─────────────────────────────────────────────────────────────

class AchievementState(StatesGroup):
    waiting_title       = State()
    waiting_description = State()
    waiting_icon        = State()
    waiting_icon_upload = State()


# ─── محدودیت‌ها ──────────────────────────────────────────────────────────────
MAX_TITLE_LEN = 25
MAX_DESC_LEN  = 50

# ─── پیام‌های helper ─────────────────────────────────────────────────────────
_MSG_TITLE = (
    "🏆 <b>Achievement Maker</b>\n\n"
    "مرحله ۱ از ۳ — <b>عنوان</b> رو بنویس:\n\n"
    "حداکثر <b>۲۵ کاراکتر</b> (فارسی یا انگلیسی)\n\n"
    "مثال: <code>اولین الماس</code> یا <code>Diamonds Found!</code>"
)

_MSG_DESC = (
    "✅ عنوان ثبت شد!\n\n"
    "مرحله ۲ از ۳ — <b>توضیح</b> رو بنویس:\n\n"
    "حداکثر <b>۵۰ کاراکتر</b>\n\n"
    "مثال: <code>اولین الماست رو پیدا کردی</code>"
)

_MSG_ICON = (
    "✅ توضیح ثبت شد!\n\n"
    "مرحله ۳ از ۳ — <b>آیکون</b> رو انتخاب کن:\n\n"
    "یا عکس دلخواه آپلود کن 👇"
)


# ─── کیبورد آیکون ────────────────────────────────────────────────────────────

def _icon_keyboard() -> InlineKeyboardMarkup:
    rows = []
    icons = list(PRESET_ICONS.items())
    # ۳ آیکون در هر ردیف
    for i in range(0, len(icons), 3):
        row = []
        for label, name in icons[i:i+3]:
            row.append(InlineKeyboardButton(
                text=label,
                callback_data=f"ach_icon:{name}"
            ))
        rows.append(row)

    rows.append([
        InlineKeyboardButton(text="📸 آپلود عکس دلخواه", callback_data="ach_icon:upload")
    ])
    rows.append([
        InlineKeyboardButton(text="❌ لغو", callback_data="ach_cancel")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── کیبورد بعد از ساخت تصویر ────────────────────────────────────────────────

def _result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 دوباره بساز",       callback_data="ach_rebuild"),
            InlineKeyboardButton(text="⬇️ دانلود HD",          callback_data="ach_hd"),
        ],
        [
            InlineKeyboardButton(text="🗑️ جدید",              callback_data="ach_new"),
        ],
    ])


# ─── ابزار ساخت و ارسال تصویر ────────────────────────────────────────────────

async def _send_achievement(message: Message, state: FSMContext, hd: bool = False):
    data    = await state.get_data()
    title   = data.get("title", "Achievement")
    desc    = data.get("description", "")
    icon    = data.get("icon_bytes") or data.get("icon_name", "star")

    await message.answer("⏳ در حال ساخت تصویر...")

    try:
        if hd:
            img_bytes = create_achievement_hd(title, desc, icon)
            suffix    = "_HD"
        else:
            img_bytes = create_achievement_base(title, desc, icon)
            suffix    = ""

        file = BufferedInputFile(img_bytes, filename=f"achievement{suffix}.png")
        caption = (
            f"🏆 <b>{title}</b>\n"
            f"📝 {desc}\n\n"
            f"{'🖼 نسخه HD (4x)' if hd else '🖼 پیش‌نمایش (2x)'}"
        )

        await message.answer_photo(
            file,
            caption=caption,
            reply_markup=_result_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(
            f"❌ خطا در ساخت تصویر:\n<code>{str(e)[:200]}</code>",
            parse_mode="HTML"
        )


# ─── ثبت هندلرها ─────────────────────────────────────────────────────────────

def register_achievement_handlers(dp: Dispatcher, bot: Bot, get_access_block_message=None):
    """
    تمام هندلرهای Achievement Maker رو داخل dp ثبت می‌کنه.

    Args:
        dp:                      Dispatcher اصلی
        bot:                     Bot instance
        get_access_block_message: تابع بررسی دسترسی (اختیاری)
    """

    # ── ۱. دکمه "🏆 Achievement Maker" (از منو) ─────────────────────────

    @dp.message(F.text == "🏆 Achievement Maker")
    async def ach_start(message: Message, state: FSMContext):
        if get_access_block_message:
            block = get_access_block_message(message.from_user.id)
            if block:
                await message.answer(block)
                return

        await state.clear()
        await state.set_state(AchievementState.waiting_title)
        await message.answer(_MSG_TITLE, parse_mode="HTML")


    # ── ۲. دریافت عنوان ─────────────────────────────────────────────────

    @dp.message(AchievementState.waiting_title, F.text)
    async def ach_title(message: Message, state: FSMContext):
        title = message.text.strip()

        if len(title) > MAX_TITLE_LEN:
            await message.answer(
                f"❌ عنوان خیلی طولانیه ({len(title)} کاراکتر).\n"
                f"حداکثر {MAX_TITLE_LEN} کاراکتر مجاز است.\n\n"
                "دوباره بنویس:"
            )
            return

        await state.update_data(title=title)
        await state.set_state(AchievementState.waiting_description)
        await message.answer(_MSG_DESC, parse_mode="HTML")


    # ── ۳. دریافت توضیح ──────────────────────────────────────────────────

    @dp.message(AchievementState.waiting_description, F.text)
    async def ach_desc(message: Message, state: FSMContext):
        desc = message.text.strip()

        if len(desc) > MAX_DESC_LEN:
            await message.answer(
                f"❌ توضیح خیلی طولانیه ({len(desc)} کاراکتر).\n"
                f"حداکثر {MAX_DESC_LEN} کاراکتر مجاز است.\n\n"
                "دوباره بنویس:"
            )
            return

        await state.update_data(description=desc)
        await state.set_state(AchievementState.waiting_icon)
        await message.answer(_MSG_ICON, parse_mode="HTML", reply_markup=_icon_keyboard())


    # ── ۴. انتخاب آیکون پیش‌فرض ─────────────────────────────────────────

    @dp.callback_query(F.data.startswith("ach_icon:"), AchievementState.waiting_icon)
    async def ach_icon_select(call: CallbackQuery, state: FSMContext):
        icon_value = call.data.split(":", 1)[1]
        await call.answer()

        if icon_value == "upload":
            await state.set_state(AchievementState.waiting_icon_upload)
            await call.message.edit_text(
                "📸 <b>عکس آیکون</b> رو ارسال کن:\n\n"
                "• بهترین حالت: تصویر مربعی PNG\n"
                "• ابعاد ایده‌آل: 32×32 تا 256×256 پیکسل",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩️ برگشت به آیکون‌ها", callback_data="ach_icon_back")
                ]])
            )
        else:
            # آیکون پیش‌فرض انتخاب شد
            await state.update_data(icon_name=icon_value, icon_bytes=None)
            await call.message.delete()
            await _send_achievement(call.message, state)


    # ── ۴b. برگشت به منوی آیکون ─────────────────────────────────────────

    @dp.callback_query(F.data == "ach_icon_back", AchievementState.waiting_icon_upload)
    async def ach_icon_back(call: CallbackQuery, state: FSMContext):
        await state.set_state(AchievementState.waiting_icon)
        await call.answer()
        await call.message.edit_text(
            _MSG_ICON,
            parse_mode="HTML",
            reply_markup=_icon_keyboard()
        )


    # ── ۵. آپلود عکس توسط کاربر ─────────────────────────────────────────

    @dp.message(AchievementState.waiting_icon_upload, F.photo)
    async def ach_icon_photo(message: Message, state: FSMContext):
        photo    = message.photo[-1]          # بزرگترین سایز
        file     = await bot.get_file(photo.file_id)
        raw      = await bot.download_file(file.file_path)
        img_bytes = raw.read() if hasattr(raw, "read") else bytes(raw)  # type: ignore

        await state.update_data(icon_bytes=img_bytes, icon_name=None)
        await _send_achievement(message, state)


    @dp.message(AchievementState.waiting_icon_upload, F.document)
    async def ach_icon_doc(message: Message, state: FSMContext):
        doc = message.document
        if not doc.file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            await message.answer("❌ فقط فایل‌های PNG / JPG / WEBP قبول میشه.")
            return

        file     = await bot.get_file(doc.file_id)
        raw      = await bot.download_file(file.file_path)
        img_bytes = raw.read() if hasattr(raw, "read") else bytes(raw)  # type: ignore

        await state.update_data(icon_bytes=img_bytes, icon_name=None)
        await _send_achievement(message, state)


    # ── ۶. دکمه‌های بعد از ساخت ──────────────────────────────────────────

    @dp.callback_query(F.data == "ach_rebuild")
    async def ach_rebuild(call: CallbackQuery, state: FSMContext):
        await call.answer("🔄 در حال ساخت مجدد...")
        await _send_achievement(call.message, state)


    @dp.callback_query(F.data == "ach_hd")
    async def ach_hd(call: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        if not data.get("title"):
            await call.answer("❌ اطلاعات یافت نشد. لطفاً دوباره شروع کن.", show_alert=True)
            return
        await call.answer("⬇️ در حال ساخت نسخه HD...")
        await _send_achievement(call.message, state, hd=True)


    @dp.callback_query(F.data == "ach_new")
    async def ach_new(call: CallbackQuery, state: FSMContext):
        await state.clear()
        await call.answer()
        await call.message.answer(_MSG_TITLE, parse_mode="HTML")


    # ── ۷. لغو در هر مرحله ───────────────────────────────────────────────

    @dp.callback_query(F.data == "ach_cancel")
    async def ach_cancel(call: CallbackQuery, state: FSMContext):
        await state.clear()
        await call.answer("❌ لغو شد.")
        await call.message.edit_text("❌ Achievement Maker لغو شد.\n\nمی‌تونی دوباره از منو شروع کنی.")