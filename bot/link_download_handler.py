# ====================== DOWNLOAD BY LINK ======================
# دانلود مستقیم فایل از یک لینک و آپلود آن در تلگرام
# ساختار این فایل مثل youtube_thumbnail_handler.py و achievement_handler.py هست:
# یک تابع register_link_download_handlers(dp, bot, get_access_block_message) که
# هندلرها رو روی dp اصلی ثبت می‌کند.

import os
import re
import mimetypes
from urllib.parse import urlparse, unquote

import aiohttp
from aiogram import F, types
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

# ====================== CONFIG ======================
DOWNLOAD_DIR = "link_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# محدودیت حجم فایل قابل دانلود/آپلود.
# توجه: سرور رسمی Bot API (cloud) اجازه آپلود بیشتر از ۵۰ مگابایت رو از طرف بات نمیده.
# اگر از یک Local Bot API Server استفاده می‌کنی، این عدد رو تا چند گیگ بالا ببر.
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024  # 50 MB

CHUNK_SIZE = 256 * 1024
URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)


# ====================== STATE ======================
class LinkDownloadState(StatesGroup):
    waiting_link = State()


# نگهداری موقت اطلاعات لینک هر کاربر تا لحظه‌ی زدن دکمه «ارسال» (حافظه، نه دیتابیس)
pending_links = {}  # user_id -> dict(url, file_name, size, format, host)


# ====================== HELPERS ======================
def human_size(num_bytes):
    if num_bytes is None:
        return "نامعلوم"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def extract_filename(url: str, headers) -> str:
    # اول از هدر Content-Disposition
    cd = headers.get("Content-Disposition")
    if cd:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        if match:
            name = unquote(match.group(1)).strip().strip('"')
            if name:
                return name

    # بعد از مسیر خود URL
    parsed = urlparse(url)
    base = os.path.basename(unquote(parsed.path))
    if base:
        return base

    return "downloaded_file"


def extract_format(file_name: str, headers) -> str:
    ext = os.path.splitext(file_name)[1].lstrip(".").upper()
    if ext:
        return ext

    content_type = (headers.get("Content-Type") or "").split(";")[0].strip()
    if content_type:
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            return guessed.lstrip(".").upper()
        return content_type.upper()

    return "نامعلوم"


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return name or "downloaded_file"


async def probe_url(url: str):
    """بررسی لینک بدون دانلود کامل فایل - سعی با HEAD، در صورت شکست با GET."""
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as session:
        try:
            async with session.head(url, allow_redirects=True) as resp:
                if resp.status < 400 and "Content-Length" in resp.headers:
                    return resp.headers, str(resp.url)
        except Exception:
            pass

        # HEAD جواب ندادن یا اطلاعات کافی نداشت -> با GET بررسی می‌کنیم (بدون خوندن بدنه)
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"سرور با کد خطای {resp.status} پاسخ داد")
            headers = resp.headers
            final_url = str(resp.url)
            resp.close()
            return headers, final_url


