# -*- coding: utf-8 -*-
"""
ماژول دانلود تامنیل ویدیوهای یوتیوب
------------------------------------
کاربر لینک ویدیوی یوتیوب یا اسم دقیق ویدیو را می‌فرستد، بات کیفیت‌های موجود
تامنیل (تا بالاترین کیفیتی که یوتیوب برای آن ویدیو دارد - معمولاً تا حد
Full HD / گاهی بالاتر، و در صورت نبود "تا جایی که موجود است") را پیدا کرده
و به صورت دکمه نمایش می‌دهد. با انتخاب کاربر، فایل تامنیل با همان کیفیت
به صورت سند (برای حفظ کیفیت اصلی) ارسال می‌شود.

نکته مهم: یوتیوب به صورت رسمی "تامنیل 4K" ندارد؛ بالاترین کیفیت موجود
معمولاً maxresdefault است که بسته به ویدیو می‌تواند از 120x90 تا حدود
1920x1080 (و به ندرت بالاتر) باشد. این ماژول هر چقدر که واقعاً موجود
باشد را پیدا و به ترتیب از بهترین به ضعیف‌ترین نمایش می‌دهد.
"""

import io
import json
import os
import re
import asyncio
from urllib.parse import quote

import aiohttp
from aiogram import F, types
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ====================== CONSTANTS ======================
YT_BUTTON_TEXT = "🖼 دانلود تامنیل یوتیوب"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# کیفیت‌های استاندارد تامنیل یوتیوب، از بالاترین به پایین‌ترین
THUMBNAIL_VARIANTS = ["maxresdefault", "sddefault", "hqdefault", "mqdefault", "default"]

# اندازه‌ی تصویر جای‌گزین (placeholder) که یوتیوب برای کیفیت‌های ناموجود برمی‌گرداند
PLACEHOLDER_SIZE = (120, 90)

CALLBACK_PREFIX = "ytq:"
CANCEL_CALLBACK = "ytthumb_cancel"
BATCH_CALLBACK_PREFIX = "ytbq:"  # batch quality chosen: ytbq:{video_id}:{quality}
BATCH_CANCEL_CALLBACK = "ytthumb_batch_cancel"

# هر خطی که شامل یک لینک یوتیوب معتبر باشه به‌عنوان یک آیتم جدا در نظر گرفته میشه
MULTI_LINK_SPLIT_RE = re.compile(r"[\r\n]+")


VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
URL_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtube\.com/shorts/|youtu\.be/|"
    r"youtube\.com/embed/|youtube\.com/v/)([A-Za-z0-9_-]{11})"
)

# حافظه موقت در RAM: video_id -> {"title": str, "candidates": {quality: data}}
_yt_cache = {}

# حافظه موقت پردازش دسته‌ای چند لینک: (chat_id, message_id) -> {
#   "queue": [video_id, ...], "index": int, "user_id": int
# }
_yt_batch_state = {}


# ====================== FSM ======================
class YoutubeThumbState(StatesGroup):
    waiting_input = State()


# ====================== HELPERS ======================
def _extract_video_id(text: str):
    text = text.strip()

    m = URL_ID_RE.search(text)
    if m:
        return m.group(1)

    # اگر کاربر مستقیم آیدی ۱۱ کاراکتری ویدیو را فرستاده باشد
    if VIDEO_ID_RE.fullmatch(text):
        return text

    return None


def _extract_multiple_video_ids(text: str):
    """
    اگر پیام شامل چند خط لینک یوتیوب باشد، لیست video_id های یکتا (با حفظ ترتیب) را برمی‌گرداند.
    اگر فقط یک خط/لینک باشد، لیست با یک عضو برمی‌گردد (یا خالی اگر چیزی پیدا نشد).
    خط‌هایی که لینک معتبر یوتیوب نباشند (نه URL و نه آیدی خام ۱۱ کاراکتری) نادیده گرفته می‌شوند.
    """
    lines = [ln.strip() for ln in MULTI_LINK_SPLIT_RE.split(text) if ln.strip()]
    ids = []
    seen = set()
    for line in lines:
        vid = _extract_video_id(line)
        if vid and vid not in seen:
            seen.add(vid)
            ids.append(vid)
    return ids