# ====================== REGISTER ======================
def register_link_download_handlers(dp, bot, get_access_block_message):

    @dp.message(F.text == "🔗 دانلود مستقیم با لینک")
    async def start_link_download(message: types.Message, state: FSMContext):
        block_msg = get_access_block_message(message.from_user.id)
        if block_msg:
            await message.answer(block_msg)
            return

        await state.set_state(LinkDownloadState.waiting_link)
        await message.answer(
            "🔗 <b>دانلود مستقیم با لینک</b>\n\n"
            "لینک دانلود مستقیم فایل رو بفرست تا اول مشخصاتش رو بررسی کنم.\n\n"
            "مثال:\n<code>https://example.com/file.zip</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ لغو", callback_data="dllink_cancel")
            ]])
        )

    @dp.message(LinkDownloadState.waiting_link, F.text)
    async def receive_link(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        url = message.text.strip()

        if not URL_REGEX.match(url):
            await message.answer(
                "❌ لینک نامعتبره.\n"
                "لینک باید با <code>http://</code> یا <code>https://</code> شروع شه.",
                parse_mode="HTML"
            )
            return

        checking_msg = await message.answer("🔎 در حال بررسی لینک...")

        try:
            headers, final_url = await probe_url(url)
        except Exception as e:
            await checking_msg.edit_text(
                f"❌ خطا در بررسی لینک:\n<code>{str(e)[:200]}</code>",
                parse_mode="HTML"
            )
            return

        file_name = extract_filename(final_url, headers)
        file_format = extract_format(file_name, headers)
        raw_size = headers.get("Content-Length")
        size_bytes = int(raw_size) if raw_size and raw_size.isdigit() else None
        host = urlparse(final_url).netloc or "نامعلوم"

        if size_bytes and size_bytes > MAX_DOWNLOAD_SIZE:
            await checking_msg.edit_text(
                f"❌ حجم فایل (<b>{human_size(size_bytes)}</b>) بیشتر از حد مجاز "
                f"(<b>{human_size(MAX_DOWNLOAD_SIZE)}</b>) هست.",
                parse_mode="HTML"
            )
            await state.clear()
            return

        pending_links[user_id] = {
            "url": final_url,
            "file_name": file_name,
            "size": size_bytes,
            "format": file_format,
            "host": host,
        }

        await checking_msg.edit_text(
            "📄 <b>مشخصات فایل پیدا شد</b>\n\n"
            f"📝 نام فایل: <code>{file_name}</code>\n"
            f"📦 حجم: <b>{human_size(size_bytes)}</b>\n"
            f"🌐 از: <code>{host}</code>\n"
            f"🗂 فرمت: <b>{file_format}</b>\n\n"
            "برای دانلود و ارسال فایل، دکمه زیر رو بزن 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📥 ارسال", callback_data="dllink_send")],
                [InlineKeyboardButton(text="❌ لغو", callback_data="dllink_cancel")],
            ])
        )
        # از حالت انتظار خارج میشیم، اطلاعات لینک توی pending_links نگه داشته میشه
        await state.clear()

    @dp.callback_query(F.data == "dllink_cancel")
    async def cancel_link_download(callback: types.CallbackQuery, state: FSMContext):
        pending_links.pop(callback.from_user.id, None)
        await state.clear()
        try:
            await callback.message.edit_text("❌ عملیات دانلود از لینک لغو شد.")
        except Exception:
            pass
        await callback.answer()

    @dp.callback_query(F.data == "dllink_send")
    async def send_link_download(callback: types.CallbackQuery, state: FSMContext):
        user_id = callback.from_user.id
        info = pending_links.get(user_id)

        if not info:
            await callback.answer("❌ اطلاعات لینک پیدا نشد، دوباره لینک رو بفرست.", show_alert=True)
            return

        block_msg = get_access_block_message(user_id)
        if block_msg:
            await callback.answer(block_msg, show_alert=True)
            return

        await callback.answer()
        await callback.message.edit_text(
            f"⬇️ در حال دانلود <code>{info['file_name']}</code> ...",
            parse_mode="HTML"
        )

        temp_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_{safe_filename(info['file_name'])}")

        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=300)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as session:
                async with session.get(info["url"], allow_redirects=True) as resp:
                    if resp.status >= 400:
                        raise RuntimeError(f"سرور با کد خطای {resp.status} پاسخ داد")

                    downloaded = 0
                    with open(temp_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if downloaded > MAX_DOWNLOAD_SIZE:
                                raise RuntimeError("حجم فایل هنگام دانلود از حد مجاز عبور کرد")

            await callback.message.edit_text(
                f"⬆️ در حال آپلود <code>{info['file_name']}</code> ...",
                parse_mode="HTML"
            )

            await bot.send_document(
                chat_id=callback.message.chat.id,
                document=FSInputFile(temp_path, filename=info["file_name"]),
                caption=(
                    f"✅ <b>{info['file_name']}</b>\n"
                    f"📦 {human_size(info['size'])} | 🌐 {info['host']}"
                ),
                parse_mode="HTML"
            )
            try:
                await callback.message.delete()
            except Exception:
                pass

        except Exception as e:
            err = str(e).lower()
            if "too large" in err or "entity too large" in err or "file is too big" in err:
                await callback.message.edit_text(
                    "❌ حجم فایل برای آپلود در تلگرام زیاده.\n"
                    f"محدودیت فعلی: {human_size(MAX_DOWNLOAD_SIZE)}\n"
                    "(اگه سرور Bot API اختصاصی داری، میتونی این محدودیت رو توی کد بالا ببری)"
                )
            else:
                await callback.message.edit_text(
                    f"❌ خطا در دانلود/آپلود فایل:\n<code>{str(e)[:300]}</code>",
                    parse_mode="HTML"
                )
        finally:
            pending_links.pop(user_id, None)
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