async def _search_youtube(session: aiohttp.ClientSession, query: str):
    """جستجوی نام ویدیو در یوتیوب؛ خروجی (video_id, title) اولین نتیجه یا (None, None)"""
    url = f"https://www.youtube.com/results?search_query={quote(query)}"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None, None
            html = await resp.text(errors="ignore")
    except Exception:
        return None, None

    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html)
    if not m:
        m = re.search(r"ytInitialData\"\]\s*=\s*(\{.*?\});", html)
    if not m:
        return None, None

    try:
        data = json.loads(m.group(1))
    except Exception:
        return None, None

    def find_video(obj):
        if isinstance(obj, dict):
            if "videoRenderer" in obj:
                vr = obj["videoRenderer"]
                vid = vr.get("videoId")
                title = ""
                try:
                    title = vr["title"]["runs"][0]["text"]
                except Exception:
                    title = vr.get("title", {}).get("simpleText", "") or ""
                if vid:
                    return vid, title
            for v in obj.values():
                res = find_video(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = find_video(item)
                if res:
                    return res
        return None

    result = find_video(data)
    if result:
        return result
    return None, None


async def _check_thumbnail(session: aiohttp.ClientSession, video_id: str, quality: str):
    """بررسی می‌کند تامنیل با این کیفیت برای ویدیو واقعاً موجود است یا یک تصویر جای‌گزین است"""
    url = f"https://i.ytimg.com/vi/{video_id}/{quality}.jpg"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            content = await resp.read()
    except Exception:
        return None

    if not content:
        return None

    width = height = None
    if PIL_AVAILABLE:
        try:
            img = Image.open(io.BytesIO(content))
            width, height = img.size
        except Exception:
            return None

        if quality != "default" and (width, height) == PLACEHOLDER_SIZE:
            # یوتیوب برای کیفیت‌های ناموجود، تصویر جای‌گزین ۱۲۰x۹۰ برمی‌گرداند
            return None
    else:
        # بدون PIL، با اندازه فایل تخمین می‌زنیم (تصویر جای‌گزین معمولاً خیلی کوچک است)
        if quality != "default" and len(content) < 2000:
            return None

    return {
        "quality": quality,
        "width": width,
        "height": height,
        "content": content,
        "size": len(content),
    }


async def _gather_thumbnails(video_id: str):
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            _check_thumbnail(session, video_id, q) for q in THUMBNAIL_VARIANTS
        ])

    candidates = [r for r in results if r]

    # حذف کیفیت‌های تکراری با وضوح یکسان (وقتی PIL نباشد ابعاد نداریم، پس بر اساس quality یکتا می‌مانند)
    seen = set()
    unique = []
    for c in candidates:
        key = (c["width"], c["height"]) if c["width"] else c["quality"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    unique.sort(key=lambda c: (c["width"] or 0) * (c["height"] or 0), reverse=True)
    return unique


def _quality_label(c: dict, is_best: bool = False) -> str:
    w, h = c.get("width"), c.get("height")
    if w and h:
        if h >= 720:
            # نکته مهم: سرور تامنیل یوتیوب (maxresdefault) برای همه‌ی ویدیوها
            # حداکثر روی 1280x720 سقف دارد. کیفیت‌های FHD/2K/4K برای تامنیل
            # وجود خارجی ندارند، حتی برای کانال‌های بزرگ - این محدودیت خود یوتیوبه.
            tag = " (بهترین کیفیت موجود از یوتیوب)" if is_best else ""
        else:
            tag = ""
        return f"🖼 {w}x{h}{tag}"
    return f"🖼 {c.get('quality', 'نامشخص')}"


async def _fetch_video_title(session: aiohttp.ClientSession, video_id: str):
    """عنوان ویدیو را از طریق oEmbed یوتیوب می‌گیرد (سبک و بدون نیاز به API key)."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            return data.get("title")
    except Exception:
        return None


def _quality_keyboard(video_id: str, candidates: list, prefix: str, extra_button=None) -> InlineKeyboardMarkup:
    rows = []
    for i, c in enumerate(candidates):
        rows.append([InlineKeyboardButton(
            text=_quality_label(c, is_best=(i == 0)),
            callback_data=f"{prefix}{video_id}:{c['quality']}"
        )])
    if extra_button:
        rows.append([extra_button])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _safe_filename(title: str, video_id: str, width, height) -> str:
    base = re.sub(r"[^\w\-]+", "_", title or video_id).strip("_")[:50] or video_id
    if width and height:
        return f"{base}_{width}x{height}.jpg"
    return f"{base}.jpg"


# ====================== REGISTER ======================
def register_youtube_thumbnail_handlers(dp, bot, get_access_block_message=None):
    """
    این تابع باید در bot.py صدا زده شود:
        register_youtube_thumbnail_handlers(dp, bot, get_access_block_message)
    پارامتر get_access_block_message اختیاری است؛ تابعی است که با گرفتن user_id
    در صورت نبود دسترسی، پیام خطا برمی‌گرداند (یا None اگر دسترسی آزاد بود).
    """

    def _check_access(user_id: int):
        if get_access_block_message:
            return get_access_block_message(user_id)
        return None

    async def _prepare_batch_item(video_id: str):
        """تامنیل‌های یک ویدیو رو آماده و در کش می‌گذارد؛ خروجی (title, candidates) یا (None, None) اگر چیزی پیدا نشد."""
        async with aiohttp.ClientSession() as session:
            title = await _fetch_video_title(session, video_id)
        candidates = await _gather_thumbnails(video_id)
        if not candidates:
            return None, None
        _yt_cache[video_id] = {
            "title": title or video_id,
            "candidates": {c["quality"]: c for c in candidates},
        }
        return title or video_id, candidates

    async def _render_batch_step(target_message: types.Message, batch_key, edit: bool = True):
        """مرحله فعلی صف batch رو روی پیام نمایش می‌دهد (یا ادیت می‌کند یا پیام جدید می‌فرستد)."""
        state_data = _yt_batch_state.get(batch_key)
        if not state_data:
            return

        queue = state_data["queue"]
        index = state_data["index"]
        total = len(queue)

        # رد کردن ویدیوهایی که موجود نبودن، تا برسیم به یک ویدیوی معتبر یا انتهای صف
        while index < total:
            video_id = queue[index]
            title, candidates = await _prepare_batch_item(video_id)
            if candidates:
                break
            state_data["failed"].append(video_id)
            index += 1
            state_data["index"] = index
        else:
            await _finish_batch(target_message, batch_key, edit=edit)
            return

        text = (
            f"🖼 انتخاب کیفیت تامنیل «{title}»\n"
            f"📋 ویدیوی {index + 1} از {total}\n\n"
            "کیفیت مورد نظر رو انتخاب کن 👇"
        )
        cancel_btn = InlineKeyboardButton(text="❌ لغو همه", callback_data=BATCH_CANCEL_CALLBACK)
        kb = _quality_keyboard(video_id, candidates, BATCH_CALLBACK_PREFIX, extra_button=cancel_btn)

        if edit:
            try:
                await target_message.edit_text(text, reply_markup=kb)
                return
            except Exception:
                pass  # اگه ادیت نشد (مثلا پیام خیلی قدیمیه)، پیام جدید می‌فرستیم

        sent = await target_message.answer(text, reply_markup=kb)
        # کلید batch_state رو به پیام جدید آپدیت کن
        new_key = (sent.chat.id, sent.message_id)
        _yt_batch_state[new_key] = state_data
        if new_key != batch_key:
            _yt_batch_state.pop(batch_key, None)

    async def _finish_batch(target_message: types.Message, batch_key, edit: bool = True):
        state_data = _yt_batch_state.pop(batch_key, None)
        if not state_data:
            return
        total = len(state_data["queue"])
        sent_count = state_data.get("sent_count", 0)
        failed = state_data.get("failed", [])

        text = f"✅ تمام شد!\n📦 {sent_count} از {total} تامنیل ارسال شد."
        if failed:
            text += f"\n⚠️ {len(failed)} لینک تامنیل نداشت یا پیدا نشد."

        if edit:
            try:
                await target_message.edit_text(text)
                return
            except Exception:
                pass
        await target_message.answer(text)

    async def _start_batch_flow(message: types.Message, video_ids: list):
        msg = await message.answer("🔎 در حال آماده‌سازی صف لینک‌ها...")
        batch_key = (msg.chat.id, msg.message_id)
        _yt_batch_state[batch_key] = {
            "queue": video_ids,
            "index": 0,
            "user_id": message.from_user.id,
            "sent_count": 0,
            "failed": [],
        }
        await _render_batch_step(msg, batch_key, edit=True)

    @dp.message(F.text == YT_BUTTON_TEXT)
    async def yt_thumb_entry(message: types.Message, state: FSMContext):
        block_msg = _check_access(message.from_user.id)
        if block_msg:
            await message.answer(block_msg)
            return

        await state.set_state(YoutubeThumbState.waiting_input)
        await message.answer(
            "🎬 <b>دانلود تامنیل ویدیو یوتیوب</b>\n\n"
            "➡️ لینک ویدیوی یوتیوب رو بفرست، یا اسم دقیق ویدیو رو تایپ کن.\n\n"
            "مثال لینک‌های قابل قبول:\n"
            "<code>https://www.youtube.com/watch?v=xxxxxxxxxxx</code>\n"
            "<code>https://youtu.be/xxxxxxxxxxx</code>\n"
            "<code>https://youtube.com/shorts/xxxxxxxxxxx</code>\n\n"
            "📋 می‌تونی چند لینک رو هم با هم بفرستی (هر کدوم تو یک خط)؛ "
            "بات برای هر ویدیو جدا جدا کیفیت تامنیل رو ازت می‌پرسه.\n\n"
            "ℹ️ نکته: یوتیوب برای تامنیل هر ویدیو حداکثر 1280x720 ارائه می‌دهد "
            "(حتی برای ویدیوهای 4K) — کیفیت تامنیل وابسته به کیفیت خود ویدیو نیست. "
            "این بات بالاترین چیزی که واقعاً برای آن ویدیو موجود است را پیدا می‌کند.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ لغو", callback_data=CANCEL_CALLBACK)
            ]])
        )

    @dp.callback_query(F.data == CANCEL_CALLBACK)
    async def yt_thumb_cancel(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        try:
            await callback.message.edit_text("❌ عملیات لغو شد.")
        except Exception:
            pass
        await callback.answer()

    @dp.message(YoutubeThumbState.waiting_input)
    async def yt_thumb_receive(message: types.Message, state: FSMContext):
        block_msg = _check_access(message.from_user.id)
        if block_msg:
            await state.clear()
            await message.answer(block_msg)
            return

        text = (message.text or "").strip()
        if not text:
            await message.answer("❌ لطفاً یک لینک یا اسم ویدیو بفرست (به‌صورت متن).")
            return

        multi_ids = _extract_multiple_video_ids(text)
        if len(multi_ids) >= 2:
            await state.clear()
            await _start_batch_flow(message, multi_ids)
            return

        wait_msg = await message.answer("🔎 در حال جستجو و بررسی کیفیت‌های تامنیل...")

        try:
            video_id = _extract_video_id(text)
            title = None

            if not video_id:
                async with aiohttp.ClientSession() as session:
                    video_id, title = await _search_youtube(session, text)

                if not video_id:
                    await wait_msg.edit_text(
                        "❌ ویدیویی با این مشخصات پیدا نشد.\n"
                        "لطفاً لینک مستقیم ویدیو رو بفرست یا اسم دقیق‌تری وارد کن."
                    )
                    await state.clear()
                    return

            candidates = await _gather_thumbnails(video_id)

            if not candidates:
                await wait_msg.edit_text("❌ هیچ تامنیلی برای این ویدیو پیدا نشد.")
                await state.clear()
                return

            _yt_cache[video_id] = {
                "title": title or text,
                "candidates": {c["quality"]: c for c in candidates},
            }

            rows = []
            for i, c in enumerate(candidates):
                rows.append([InlineKeyboardButton(
                    text=_quality_label(c, is_best=(i == 0)),
                    callback_data=f"{CALLBACK_PREFIX}{video_id}:{c['quality']}"
                )])
            rows.append([InlineKeyboardButton(text="❌ لغو", callback_data=CANCEL_CALLBACK)])

            best = candidates[0]
            best_label = _quality_label(best, is_best=True)
            note = ""
            if best.get("height") and best["height"] <= 720:
                note = (
                    "\n\nℹ️ توجه: یوتیوب برای تامنیل هیچ ویدیویی (حتی کانال‌های بزرگ) "
                    "بیشتر از 1280x720 نمی‌دهد. کیفیت واقعی ویدیو با کیفیت تامنیل آن فرق دارد."
                )

            await wait_msg.edit_text(
                "✅ ویدیو پیدا شد!\n"
                f"📌 بهترین کیفیت موجود: {best_label}{note}\n\n"
                "کیفیت مورد نظر برای دانلود رو انتخاب کن 👇",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
            )
            await state.clear()

        except Exception as e:
            try:
                await wait_msg.edit_text(f"❌ خطا در پردازش:\n<code>{str(e)[:300]}</code>", parse_mode="HTML")
            except Exception:
                await message.answer(f"❌ خطا در پردازش:\n{str(e)[:300]}")
            await state.clear()

    @dp.callback_query(F.data.startswith(CALLBACK_PREFIX))
    async def yt_thumb_quality_chosen(callback: types.CallbackQuery):
        block_msg = _check_access(callback.from_user.id)
        if block_msg:
            await callback.answer(block_msg, show_alert=True)
            return

        try:
            payload = callback.data[len(CALLBACK_PREFIX):]
            video_id, quality = payload.rsplit(":", 1)
        except Exception:
            await callback.answer("❌ درخواست نامعتبر.", show_alert=True)
            return

        cache = _yt_cache.get(video_id)
        if not cache or quality not in cache["candidates"]:
            await callback.answer("❌ این درخواست منقضی شده، لطفاً دوباره لینک رو بفرست.", show_alert=True)
            return

        await callback.answer("⏳ در حال ارسال...")

        c = cache["candidates"][quality]
        filename = _safe_filename(cache["title"], video_id, c.get("width"), c.get("height"))
        tmp_path = f"/tmp/ytthumb_{video_id}_{quality}.jpg"

        try:
            with open(tmp_path, "wb") as f:
                f.write(c["content"])

            res_text = f"{c['width']}x{c['height']}" if c.get("width") else c["quality"]
            await callback.message.answer_document(
                FSInputFile(tmp_path, filename=filename),
                caption=f"🖼 {cache['title']}\n📐 کیفیت: {res_text}"
            )
        except Exception as e:
            await callback.message.answer(f"❌ خطا در ارسال فایل:\n{str(e)[:200]}")
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    @dp.callback_query(F.data == BATCH_CANCEL_CALLBACK)
    async def yt_thumb_batch_cancel(callback: types.CallbackQuery):
        batch_key = (callback.message.chat.id, callback.message.message_id)
        _yt_batch_state.pop(batch_key, None)
        try:
            await callback.message.edit_text("❌ پردازش دسته‌ای لغو شد.")
        except Exception:
            pass
        await callback.answer()

    @dp.callback_query(F.data.startswith(BATCH_CALLBACK_PREFIX))
    async def yt_thumb_batch_quality_chosen(callback: types.CallbackQuery):
        block_msg = _check_access(callback.from_user.id)
        if block_msg:
            await callback.answer(block_msg, show_alert=True)
            return

        batch_key = (callback.message.chat.id, callback.message.message_id)
        state_data = _yt_batch_state.get(batch_key)
        if not state_data:
            await callback.answer("❌ این صف منقضی شده، لطفاً لینک‌ها رو دوباره بفرست.", show_alert=True)
            return

        try:
            payload = callback.data[len(BATCH_CALLBACK_PREFIX):]
            video_id, quality = payload.rsplit(":", 1)
        except Exception:
            await callback.answer("❌ درخواست نامعتبر.", show_alert=True)
            return

        cache = _yt_cache.get(video_id)
        if not cache or quality not in cache["candidates"]:
            await callback.answer("❌ این آیتم منقضی شده، رد می‌شیم به بعدی.", show_alert=True)
        else:
            await callback.answer("⏳ در حال ارسال...")

            c = cache["candidates"][quality]
            filename = _safe_filename(cache["title"], video_id, c.get("width"), c.get("height"))
            tmp_path = f"/tmp/ytthumb_{video_id}_{quality}.jpg"

            try:
                with open(tmp_path, "wb") as f:
                    f.write(c["content"])

                res_text = f"{c['width']}x{c['height']}" if c.get("width") else c["quality"]
                await callback.message.answer_document(
                    FSInputFile(tmp_path, filename=filename),
                    caption=f"🖼 {cache['title']}\n📐 کیفیت: {res_text}"
                )
                state_data["sent_count"] = state_data.get("sent_count", 0) + 1
            except Exception as e:
                await callback.message.answer(f"❌ خطا در ارسال فایل:\n{str(e)[:200]}")
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        # برو سراغ ویدیوی بعدی صف
        state_data["index"] += 1
        await _render_batch_step(callback.message, batch_key, edit=True)
