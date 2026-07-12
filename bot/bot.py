from pathlib import Path
import asyncio
import os
import io
import random
import string
import zipfile
import json
import uuid
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from armor_handler import register_armor_handlers
from youtube_thumbnail_handler import register_youtube_thumbnail_handlers
from achievement_handler import register_achievement_handlers
from link_download_handler import register_link_download_handlers
import aiohttp
from mod_search_handler import register_mod_search_handlers

from config import TOKEN, ADMIN_ID
from database import Session, License
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = None

# ====================== زمان و احوال‌پرسی فارسی ======================
PERSIAN_WEEKDAYS = {
    0: "دوشنبه",
    1: "سه‌شنبه",
    2: "چهارشنبه",
    3: "پنجشنبه",
    4: "جمعه",
    5: "شنبه",
    6: "یکشنبه",
}


def get_tehran_now() -> datetime:
    """زمان فعلی به وقت تهران (Asia/Tehran)."""
    if TEHRAN_TZ:
        return datetime.now(TEHRAN_TZ)
    # fallback در صورت نبود دیتابیس tz: UTC+3:30
    return datetime.utcnow() + timedelta(hours=3, minutes=30)


def get_daypart_greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "صبح"
    if 12 <= hour < 15:
        return "ظهر"
    if 15 <= hour < 19:
        return "بعد از ظهر"
    return "شب"


def get_persian_datetime_greeting() -> str:
    """مثال خروجی: «صبح روز یکشنبه بخیر»"""
    now = get_tehran_now()
    weekday_fa = PERSIAN_WEEKDAYS.get(now.weekday(), "")
    daypart = get_daypart_greeting(now.hour)
    return f"{daypart} روز {weekday_fa} بخیر"


# ====================== FSM ======================
class BroadcastState(StatesGroup):
    waiting_message = State()
    waiting_buttons = State()

class AdminState(StatesGroup):
    waiting_search = State()

class LicenseCreateState(StatesGroup):
    waiting_custom_minutes = State()

class ResourcePackManualState(StatesGroup):
    waiting_icon = State()
    waiting_inventory = State()

class ResourcePackSettingsState(StatesGroup):
    choosing_percent = State()

# گزینه‌های زمانی لایسنس به ترتیب چرخش: (متن نمایشی, مقدار به دقیقه / None برای همیشگی / "custom" برای دلخواه)
LICENSE_TIME_OPTIONS = [
    ("♾️ همیشگی", None),
    ("📅 5 روز", 5 * 24 * 60),
    ("📅 10 روز", 10 * 24 * 60),
    ("📅 15 روز", 15 * 24 * 60),
    ("📅 30 روز", 30 * 24 * 60),
    ("✏️ دلخواه", "custom"),
]

# نگهداری وضعیت انتخاب زمان لایسنس برای هر چت ادمین (حافظه موقت، نه دیتابیس)
license_selection = {}  # chat_id -> {"index": int, "custom_minutes": int|None, "message_id": int}

bot = Bot(token=TOKEN)
dp = Dispatcher()
register_armor_handlers(dp, bot)
user_modes = {}
user_data = {}  # برای ذخیره اطلاعات بین دو مرحله JSON و Texture
user_selections = {}  # برای ذخیره انتخاب‌های کاربر در جستجوی asset

# ====================== 3D BLOCK ITEM BUILDER (state) ======================
_block_model_cache = {}       # model_path ("block/xxx") -> parsed JSON یا None (کش برای جلوگیری از فچ تکراری)
block3d_last_use = {}         # user_id -> datetime آخرین باری که "ساخت آیتم سه‌بعدی" استفاده شده
BLOCK3D_COOLDOWN_MINUTES = 10  # هر کاربر فقط هر ۱۰ دقیقه یک‌بار می‌تونه بسازه

# ====================== PATHS ======================
BASE_DIR = Path(__file__).resolve().parent.parent    # ← تغییر به parent.parent

PROCESSOR_DIR = str(BASE_DIR / "processor")

NODE_SCRIPT = os.path.join(PROCESSOR_DIR, "processor.mjs")
ITEM3D_SCRIPT = os.path.join(PROCESSOR_DIR, "item3d.mjs")
JSON_TO_OBJ_SCRIPT = os.path.join(PROCESSOR_DIR, "json_to_obj.mjs")
OBJ_PREVIEW_SCRIPT = os.path.join(PROCESSOR_DIR, "obj_preview.mjs")
BLOCK_RENDER_SCRIPT = os.path.join(PROCESSOR_DIR, "block_render.mjs")

INPUT_DIR = os.path.join(PROCESSOR_DIR, "input")
OUTPUT_DIR = os.path.join(PROCESSOR_DIR, "output")

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ====================== LICENSE ======================
LICENSE_REGEX = r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$"


def generate_license():
    chars = string.ascii_uppercase + string.digits
    return '-'.join(''.join(random.choices(chars, k=4)) for _ in range(4))


def is_admin(user_id: int):
    return user_id == ADMIN_ID


def is_user_banned(user_id: int) -> bool:
    session = Session()
    try:
        banned = session.query(License).filter(
            License.user_id == user_id,
            License.banned == True
        ).first()
        return banned is not None
    finally:
        session.close()


def get_access_block_message(user_id: int):
    """
    بررسی می‌کند آیا کاربر اجازه استفاده از امکانات بات (دکمه‌ها) را دارد یا نه.
    اگر دسترسی نداشته باشد (لایسنس نزده، بن شده یا لایسنسش تموم شده)
    پیام مناسب (فرمت‌شده با HTML) را برمی‌گرداند، در غیر این صورت None یعنی دسترسی آزاد است.
    توجه: خروجی این تابع شامل تگ HTML است؛ هرجا با message.answer استفاده می‌شود
    باید parse_mode="HTML" هم پاس داده شود. برای نمایش در alert (که HTML پشتیبانی
    نمی‌شود) از strip_html_tags() روی خروجی استفاده کنید.
    """
    if is_admin(user_id):
        return None

    session = Session()
    try:
        licenses = session.query(License).filter(License.user_id == user_id).all()

        if not licenses:
            return (
                "🔒 <b>شما لایسنس فعالی ندارید</b>\n\n"
                "<blockquote>برای استفاده از امکانات ربات، ابتدا باید یک لایسنس فعال داشته باشید.\n\n"
                "📩 برای دریافت لایسنس به ادمین پیام بدید:\n"
                "@Amirmah198</blockquote>"
            )

        if any(l.banned for l in licenses):
            return (
                "🚫 <b>شما از استفاده از ربات بن شده‌اید</b>\n\n"
                "<blockquote>در صورت اعتراض می‌تونید به ادمین پیام بدید:\n"
                "@Amirmah198</blockquote>"
            )

        now = datetime.utcnow()
        has_active = any(
            l.used and (l.expires_at is None or l.expires_at > now)
            for l in licenses
        )

        if has_active:
            return None

        return (
            "⏳ <b>لایسنس شما به پایان رسیده</b>\n\n"
            "<blockquote>مدت اعتبار لایسنس قبلی‌تون تموم شده.\n"
            "برای خرید مجدد و ادامه استفاده از امکانات ربات:\n\n"
            "📩 @AmirMah198</blockquote>"
        )
    finally:
        session.close()


def strip_html_tags(text: str) -> str:
    """حذف تگ‌های HTML از یک متن، برای نمایش در جاهایی مثل alert که HTML پشتیبانی نمی‌شود."""
    import re
    return re.sub(r"<[^>]+>", "", text)




# ثبت هندلرهای دانلود تامنیل یوتیوب (بعد از تعریف get_access_block_message)
register_youtube_thumbnail_handlers(dp, bot, get_access_block_message)
register_achievement_handlers(dp, bot, get_access_block_message)
register_link_download_handlers(dp, bot, get_access_block_message)
register_mod_search_handlers(dp, bot, get_access_block_message)

# ====================== HELPERS ======================
async def run_node_processor(input_path: str, output_path: str, xp_percent: float = 0.7, upscale_rate: int = 1):
    proc = await asyncio.create_subprocess_exec(
        "node", NODE_SCRIPT, input_path, output_path,
        str(xp_percent), str(upscale_rate),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=PROCESSOR_DIR
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Node processor failed: {stderr.decode()}")


# ====================== HUD OVERLAY - انتخاب درصد نوار XP ======================
DEFAULT_XP_PERCENT_INT = 70  # درصد پیش‌فرض (عدد صحیح 0 تا 100)


def _rp_xp_percent_int(state_data: dict) -> int:
    return state_data.get("rp_xp_percent_int", DEFAULT_XP_PERCENT_INT)


def build_rp_percent_text(percent_int: int) -> str:
    return (
        "🎚 <b>تنظیم درصد نوار XP</b>\n\n"
        f"📊 درصد فعلی: <b>{percent_int}٪</b>\n\n"
        "با دکمه‌های پایین درصد رو کم/زیاد کن و بعد «شروع پردازش» رو بزن."
    )


def build_rp_percent_kb(percent_int: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📊 {percent_int}٪", callback_data="rp_xp_noop")],
        [
            InlineKeyboardButton(text="➖10", callback_data="rp_xp_dec10"),
            InlineKeyboardButton(text="➖5", callback_data="rp_xp_dec5"),
            InlineKeyboardButton(text="➕5", callback_data="rp_xp_inc5"),
            InlineKeyboardButton(text="➕10", callback_data="rp_xp_inc10"),
        ],
        [InlineKeyboardButton(text="✅ شروع پردازش", callback_data="rp_xp_start", style="success")],
    ])


async def run_item3d(input_path: str, output_obj: str):
    proc = await asyncio.create_subprocess_exec(
        "node", ITEM3D_SCRIPT, input_path, output_obj,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=PROCESSOR_DIR
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"Item3D failed:\n{stderr.decode()}")

    zip_path = output_obj.replace(".obj", ".zip")
    if os.path.exists(zip_path):
        return zip_path
    elif os.path.exists(output_obj):
        return output_obj
    else:
        raise RuntimeError("No output file was generated")


async def run_json_to_obj(json_path: str, output_obj: str):
    os.makedirs(os.path.dirname(output_obj), exist_ok=True)
    
    proc = await asyncio.create_subprocess_exec(
        "node", JSON_TO_OBJ_SCRIPT, json_path, output_obj,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=PROCESSOR_DIR
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        raise RuntimeError(f"JSON to OBJ failed:\n{stderr.decode()}")
    
    if not os.path.exists(output_obj):
        raise RuntimeError(f"Output file not created: {output_obj}")
    
    return output_obj


async def run_obj_preview(obj_path: str, texture_path: str, preview_path: str):
    """ساخت یک تصویر پیش‌نمایش (جلو + پشت کنار هم) از فایل OBJ با تکسچر آن."""
    os.makedirs(os.path.dirname(preview_path), exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "node", OBJ_PREVIEW_SCRIPT, obj_path, texture_path, preview_path, "512",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=PROCESSOR_DIR
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"OBJ preview render failed:\n{stderr.decode()}")

    if not os.path.exists(preview_path):
        raise RuntimeError(f"Preview file not created: {preview_path}")

    return preview_path


async def run_block_render(obj_path: str, out_png: str, size: int = 512):
    """اجرای block_render.mjs برای ساخت آیکون ایزومتریک (مثل اینونتوری) از یک OBJ چندمتریالی."""
    os.makedirs(os.path.dirname(out_png), exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "node", BLOCK_RENDER_SCRIPT, obj_path, out_png, str(size),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=PROCESSOR_DIR
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"Block render failed:\n{stderr.decode()}")

    if not os.path.exists(out_png):
        raise RuntimeError(f"Render file not created: {out_png}")

    return out_png


def create_zip_with_texture(base_name: str, obj_path: str, texture_path: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    zip_path = os.path.join(OUTPUT_DIR, f"{base_name}.zip")
    texture_name = os.path.basename(texture_path)
    mtl_path = obj_path.replace('.obj', '.mtl')
    
    if os.path.exists(mtl_path):
        with open(mtl_path, 'r', encoding='utf-8') as f:
            mtl_content = f.read()
        mtl_content = mtl_content.replace('texture.png', texture_name)
        with open(mtl_path, 'w', encoding='utf-8') as f:
            f.write(mtl_content)

    with zipfile.ZipFile(zip_path, 'w') as z:
        z.write(obj_path, os.path.basename(obj_path))
        if os.path.exists(mtl_path):
            z.write(mtl_path, os.path.basename(mtl_path))
        z.write(texture_path, texture_name)
    
    return zip_path


# ====================== LICENSE TIME SELECTION HELPERS ======================
def license_time_label(index: int, custom_minutes: int = None) -> str:
    label, value = LICENSE_TIME_OPTIONS[index]
    if value == "custom" and custom_minutes:
        return f"✏️ دلخواه ({custom_minutes} دقیقه)"
    return label


def license_time_minutes(index: int, custom_minutes: int = None):
    """مقدار نهایی دقیقه برای ساخت لایسنس. None یعنی همیشگی."""
    label, value = LICENSE_TIME_OPTIONS[index]
    if value == "custom":
        return custom_minutes
    return value


def build_license_time_kb(index: int, custom_minutes: int = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=license_time_label(index, custom_minutes), callback_data=make_cb("lic_cycle"))],
        [InlineKeyboardButton(text="✅ ساخت لایسنس", callback_data=make_cb("lic_build"))],
    ])


# ====================== ADMIN PANEL HELPERS ======================
PAGE_SIZE = 5


def make_cb(a: str, **kwargs) -> str:
    data = {"a": a}
    data.update(kwargs)
    return json.dumps(data, separators=(",", ":"))


def parse_cb(data: str):
    return json.loads(data)


def admin_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔑 ساخت لایسنس جدید")],
            [KeyboardButton(text="📢 اطلاع‌رسانی")],
            [KeyboardButton(text="🛠 سیستم مدیریت")],
            [KeyboardButton(text="🧪 تست پنل کاربر")],
        ],
        resize_keyboard=True
    )


def get_license_status_text(user_id: int) -> str:
    """متن دکمه‌ی بالای منو: چند روز/ساعت از لایسنس کاربر باقی مانده است."""
    if not user_id:
        return "⏳ وضعیت لایسنس"

    if is_admin(user_id):
        return "⏳ وضعیت لایسنس: ادمین ♾️"

    session = Session()
    try:
        licenses = session.query(License).filter(
            License.user_id == user_id,
            License.used == True,
            License.banned == False
        ).all()

        if not licenses:
            return "⏳ وضعیت لایسنس: ندارید"

        if any(l.expires_at is None for l in licenses):
            return "⏳ وضعیت لایسنس: همیشگی ♾️"

        now = datetime.utcnow()
        active = [l for l in licenses if l.expires_at and l.expires_at > now]
        if not active:
            return "⏳ وضعیت لایسنس: منقضی شده"

        closest = min(active, key=lambda l: l.expires_at)
        remaining = closest.expires_at - now
        days = remaining.days
        hours = remaining.seconds // 3600

        if days > 0:
            return f"⏳ وضعیت لایسنس: {days} روز باقی مانده"
        return f"⏳ وضعیت لایسنس: {hours} ساعت باقی مانده"
    finally:
        session.close()


def user_main_keyboard(user_id: int = None):
    status_text = get_license_status_text(user_id)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=status_text)],
            [KeyboardButton(text="📦 ساخت HUD Overlay از ریسورس پک"), KeyboardButton(text="🧊 ساخت آیتم سه‌بعدی ماینکرافت")],
            [KeyboardButton(text="🔄 تبدیل JSON به OBJ"), KeyboardButton(text="📥 گرفتن فایل‌های ماینکرافت")],
            [KeyboardButton(text="🛡 ساخت آرمور با تریم"), KeyboardButton(text="🖼 دانلود تامنیل یوتیوب")],
            [KeyboardButton(text="🔗 دانلود مستقیم با لینک")],
            [KeyboardButton(text="🔎 جستجوی مود/ریسورس‌پک")],
        ],
        resize_keyboard=True
    )


# ====================== منوی اصلی به‌صورت Inline ======================
# ترتیب دقیقاً مطابق ReplyKeyboard منوی کاربر است
MAIN_MENU_ROWS = [
    [("📦 ساخت HUD Overlay از ریسورس پک", "menu_hud"), ("🧊 ساخت آیتم سه‌بعدی ماینکرافت", "menu_item3d")],
    [("🔄 تبدیل JSON به OBJ", "menu_json2obj"), ("📥 گرفتن فایل‌های ماینکرافت", "menu_getfiles")],
    [("🛡 ساخت آرمور با تریم", "menu_armor"), ("🖼 دانلود تامنیل یوتیوب", "menu_thumb")],
    [("🔗 دانلود مستقیم با لینک", "menu_link")],
    [("🔎 جستجوی مود/ریسورس‌پک", "menu_modsearch")],
]

# نگاشت callback_data به همان متنی که هندلرهای فعلی روی آن (F.text ==) گوش می‌دهند
MENU_CALLBACK_TO_TEXT = {cb: text for row in MAIN_MENU_ROWS for text, cb in row}


def build_main_inline_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text, callback_data=cb) for text, cb in row]
        for row in MAIN_MENU_ROWS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)



def build_welcome_back_text(first_name: str) -> str:
    """پیام خوش‌آمدگویی برای کاربرانی که لایسنس فعال دارند و دوباره /start می‌زنند."""
    greeting = get_persian_datetime_greeting()
    return (
        f"سلام {first_name} 👋\n\n"
        f"{greeting} 🌤\n\n"
        "امروز می‌خوایم چه کاری انجام بدیم؟ 🚀\n"
        "یکی از گزینه‌های زیر رو انتخاب کن 👇"
    )


def build_welcome_activated_text(first_name: str) -> str:
    """پیامی که بلافاصله بعد از فعال‌سازی موفق لایسنس نمایش داده می‌شود."""
    return (
        f"🎉 <b>{first_name} عزیز، خوش اومدی به بلاکسی تول (Bloxy Tool)!</b>\n\n"
        "قراره خیلی با هم پیشرفت کنیم 🚀\n\n"
        "برای شروع، یکی از گزینه‌های زیر رو انتخاب کن 👇"
    )


def build_no_license_text(first_name: str) -> str:
    """پیام خوش‌آمدگویی برای کاربرانی که هنوز لایسنسی فعال نکرده‌اند."""
    return (
        f"👋 سلام {first_name} عزیز، به <b>بلاکسی تول (Bloxy Tool)</b> خوش اومدی!\n\n"
        "🤖 این بات کلی امکانات کاربردی برای ماینکرافت داره؛ از ساخت HUD Overlay و "
        "آیتم سه‌بعدی گرفته تا دانلود مستقیم فایل و جستجوی مود و ریسورس‌پک.\n\n"
        "🔑 برای استفاده از امکانات بات، ابتدا باید یک <b>لایسنس فعال</b> داشته باشی.\n"
        "لایسنس یه کد با این فرمته:\n"
        "<code>XXXX-XXXX-XXXX-XXXX</code>\n"
        "مثال: <code>A1B2-C3D4-E5F6-G7H8</code>\n\n"
        "✅ همین که لایسنستو گرفتی، کافیه دقیقاً همون کد رو همینجا برام بفرستی "
        "تا خودکار فعال بشه.\n\n"
        "📩 برای دریافت لایسنس به ادمین پیام بده:\n"
        "@Amirmah198"
    )


def build_license_delivery_text(key: str, time_label: str) -> str:
    """متن استاندارد تحویل لایسنس به خریدار (هم در پنل ادمین و هم در حالت Inline استفاده می‌شود)."""
    return (
        f"<b>✅ لایسنس شما آماده شد</b>\n\n"
        f"<code>{key}</code>\n\n"
        f"⏱ مدت اعتبار: {time_label}\n\n"
        "⚠️ این لایسنس <b>یک‌بار مصرف</b> میباشد.\n"
        "لطفاً آن را فقط در اکانتی وارد نمایید که از <b>امنیت آن اطمینان کامل</b> دارید.\n\n"
        "⚠️ لایسنس‌ها مجدد ساخته نمی‌شوند. هیچ پاسخی از طرف بنده در قبال دریافت مجدد لایسنس پذیرا نخواهم شد.\n\n"
        "<b>مبارکتون باشه 🌹</b>"
    )


def build_license_delivery_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ فعال‌سازی خودکار لایسنس", callback_data=f"actlic:{key}")
    ]])


def build_management_panel_kb():
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👥 لیست کاربران", callback_data=make_cb("list", p=1))],
        [types.InlineKeyboardButton(text="🚫 لیست بن‌شده‌ها", callback_data=make_cb("banned", p=1))],
        [types.InlineKeyboardButton(text="🔍 جستجو کاربر", callback_data=make_cb("search"))],
        [types.InlineKeyboardButton(text="↩️ بستن پنل", callback_data=make_cb("back"))],
    ])
    return kb


def render_user_line(idx: int, lic: License) -> str:
    uname = lic.username or "بدون یوزرنیم"
    status = "بن‌شده" if lic.banned else "فعال"
    used_at = lic.used_at.strftime("%Y-%m-%d %H:%M") if lic.used_at else "نامشخص"
    return (
        f"{idx}. ID: {lic.user_id}\n"
        f"   Username: @{uname}\n"
        f"   License: {lic.key}\n"
        f"   Used at: {used_at}\n"
        f"   Status: {status}\n"
    )


async def send_users_page(callback: types.CallbackQuery, page: int, banned_only: bool = False):
    session = Session()
    try:
        q = session.query(License).filter(License.used == True)
        if banned_only:
            q = q.filter(License.banned == True)
        q = q.order_by(License.id)

        total = q.count()
        if total == 0:
            text = "هیچ کاربری یافت نشد." if not banned_only else "هیچ کاربر بن‌شده‌ای وجود ندارد."
            await callback.message.edit_text(text, reply_markup=build_management_panel_kb())
            return

        max_page = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if page < 1:
            page = 1
        if page > max_page:
            page = max_page

        items = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

        lines = []
        for i, lic in enumerate(items, start=1 + (page - 1) * PAGE_SIZE):
            lines.append(render_user_line(i, lic))

        header = "👥 لیست کاربران\n\n" if not banned_only else "🚫 لیست کاربران بن‌شده\n\n"
        text = header + "\n".join(lines) + f"\n\nصفحه {page} از {max_page}"

        kb_rows = []

        # دکمه پروفایل برای هر کاربر
        for lic in items:
            uname = lic.username or "بدون یوزرنیم"
            btn_text = f"👤 {lic.user_id} (@{uname})"
            kb_rows.append([
                types.InlineKeyboardButton(
                    text=btn_text,
                    callback_data=make_cb("profile", u=lic.user_id)
                )
            ])

        nav_row = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️ قبلی",
                    callback_data=make_cb("banned" if banned_only else "list", p=page - 1)
                )
            )
        if page < max_page:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="➡️ بعدی",
                    callback_data=make_cb("banned" if banned_only else "list", p=page + 1)
                )
            )
        if nav_row:
            kb_rows.append(nav_row)

        kb_rows.append([
            types.InlineKeyboardButton(text="↩️ بازگشت به پنل", callback_data=make_cb("panel"))
        ])

        kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await callback.message.edit_text(text, reply_markup=kb)

    finally:
        session.close()


async def send_user_profile(callback: types.CallbackQuery, user_id: int):
    session = Session()
    try:
        licenses = session.query(License).filter(License.user_id == user_id).all()
        if not licenses:
            await callback.answer("کاربر یافت نشد.", show_alert=True)
            return

        banned = any(l.banned for l in licenses)
        uname = licenses[0].username or "بدون یوزرنیم"

        lines = [
            "👤 پروفایل کاربر",
            "",
            f"ID: {user_id}",
            f"Username: @{uname}",
            f"Status: {'بن‌شده' if banned else 'فعال'}",
            "",
            "لایسنس‌ها:"
        ]
        for lic in licenses:
            used_at = lic.used_at.strftime("%Y-%m-%d %H:%M") if lic.used_at else "نامشخص"
            lines.append(f"- {lic.key} (Used at: {used_at})")

        text = "\n".join(lines)

        kb_rows = []
        if banned:
            kb_rows.append([
                types.InlineKeyboardButton(
                    text="♻️ آن‌بن کاربر",
                    callback_data=make_cb("unban", u=user_id)
                )
            ])
        else:
            kb_rows.append([
                types.InlineKeyboardButton(
                    text="🚫 بن کاربر",
                    callback_data=make_cb("ban", u=user_id)
                )
            ])

        kb_rows.append([
            types.InlineKeyboardButton(
                text="↩️ بازگشت به پنل",
                callback_data=make_cb("panel")
            )
        ])

        kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await callback.message.edit_text(text, reply_markup=kb)

    finally:
        session.close()


# ====================== COMMANDS ======================
@dp.message(Command("start"))
async def start(message: types.Message):
    if is_admin(message.from_user.id):
        keyboard = admin_main_keyboard()
        await message.answer(
            "سلام ادمین گرامی\n\nبرای مدیریت از دکمه‌های زیر استفاده کن:",
            reply_markup=keyboard
        )
        return

    if is_user_banned(message.from_user.id):
        await message.answer("❌ شما از استفاده از ربات بن شده‌اید.")
        return

    first_name = message.from_user.first_name or "دوست عزیز"
    block_msg = get_access_block_message(message.from_user.id)

    if block_msg is None:
        # کاربر لایسنس فعال دارد → کیبورد اصلی واقعی کاربر نمایش داده شود
        await message.answer(
            build_welcome_back_text(first_name),
            reply_markup=user_main_keyboard(message.from_user.id)
        )
    else:
        session = Session()
        try:
            has_any_license = session.query(License).filter(
                License.user_id == message.from_user.id
            ).count() > 0
        finally:
            session.close()

        if has_any_license:
            # لایسنس داشته ولی الان منقضی شده؛ کیبورد باید حذف شود
            await message.answer(block_msg, parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
        else:
            # اولین بار است که استارت می‌زند و هیچ لایسنسی هم ندارد؛ کیبوردی نباید باشد
            await message.answer(
                build_no_license_text(first_name),
                parse_mode="HTML",
                reply_markup=types.ReplyKeyboardRemove()
            )

@dp.message(F.text == "🔑 ساخت لایسنس جدید")
async def create_license(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.clear()
    license_selection[message.chat.id] = {"index": 0, "custom_minutes": None}

    sent = await message.answer(
        "⏱ انتخاب تایم لایسنس",
        reply_markup=build_license_time_kb(0)
    )
    license_selection[message.chat.id]["message_id"] = sent.message_id


@dp.message(LicenseCreateState.waiting_custom_minutes)
async def receive_custom_license_minutes(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = message.text.strip() if message.text else ""
    if not text.isdigit() or int(text) <= 0:
        await message.answer("❌ لطفاً فقط یک عدد صحیح و مثبت به دقیقه ارسال کنید.")
        return

    minutes = int(text)
    chat_id = message.chat.id
    sel = license_selection.get(chat_id)

    if not sel:
        await message.answer("❌ ابتدا روی «🔑 ساخت لایسنس جدید» بزنید.")
        await state.clear()
        return

    sel["custom_minutes"] = minutes
    await state.clear()

    time_text = license_time_label(sel["index"], minutes)
    new_text = f"⏱ تایم شد این: {time_text}"
    new_kb = build_license_time_kb(sel["index"], minutes)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=sel["message_id"],
            text=new_text,
            reply_markup=new_kb
        )
    except Exception:
        sent = await message.answer(new_text, reply_markup=new_kb)
        sel["message_id"] = sent.message_id


# ====================== LICENSE CHECK ======================
@dp.message(F.text.regexp(LICENSE_REGEX))
async def check_license(message: types.Message):
    if is_admin(message.from_user.id):
        return

    if is_user_banned(message.from_user.id):
        await message.answer("❌ شما از استفاده از ربات بن شده‌اید.")
        return

    session = Session()
    try:
        license_glb = session.query(License).filter_by(
            key=message.text.strip(), used=False
        ).first()

        if license_glb:
            license_glb.used = True
            license_glb.user_id = message.from_user.id
            license_glb.username = message.from_user.username or "بدون یوزرنیم"
            license_glb.used_at = datetime.utcnow()
            if license_glb.duration_minutes:
                license_glb.expires_at = license_glb.used_at + timedelta(minutes=license_glb.duration_minutes)
            else:
                license_glb.expires_at = None
            session.commit()

            first_name = message.from_user.first_name or "دوست عزیز"
            await message.answer(
                build_welcome_activated_text(first_name),
                parse_mode="HTML",
                reply_markup=user_main_keyboard(message.from_user.id)
            )
        else:
            await message.answer("❌ لایسنس نامعتبر یا قبلاً استفاده شده.")
    finally:
        session.close()


# ====================== اجرای گزینه‌های منو از طریق دکمه‌های شیشه‌ای ======================
@dp.callback_query(F.data.in_(MENU_CALLBACK_TO_TEXT.keys()))
async def handle_main_menu_inline(callback: types.CallbackQuery):
    """
    وقتی کاربر روی یکی از دکمه‌های Inline منوی اصلی می‌زند، همان اتفاقی می‌افتد
    که با زدن دکمه‌ی متنی مشابه در ReplyKeyboard رخ می‌داد؛ چون یک پیام
    مصنوعی با همان متن ساخته و به دیسپچر تحویل می‌دهیم تا هندلر واقعی همان
    گزینه اجرا شود.
    """
    block_msg = get_access_block_message(callback.from_user.id)
    if block_msg:
        await callback.answer(strip_html_tags(block_msg), show_alert=True)
        return

    await callback.answer()

    text = MENU_CALLBACK_TO_TEXT[callback.data]
    fake_message = callback.message.model_copy(update={
        "from_user": callback.from_user,
        "text": text,
        "date": datetime.now(),
    })
    await dp.feed_update(bot, types.Update(update_id=0, message=fake_message))


@dp.callback_query(F.data.startswith("actlic:"))
async def auto_activate_license(callback: types.CallbackQuery):
    """
    دکمه‌ی شیشه‌ای زیر پیام لایسنسِ ساخته‌شده توسط ادمین. کاربر نهایی با زدن این
    دکمه، بدون نیاز به کپی/پیست کردن کد، لایسنس را برای خودش فعال می‌کند.
    """
    key = callback.data.split("actlic:", 1)[1]
    user = callback.from_user

    if is_admin(user.id):
        await callback.answer("این دکمه مخصوص فعال‌سازی توسط کاربر نهایی است.", show_alert=True)
        return

    if is_user_banned(user.id):
        await callback.answer("❌ شما از استفاده از ربات بن شده‌اید.", show_alert=True)
        return

    session = Session()
    try:
        license_glb = session.query(License).filter_by(key=key, used=False).first()
        if not license_glb:
            await callback.answer("❌ این لایسنس نامعتبر است یا قبلاً استفاده شده.", show_alert=True)
            return

        license_glb.used = True
        license_glb.user_id = user.id
        license_glb.username = user.username or "بدون یوزرنیم"
        license_glb.used_at = datetime.utcnow()
        if license_glb.duration_minutes:
            license_glb.expires_at = license_glb.used_at + timedelta(minutes=license_glb.duration_minutes)
        else:
            license_glb.expires_at = None
        session.commit()
    finally:
        session.close()

    await callback.answer("✅ لایسنس شما با موفقیت فعال شد!", show_alert=True)

    # وقتی این دکمه از طریق Inline Mode ارسال شده باشد (ادمین ربات را در یک
    # چت با @نام‌کاربری تگ کرده)، تلگرام هیچ‌وقت callback.message را پر
    # نمی‌کند و به‌جایش فقط callback.inline_message_id را می‌فرستد. باید هر
    # دو حالت را پوشش بدهیم وگرنه با AttributeError کرش می‌کند.
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    elif callback.inline_message_id:
        try:
            await bot.edit_message_reply_markup(
                inline_message_id=callback.inline_message_id,
                reply_markup=None
            )
        except Exception:
            pass

    # پیام خوش‌آمدگویی و منوی اصلی همیشه باید در چت خصوصی کاربر با ربات
    # ارسال شود (نه در چتی که دکمه‌ی Inline در آن کلیک شده)، چون آن چت
    # ممکن است اصلاً یک گروه یا چت دیگری باشد که ربات نمی‌تواند پیام
    # معمولی در آن بفرستد یا اصلاً منطقی نیست منوی شخصی آنجا نمایش داده شود.
    target_chat_id = user.id
    first_name = user.first_name or "دوست عزیز"
    try:
        await bot.send_message(
            target_chat_id,
            build_welcome_activated_text(first_name),
            parse_mode="HTML",
            reply_markup=user_main_keyboard(user.id)
        )
    except Exception:
        # کاربر ممکن است هنوز چت خصوصی با ربات را استارت نکرده باشد
        pass


# ====================== ساخت و ارسال لایسنس با تگ کردن بات (Inline Mode) ======================
# ادمین در هر پیوی/چتی کافیه بنویسه: @نام‌کاربری‌بات و یکی از گزینه‌ها رو انتخاب کنه
# تا لایسنس همون‌جا ساخته و ارسال بشه، بدون نیاز به رفتن به پنل ادمین.
# نکته: برای اینکه انتخاب کاربر (chosen_inline_result) هم قابل ردیابی باشه بهتره
# در بات‌فادر با دستور /setinlinefeedback درصد گزارش‌دهی رو (مثلاً 100) فعال کنید.
# ====================== ساخت و ارسال لایسنس با تگ کردن بات (Inline Mode) ======================
# ادمین در هر پیوی/چتی کافیه بنویسه: @نام‌کاربری‌بات و یکی از گزینه‌ها رو انتخاب کنه
# تا لایسنس همون‌جا ساخته و ارسال بشه، بدون نیاز به رفتن به پنل ادمین.
# هر بار که این کوئری اجرا میشه، لایسنس‌های کاملاً تازه ساخته می‌شوند (بدون کش) تا
# هیچ‌وقت یک لایسنسِ قبلاً مصرف‌شده دوباره نمایش داده نشود.


@dp.inline_query()
async def handle_inline_license_creation(inline_query: types.InlineQuery):
    if not is_admin(inline_query.from_user.id):
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    query_text = inline_query.query.strip()

    options = []
    if query_text.isdigit() and int(query_text) > 0:
        days = int(query_text)
        options.append({"label": f"📅 {days} روز", "duration_minutes": days * 24 * 60})
    else:
        options.append({"label": "♾️ همیشگی", "duration_minutes": None})
        for d in (5, 10, 15, 30):
            options.append({"label": f"📅 {d} روز", "duration_minutes": d * 24 * 60})

    session = Session()
    try:
        for opt in options:
            key = generate_license()
            session.add(License(key=key, duration_minutes=opt["duration_minutes"]))
            opt["key"] = key
        session.commit()
    finally:
        session.close()

    results = []
    for opt in options:
        results.append(
            types.InlineQueryResultArticle(
                id=uuid.uuid4().hex,  # همیشه یکتا، تا تلگرام هیچ‌وقت نتیجه‌ی قدیمی رو کش نکنه
                title=f"ساخت لایسنس {opt['label']}",
                description=f"کد: {opt['key']}",
                input_message_content=types.InputTextMessageContent(
                    message_text=build_license_delivery_text(opt["key"], opt["label"]),
                    parse_mode="HTML"
                ),
                reply_markup=build_license_delivery_kb(opt["key"])
            )
        )

    await inline_query.answer(results, cache_time=0, is_personal=True)


# ====================== BROADCAST ======================
@dp.message(F.text == "📢 اطلاع‌رسانی")
async def start_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(BroadcastState.waiting_message)
    await message.answer(
        "📨 پیام خود را ارسال کنید.\n\n"
        "می‌توانید متن، عکس، ویدیو یا فایل بفرستید."
    )


@dp.message(BroadcastState.waiting_message)
async def receive_broadcast_message(message: types.Message, state: FSMContext):
    content = {}

    if message.text:
        content["type"] = "text"
        content["text"] = message.text

    elif message.photo:
        content["type"] = "photo"
        content["file_id"] = message.photo[-1].file_id
        content["caption"] = message.caption or ""

    elif message.video:
        content["type"] = "video"
        content["file_id"] = message.video.file_id
        content["caption"] = message.caption or ""

    elif message.document:
        content["type"] = "document"
        content["file_id"] = message.document.file_id
        content["caption"] = message.caption or ""

    else:
        await message.answer("❌ فرمت پیام پشتیبانی نمی‌شود.")
        return

    await state.update_data(content=content, buttons=[])

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
    keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="➕ افزودن دکمه", callback_data="add_btn")])
    keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="✔️ تکمیل و ارسال", callback_data="finish")])
    keyboard.inline_keyboard.append([types.InlineKeyboardButton(text="🔙 بازگشت", callback_data="back")])

    await state.set_state(BroadcastState.waiting_buttons)
    await message.answer("پیام ذخیره شد.\n\nاکنون می‌توانید دکمه اضافه کنید.", reply_markup=keyboard)


@dp.callback_query(F.data == "finish")
async def finish_broadcast(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    content = data.get("content")
    buttons = data.get("buttons", [])

    if not content:
        await callback.answer("❌ محتوا پیدا نشد!", show_alert=True)
        return

    kb = None
    if buttons:
        inline_buttons = []
        for btn in buttons:
            if btn["action"].startswith("copy:"):
                msg_id = btn["action"].replace("copy:", "")
                inline_buttons.append([
                    types.InlineKeyboardButton(
                        text=btn["title"],
                        callback_data=f"copy_{msg_id}"
                    )
                ])
            else:
                inline_buttons.append([
                    types.InlineKeyboardButton(
                        text=btn["title"],
                        url=btn["action"]
                    )
                ])
        kb = types.InlineKeyboardMarkup(inline_keyboard=inline_buttons)

    session = Session()
    try:
        users = session.query(License).filter(License.used == True).all()
        success = 0
        failed = 0

        for u in users:
            if not u.user_id:
                failed += 1
                continue

            if u.banned:
                failed += 1
                continue
                
            try:
                if content["type"] == "text":
                    await bot.send_message(
                        u.user_id,
                        content["text"],
                        reply_markup=kb,
                        disable_web_page_preview=True
                    )
                elif content["type"] == "photo":
                    await bot.send_photo(
                        u.user_id,
                        content["file_id"],
                        caption=content.get("caption", ""),
                        reply_markup=kb
                    )
                elif content["type"] == "video":
                    await bot.send_video(
                        u.user_id,
                        content["file_id"],
                        caption=content.get("caption", ""),
                        reply_markup=kb
                    )
                elif content["type"] == "document":
                    await bot.send_document(
                        u.user_id,
                        content["file_id"],
                        caption=content.get("caption", ""),
                        reply_markup=kb
                    )
                success += 1

                await asyncio.sleep(0.05)

            except Exception as e:
                failed += 1
                print(f"Failed to send to {u.user_id}: {e}")

        await callback.message.edit_text(
            f"✅ اطلاع‌رسانی تمام شد!\n\n"
            f"✅ موفق: {success}\n"
            f"❌ ناموفق: {failed}\n"
            f"👥 کل کاربران: {len(users)}"
        )

    except Exception as e:
        await callback.message.edit_text(f"❌ خطای کلی: {e}")
    finally:
        session.close()
        await state.clear()


@dp.callback_query(F.data == "back")
async def back_to_admin(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = admin_main_keyboard()
    await callback.message.answer("به منوی ادمین برگشتی.", reply_markup=keyboard)


@dp.callback_query(F.data == "add_btn")
async def ask_button(callback: types.CallbackQuery):
    await callback.message.answer(
        "فرمت دکمه:\n\n"
        "`عنوان دکمه | لینک`\n"
        "یا\n"
        "`عنوان دکمه | copy:MESSAGE_ID`\n",
        parse_mode="Markdown"
    )


@dp.message(F.text.contains("|"), BroadcastState.waiting_buttons)
async def add_button(message: types.Message, state: FSMContext):
    title, action = message.text.split("|", 1)
    title = title.strip()
    action = action.strip()

    data = await state.get_data()
    buttons = data["buttons"]

    buttons.append({"title": title, "action": action})
    await state.update_data(buttons=buttons)

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➕ افزودن دکمه", callback_data="add_btn")],
        [types.InlineKeyboardButton(text="✔️ تکمیل و ارسال", callback_data="finish")],
        [types.InlineKeyboardButton(text="🔙 بازگشت", callback_data="back")]
    ])

    await message.answer(f"دکمه «{title}» اضافه شد.", reply_markup=keyboard)


# ===================== COPY BUTTON ======================
@dp.callback_query(F.data.startswith("copy_"))
async def copy_message_handler(callback: types.CallbackQuery):
    msg_id = callback.data.replace("copy_", "")
    try:
        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=ADMIN_ID,
            message_id=int(msg_id)
        )
    except:
        await callback.answer("❌ پیام یافت نشد.", show_alert=True)


# ====================== ADMIN MANAGEMENT PANEL ======================
@dp.message(F.text == "🛠 سیستم مدیریت")
async def open_management_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    kb = build_management_panel_kb()
    await message.answer("🛠 پنل مدیریت کاربران:", reply_markup=kb)


# ====================== ADMIN TEST USER PANEL ======================
@dp.message(F.text == "🧪 تست پنل کاربر")
async def test_user_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "🧪 <b>حالت تست پنل کاربر</b>\n\n"
        "الان دکمه‌های کاربر رو می‌بینی. هر دکمه‌ای بزنی عیناً مثل یه کاربر عادی کار می‌کنه.\n\n"
        "برای برگشت به پنل ادمین دستور /admin رو بزن.",
        parse_mode="HTML",
        reply_markup=user_main_keyboard(message.from_user.id)
    )


@dp.message(Command("admin"))
async def cmd_back_to_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "👑 برگشتی به پنل ادمین.",
        reply_markup=admin_main_keyboard()
    )


@dp.callback_query(F.data.startswith("{"))
async def management_router(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    try:
        data = parse_cb(callback.data)
    except Exception:
        await callback.answer("خطای داده.", show_alert=True)
        return

    action = data.get("a")

    if action == "panel":
        kb = build_management_panel_kb()
        await callback.message.edit_text("🛠 پنل مدیریت کاربران:", reply_markup=kb)

    elif action == "list":
        page = int(data.get("p", 1))
        await send_users_page(callback, page, banned_only=False)

    elif action == "banned":
        page = int(data.get("p", 1))
        await send_users_page(callback, page, banned_only=True)

    elif action == "profile":
        uid = int(data.get("u"))
        await send_user_profile(callback, uid)

    elif action == "ban":
        uid = int(data.get("u"))
        session = Session()
        try:
            licenses = session.query(License).filter(License.user_id == uid).all()
            if not licenses:
                await callback.answer("کاربر یافت نشد.", show_alert=True)
                return
            for lic in licenses:
                lic.banned = True
            session.commit()
        finally:
            session.close()
        await send_user_profile(callback, uid)

    elif action == "unban":
        uid = int(data.get("u"))
        session = Session()
        try:
            licenses = session.query(License).filter(License.user_id == uid).all()
            if not licenses:
                await callback.answer("کاربر یافت نشد.", show_alert=True)
                return
            for lic in licenses:
                lic.banned = False
            session.commit()
        finally:
            session.close()
        await send_user_profile(callback, uid)

    elif action == "search":
        await state.set_state(AdminState.waiting_search)
        await callback.message.edit_text(
            "🔍 آیدی عددی، یوزرنیم (بدون @) یا کد لایسنس را ارسال کنید.\n\n"
            "مثال‌ها:\n"
            "`123456789`\n"
            "`testuser`\n"
            "`ABCD-EFGH-IJKL-MNOP`",
            parse_mode="Markdown"
        )

    elif action == "lic_cycle":
        chat_id = callback.message.chat.id
        sel = license_selection.setdefault(chat_id, {"index": 0, "custom_minutes": None})
        sel["index"] = (sel["index"] + 1) % len(LICENSE_TIME_OPTIONS)
        sel["message_id"] = callback.message.message_id
        sel["custom_minutes"] = None
        _, value = LICENSE_TIME_OPTIONS[sel["index"]]

        if value == "custom":
            await state.set_state(LicenseCreateState.waiting_custom_minutes)
            await callback.message.edit_text("⏱ تایم لایسنس رو به دقیقه وارد کنید:")
        else:
            await state.clear()
            await callback.message.edit_text(
                "⏱ انتخاب تایم لایسنس",
                reply_markup=build_license_time_kb(sel["index"])
            )
        await callback.answer()

    elif action == "lic_build":
        chat_id = callback.message.chat.id
        sel = license_selection.get(chat_id)
        if not sel:
            await callback.answer("❌ ابتدا روی «🔑 ساخت لایسنس جدید» بزنید.", show_alert=True)
            return

        _, value = LICENSE_TIME_OPTIONS[sel["index"]]
        if value == "custom" and not sel.get("custom_minutes"):
            await callback.answer("❌ ابتدا تایم دلخواه را وارد کنید.", show_alert=True)
            return

        duration_minutes = license_time_minutes(sel["index"], sel.get("custom_minutes"))
        time_text = license_time_label(sel["index"], sel.get("custom_minutes"))

        key = generate_license()
        session = Session()
        try:
            session.add(License(key=key, duration_minutes=duration_minutes))
            session.commit()
        finally:
            session.close()

        await callback.message.edit_text(f"✅ لایسنس با تایم «{time_text}» ساخته شد.")

        await callback.message.answer(
            build_license_delivery_text(key, time_text),
            parse_mode='HTML',
            reply_markup=build_license_delivery_kb(key)
        )

        license_selection.pop(chat_id, None)
        await callback.answer()

    elif action == "back":
        kb = build_management_panel_kb()
        await callback.message.edit_text("🛠 پنل مدیریت کاربران:", reply_markup=kb)


@dp.message(AdminState.waiting_search)
async def admin_search_user(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    query = message.text.strip()
    session = Session()
    try:
        user = None

        if query.isdigit():
            user = session.query(License).filter(License.user_id == int(query)).first()
        if not user and not query.isdigit():
            user = session.query(License).filter(License.username == query).first()
        if not user and "-" in query:
            user = session.query(License).filter(License.key == query).first()

        if not user:
            await message.answer("❌ کاربر یا لایسنس یافت نشد.")
            await state.clear()
            return

        uid = user.user_id
        if not uid:
            await message.answer("❌ این لایسنس هنوز به کاربری متصل نشده.")
            await state.clear()
            return

        await state.clear()
        fake_callback = types.CallbackQuery(
            id="0",
            from_user=message.from_user,
            chat_instance="",
            message=message,
            data=make_cb("profile", u=uid)
        )
        await management_router(fake_callback, state)

    finally:
        session.close()


# ====================== MODES ======================
@dp.message(F.text == "📦 ساخت HUD Overlay از ریسورس پک")
async def ask_for_pack(message: types.Message):
    block_msg = get_access_block_message(message.from_user.id)
    if block_msg:
        await message.answer(block_msg, parse_mode="HTML")
        return

    user_modes[message.from_user.id] = "resource_pack"
    await message.answer(
        "📦 <b>ساخت HUD Overlay از ریسورس پک</b>\n\n"
        "📤 فایل ریسورس پکتو همینجا بفرست.\n"
        "فقط فرمت <code>.zip</code> یا <code>.mcpack</code> قبول میشه.\n\n"
        "<blockquote>💡 بعد از دریافت فایل، یه صفحه برای تنظیم درصد نوار XP باز میشه؛ "
        "خودت درصد رو تنظیم می‌کنی و بعدش پردازش شروع میشه.\n"
        "⏱ زمان تقریبی پردازش: ۱۰ تا ۲۵ ثانیه (بسته به حجم پک)</blockquote>",
        parse_mode="HTML"
    )


@dp.message(F.text == "🧊 ساخت آیتم سه‌بعدی ماینکرافت")
async def minecraft_3d(message: types.Message):
    block_msg = get_access_block_message(message.from_user.id)
    if block_msg:
        await message.answer(block_msg, parse_mode="HTML")
        return

    user_modes[message.from_user.id] = "minecraft_3d"
    await message.answer(
        "🧊 <b>ساخت آیتم سه‌بعدی ماینکرافت</b>\n\n"
        "📤 فایل <b>PNG</b> آیتم موردنظرتو بفرست.\n\n"
        "<blockquote>💡 از روی همون تکسچر، یه مدل سه‌بعدی (OBJ) ساخته میشه و به‌صورت فایل برات ارسال میشه.\n"
        "⏱ زمان تقریبی ساخت: ۵ تا ۱۵ ثانیه</blockquote>",
        parse_mode="HTML"
    )


@dp.message(F.text == "🔄 تبدیل JSON به OBJ")
async def json_to_obj_mode(message: types.Message):
    block_msg = get_access_block_message(message.from_user.id)
    if block_msg:
        await message.answer(block_msg, parse_mode="HTML")
        return

    user_modes[message.from_user.id] = "json_to_obj"
    user_data.pop(message.from_user.id, None)
    await message.answer(
        "🔄 <b>تبدیل JSON به OBJ</b>\n\n"
        "📤 <b>مرحله ۱ از ۲</b> — فایل <b>JSON</b> مدل ماینکرافتو بفرست.\n\n"
        "<blockquote>💡 بعدش فایل تکسچر (PNG) رو هم ازت می‌خوایم و در نهایت یه فایل OBJ + MTL همراه با پیش‌نمایش تصویری تحویلت میدیم.\n"
        "⏱ زمان تقریبی کل فرایند: ۱۰ تا ۲۰ ثانیه</blockquote>",
        parse_mode="HTML"
    )

# ====================== MINECRAFT ASSETS DOWNLOADER ======================
@dp.message(F.text == "📥 گرفتن فایل‌های ماینکرافت")
async def minecraft_assets_mode(message: types.Message):
    block_msg = get_access_block_message(message.from_user.id)
    if block_msg:
        await message.answer(block_msg, parse_mode="HTML")
        return

    user_modes[message.from_user.id] = "minecraft_assets"
    await message.answer(
        "📥 <b>گرفتن فایل‌های ماینکرافت</b>\n\n"
        "اسم آیتم موردنظرتو بنویس، مثال:\n"
        "<code>diamond_sword</code>\n"
        "<code>copper tools</code>\n"
        "<code>spear</code>\n"
        "<code>spear_in_hand</code>\n"
        "<code>gold armor</code>\n"
        "<code>armor layers</code>\n"
        "<code>armors</code>\n"        
        "<code>oak_planks</code>\n\n"
    
        "• برای زره بنویس: <code>iron armor</code> یا <code>diamond armor</code>\n"
        "• برای نیزه بنویس: <code>spear</code> یا <code>spear in hand</code>\n"
        "• برای ابزار بنویس: <code>netherite tools</code> یا <code>...</code>\n"
        "• برای لایه آرمور بنویس: <code>armor layer</code>\n"
        "• برای ore بنویس: <code>ore</code> یا <code>copper_ore</code>\n\n"
        "<blockquote>💡 بعد از جستجو، لیست فایل‌های پیدا شده رو با دکمه نشونت میدیم تا انتخاب کنی و برات بفرستیم (حداکثر ۵ فایل هر بار).\n"
        "⏱ زمان تقریبی جستجو: ۲ تا ۵ ثانیه</blockquote>",
    
        parse_mode="HTML"
    )
    
# ====================== FILE HANDLER ======================
# StateFilter(None) = فقط وقتی کاربر توی هیچ FSM state ای نیست اجرا میشه
# هندلرهای ResourcePackManualState چون state دارن اولویت بالاتری دارن
@dp.message(F.document, StateFilter(None))
async def handle_document(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    block_msg = get_access_block_message(user_id)
    if block_msg:
        await message.answer(block_msg, parse_mode="HTML")
        return

    mode = user_modes.get(user_id)
    doc = message.document

    if not doc or not mode:
        return

    # RESOURCE PACK
    if mode == "resource_pack":
        if not (doc.file_name.endswith(".zip") or doc.file_name.endswith(".mcpack")):
            await message.answer(
                "❌ <b>فرمت فایل اشتباهه</b>\n\n"
                "<blockquote>فقط فایل <b>ZIP</b> یا <b>MCPACK</b> قبول میشه.</blockquote>",
                parse_mode="HTML"
            )
            return

        # بررسی حجم فایل قبل از دانلود (20MB)
        FILE_SIZE_LIMIT = 20 * 1024 * 1024
        if doc.file_size and doc.file_size > FILE_SIZE_LIMIT:
            await _send_manual_upload_prompt(message, user_id, reason="حجم فایل بیشتر از ۲۰ مگابایته و تلگرام اجازه دانلود نمیده")
            return

        input_path = os.path.join(INPUT_DIR, doc.file_name)
        output_name = os.path.splitext(doc.file_name)[0] + "_ui.png"
        output_path = os.path.join(OUTPUT_DIR, output_name)

        try:
            file = await bot.get_file(doc.file_id)
            await bot.download_file(file.file_path, destination=input_path)
        except Exception as e:
            err = str(e).lower()
            if "too big" in err or "large" in err or "file is too big" in err:
                await _send_manual_upload_prompt(message, user_id, reason="فایل برای دانلود در تلگرام خیلی بزرگه")
            else:
                await message.answer(f"❌ خطا در دریافت فایل:\n{e}")
            return

        # فایل با موفقیت دانلود شد. به‌جای پردازش فوری، مستقیم صفحه‌ی تنظیم درصد نوار XP نشون داده می‌شه.
        await state.update_data(
            rp_input_path=input_path,
            rp_output_path=output_path,
            rp_doc_name=doc.file_name,
            rp_xp_percent_int=DEFAULT_XP_PERCENT_INT,
        )
        await state.set_state(ResourcePackSettingsState.choosing_percent)
        await message.answer(
            build_rp_percent_text(DEFAULT_XP_PERCENT_INT),
            parse_mode="HTML",
            reply_markup=build_rp_percent_kb(DEFAULT_XP_PERCENT_INT)
        )

    # MINECRAFT 3D ITEM
    elif mode == "minecraft_3d":
        if not doc.file_name.lower().endswith(".png"):
            await message.answer(
                "❌ <b>فرمت فایل اشتباهه</b>\n\n"
                "<blockquote>فقط فایل <b>PNG</b> قبول میشه.</blockquote>",
                parse_mode="HTML"
            )
            return

        await message.answer(
            "🔄 <b>در حال ساخت مدل سه‌بعدی...</b>\n\n"
            "<blockquote>⏱ چند ثانیه صبر کن، معمولاً ۵ تا ۱۵ ثانیه طول می‌کشه.</blockquote>",
            parse_mode="HTML"
        )
        input_path = os.path.join(INPUT_DIR, doc.file_name)
        output_obj = os.path.join(OUTPUT_DIR, os.path.splitext(doc.file_name)[0] + ".obj")

        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, destination=input_path)

        try:
            output_file = await run_item3d(input_path, output_obj)
            user_modes.pop(user_id, None)
            await message.answer_document(FSInputFile(output_file), caption="✅ مدل سه‌بعدی با موفقیت ساخته شد!")
            if os.path.exists(input_path):
                os.remove(input_path)
        except Exception as e:
            await message.answer(f"❌ خطا در ساخت مدل:\n{str(e)}")

    # JSON TO OBJ - STEP 1
    elif mode == "json_to_obj":
        if not doc.file_name.lower().endswith(".json"):
            await message.answer(
                "❌ <b>فرمت فایل اشتباهه</b>\n\n"
                "<blockquote>فقط فایل <b>JSON</b> قبول میشه.</blockquote>",
                parse_mode="HTML"
            )
            return

        await message.answer(
            "✅ <b>JSON دریافت شد!</b>\n\n"
            "📤 <b>مرحله ۲ از ۲</b> — حالا فایل تکسچر (<b>PNG</b>) رو بفرست.\n\n"
            "<blockquote>💡 بعد از دریافت تکسچر، مدل OBJ + MTL ساخته میشه و یه پیش‌نمایش تصویری هم برات می‌فرستیم.\n"
            "⏱ زمان تقریبی: ۱۰ تا ۲۰ ثانیه</blockquote>",
            parse_mode="HTML"
        )

        json_path = os.path.join(INPUT_DIR, doc.file_name)
        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, destination=json_path)

        user_data[user_id] = {
            "json_path": json_path,
            "base_name": os.path.splitext(doc.file_name)[0]
        }
        user_modes[user_id] = "json_to_obj_waiting_texture"

    # JSON TO OBJ - STEP 2
    elif mode == "json_to_obj_waiting_texture":
        if not doc.file_name.lower().endswith(".png"):
            await message.answer(
                "❌ <b>فرمت فایل اشتباهه</b>\n\n"
                "<blockquote>فقط فایل <b>PNG</b> قبول میشه.</blockquote>",
                parse_mode="HTML"
            )
            return

        await message.answer(
            "🔄 <b>در حال ساخت OBJ + MTL + ZIP...</b>\n\n"
            "<blockquote>⏱ چند ثانیه صبر کن، معمولاً ۱۰ تا ۲۰ ثانیه طول می‌کشه.</blockquote>",
            parse_mode="HTML"
        )

        data = user_data.get(user_id)
        if not data:
            await message.answer("❌ خطا: اطلاعات مدل پیدا نشد.")
            return

        json_path = data["json_path"]
        base_name = data["base_name"]
        texture_path = os.path.join(INPUT_DIR, doc.file_name)
        output_obj = os.path.join(OUTPUT_DIR, base_name + ".obj")

        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, destination=texture_path)

        try:
            await run_json_to_obj(json_path, output_obj)

            # ساخت پیش‌نمایش سه‌بعدی ساده (جلو + پشت کنار هم)
            preview_path = os.path.join(OUTPUT_DIR, base_name + "_preview.png")
            try:
                await run_obj_preview(output_obj, texture_path, preview_path)
                await message.answer_photo(
                    FSInputFile(preview_path),
                    caption="👀 پیش‌نمایش مدل (راست: پشت | چپ: جلو)"
                )
            except Exception as preview_err:
                # اگه پیش‌نمایش ساخته نشد، روند اصلی (ارسال ZIP) رو متوقف نکن
                await message.answer(f"⚠️ ساخت پیش‌نمایش با خطا مواجه شد:\n{str(preview_err)[:300]}")
                preview_path = None

            zip_path = create_zip_with_texture(base_name, output_obj, texture_path)

            await message.answer_document(
                FSInputFile(zip_path),
                caption=f"✅ تبدیل با موفقیت انجام شد!\n\n"
                        f"📦 فایل‌ها داخل ZIP:\n"
                        f"• {base_name}.obj\n"
                        f"• {base_name}.mtl\n"
                        f"• {os.path.basename(texture_path)}"
            )

        except Exception as e:
            await message.answer(f"❌ خطا:\n{str(e)}")

        finally:
            cleanup_paths = [json_path, texture_path, output_obj, output_obj.replace('.obj', '.mtl')]
            preview_cleanup = os.path.join(OUTPUT_DIR, base_name + "_preview.png")
            cleanup_paths.append(preview_cleanup)
            for p in cleanup_paths:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except:
                        pass
            user_modes.pop(user_id, None)
            user_data.pop(user_id, None)

# ====================== MINECRAFT ASSETS DOWNLOADER ======================
"""
Minecraft Asset Aliases - EXPANDED v2.0
Covers all vanilla blocks, items, Forge/NeoForge tags, custom spear/copper tools,
deepslate ores, all tool tiers, all armor types, and every category requested.
"""

MC_ASSETS_BASE = "https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/26.1.2/assets/minecraft"

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS – build big lists so the dict stays readable
# ─────────────────────────────────────────────────────────────────────────────

_TIERS        = ["wooden", "stone", "copper", "iron", "golden", "diamond", "netherite"]
_WOOD_TYPES   = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
                 "mangrove", "cherry", "bamboo", "crimson", "warped"]
_STONE_TYPES  = ["stone", "cobblestone", "smooth_stone", "stone_bricks",
                 "mossy_cobblestone", "mossy_stone_bricks", "cracked_stone_bricks",
                 "chiseled_stone_bricks", "deepslate", "cobbled_deepslate",
                 "polished_deepslate", "deepslate_bricks", "deepslate_tiles",
                 "cracked_deepslate_bricks", "cracked_deepslate_tiles",
                 "chiseled_deepslate", "reinforced_deepslate"]
_ORE_MATS     = ["coal", "iron", "copper", "gold", "redstone", "lapis",
                 "diamond", "emerald", "nether_quartz", "nether_gold", "ancient_debris"]
_WOOL_COLORS  = ["white", "orange", "magenta", "light_blue", "yellow", "lime",
                 "pink", "gray", "light_gray", "cyan", "purple", "blue",
                 "brown", "green", "red", "black"]
_CORAL_TYPES  = ["tube", "brain", "bubble", "fire", "horn"]
_FISH_TYPES   = ["cod", "salmon", "tropical_fish", "pufferfish"]
_MUSIC_DISCS  = ["13", "cat", "blocks", "chirp", "far", "mall", "mellohi",
                 "stal", "strad", "ward", "11", "wait", "otherside", "5",
                 "pigstep", "relic", "creator", "creator_music_box", "precipice"]
_TRIM_MATS    = ["quartz", "iron", "netherite", "redstone", "copper", "gold",
                 "emerald", "diamond", "lapis", "amethyst", "resin"]
_TRIM_TMPL    = ["coast", "dune", "eye", "host", "raiser", "rib", "sentry",
                 "shaper", "silence", "snout", "spire", "tide", "vex",
                 "ward", "wayfinder", "wild", "flow", "bolt"]
_DYE_COLORS   = _WOOL_COLORS  # same 16 colours
_BANNER_COLORS = _WOOL_COLORS
_BED_COLORS   = _WOOL_COLORS
_CANDLE_COLORS = _WOOL_COLORS
_TERRACOTTA_C  = _WOOL_COLORS
_CONCRETE_C    = _WOOL_COLORS
_GLASS_C       = _WOOL_COLORS

def _tools(tier: str):
    return [f"{tier}_pickaxe", f"{tier}_axe", f"{tier}_shovel",
            f"{tier}_hoe", f"{tier}_sword"]

def _armor(tier: str):
    prefix = "golden" if tier == "gold" else tier
    return [f"{prefix}_helmet", f"{prefix}_chestplate",
            f"{prefix}_leggings", f"{prefix}_boots"]

def _colored(base: str, colors=_WOOL_COLORS):
    return [f"{c}_{base}" for c in colors]

def _ores(deepslate=False):
    prefix = "deepslate_" if deepslate else ""
    return [f"{prefix}{m}_ore" for m in _ORE_MATS
            if not (deepslate and m in ("nether_quartz", "nether_gold", "ancient_debris"))]

def _logs():      return [f"{w}_log" for w in _WOOD_TYPES]
def _planks():    return [f"{w}_planks" for w in _WOOD_TYPES]
def _leaves():    return [f"{w}_leaves" for w in _WOOD_TYPES]
def _saplings():  return [f"{w}_sapling" for w in _WOOD_TYPES]
def _stairs(mat): return [f"{m}_stairs" for m in mat]
def _slabs(mat):  return [f"{m}_slab" for m in mat]
def _walls(mat):  return [f"{m}_wall" for m in mat]
def _fences():
    return [f"{w}_fence" for w in _WOOD_TYPES] + ["nether_brick_fence"]
def _fence_gates():
    return [f"{w}_fence_gate" for w in _WOOD_TYPES]
def _doors():
    return [f"{w}_door" for w in _WOOD_TYPES] + \
           ["iron_door", "copper_door", "exposed_copper_door",
            "weathered_copper_door", "oxidized_copper_door", "waxed_copper_door"]
def _trapdoors():
    return [f"{w}_trapdoor" for w in _WOOD_TYPES] + \
           ["iron_trapdoor", "copper_trapdoor", "exposed_copper_trapdoor",
            "weathered_copper_trapdoor", "oxidized_copper_trapdoor", "waxed_copper_trapdoor"]
def _buttons():
    return [f"{w}_button" for w in _WOOD_TYPES] + \
           ["stone_button", "polished_blackstone_button"]
def _pressure_plates():
    return [f"{w}_pressure_plate" for w in _WOOD_TYPES] + \
           ["stone_pressure_plate", "light_weighted_pressure_plate",
            "heavy_weighted_pressure_plate", "polished_blackstone_pressure_plate"]

# ─────────────────────────────────────────────────────────────────────────────
# MAIN ALIAS DICT
# ─────────────────────────────────────────────────────────────────────────────

ITEM_ALIASES: dict[str, list[str]] = {

    # ══════════════════════════════════════════════════════════════════════════
    # ORES  (normal + deepslate)
    # ══════════════════════════════════════════════════════════════════════════
    "ore":             _ores(),
    "ores":            _ores(),
    "_ore":            _ores(),
    " ore":            _ores(),
    "deepslate_ore":   _ores(deepslate=True),
    "deepslate_ores":  _ores(deepslate=True),
    "deepslate ores":  _ores(deepslate=True),

    # individual ores – both variants
    **{f"{m}_ore":           [f"{m}_ore", f"deepslate_{m}_ore"] for m in _ORE_MATS
       if m not in ("nether_quartz", "nether_gold", "ancient_debris")},
    **{f"{m} ore":           [f"{m}_ore", f"deepslate_{m}_ore"] for m in _ORE_MATS
       if m not in ("nether_quartz", "nether_gold", "ancient_debris")},
    **{f"deepslate_{m}_ore": [f"deepslate_{m}_ore"] for m in _ORE_MATS
       if m not in ("nether_quartz", "nether_gold", "ancient_debris")},
    **{f"deepslate {m} ore": [f"deepslate_{m}_ore"] for m in _ORE_MATS
       if m not in ("nether_quartz", "nether_gold", "ancient_debris")},

    # nether-only ores
    "nether_quartz_ore":  ["nether_quartz_ore"],
    "nether quartz ore":  ["nether_quartz_ore"],
    "nether_gold_ore":    ["nether_gold_ore"],
    "nether gold ore":    ["nether_gold_ore"],
    "ancient_debris":     ["ancient_debris"],
    "ancient debris":     ["ancient_debris"],
    "nether ores":        ["nether_quartz_ore", "nether_gold_ore", "ancient_debris"],

    # ══════════════════════════════════════════════════════════════════════════
    # LOGS / WOOD
    # ══════════════════════════════════════════════════════════════════════════
    "log":    _logs(),
    "logs":   _logs(),
    "wood":   _logs() + _planks(),

    **{f"{w}_log":   [f"{w}_log"]   for w in _WOOD_TYPES},
    **{f"{w} log":   [f"{w}_log"]   for w in _WOOD_TYPES},
    **{f"{w}_wood":  [f"{w}_log"]   for w in _WOOD_TYPES},
    **{f"{w} wood":  [f"{w}_log"]   for w in _WOOD_TYPES},

    # stripped logs
    **{f"stripped_{w}_log":  [f"stripped_{w}_log"]  for w in _WOOD_TYPES},
    **{f"stripped {w} log":  [f"stripped_{w}_log"]  for w in _WOOD_TYPES},
    "stripped_logs": [f"stripped_{w}_log" for w in _WOOD_TYPES],
    "stripped logs": [f"stripped_{w}_log" for w in _WOOD_TYPES],

    # ══════════════════════════════════════════════════════════════════════════
    # PLANKS
    # ══════════════════════════════════════════════════════════════════════════
    "planks":  _planks(),
    **{f"{w}_planks": [f"{w}_planks"] for w in _WOOD_TYPES},
    **{f"{w} planks": [f"{w}_planks"] for w in _WOOD_TYPES},

    # ══════════════════════════════════════════════════════════════════════════
    # LEAVES
    # ══════════════════════════════════════════════════════════════════════════
    "leaves":  _leaves() + ["azalea_leaves", "flowering_azalea_leaves"],
    **{f"{w}_leaves": [f"{w}_leaves"] for w in _WOOD_TYPES},
    **{f"{w} leaves": [f"{w}_leaves"] for w in _WOOD_TYPES},
    "azalea leaves": ["azalea_leaves", "flowering_azalea_leaves"],

    # ══════════════════════════════════════════════════════════════════════════
    # SAPLINGS
    # ══════════════════════════════════════════════════════════════════════════
    "sapling":  _saplings(),
    "saplings": _saplings(),
    **{f"{w}_sapling": [f"{w}_sapling"] for w in _WOOD_TYPES},
    **{f"{w} sapling": [f"{w}_sapling"] for w in _WOOD_TYPES},

    # ══════════════════════════════════════════════════════════════════════════
    # WOOL / CARPET / COLORED BLOCKS
    # ══════════════════════════════════════════════════════════════════════════
    "wool":     _colored("wool"),
    **{f"{c}_wool":  [f"{c}_wool"]  for c in _WOOL_COLORS},
    **{f"{c} wool":  [f"{c}_wool"]  for c in _WOOL_COLORS},

    "carpet":   _colored("carpet"),
    **{f"{c}_carpet": [f"{c}_carpet"] for c in _WOOL_COLORS},
    **{f"{c} carpet": [f"{c}_carpet"] for c in _WOOL_COLORS},

    # ══════════════════════════════════════════════════════════════════════════
    # TERRACOTTA
    # ══════════════════════════════════════════════════════════════════════════
    "terracotta":          ["terracotta"] + _colored("terracotta"),
    "glazed_terracotta":   _colored("glazed_terracotta"),
    "glazed terracotta":   _colored("glazed_terracotta"),
    **{f"{c}_terracotta":         [f"{c}_terracotta"]         for c in _TERRACOTTA_C},
    **{f"{c} terracotta":         [f"{c}_terracotta"]         for c in _TERRACOTTA_C},
    **{f"{c}_glazed_terracotta":  [f"{c}_glazed_terracotta"]  for c in _TERRACOTTA_C},
    **{f"{c} glazed terracotta":  [f"{c}_glazed_terracotta"]  for c in _TERRACOTTA_C},

    # ══════════════════════════════════════════════════════════════════════════
    # CONCRETE + CONCRETE POWDER
    # ══════════════════════════════════════════════════════════════════════════
    "concrete":        _colored("concrete"),
    "concrete_powder": _colored("concrete_powder"),
    "concrete powder": _colored("concrete_powder"),
    **{f"{c}_concrete":        [f"{c}_concrete"]        for c in _CONCRETE_C},
    **{f"{c} concrete":        [f"{c}_concrete"]        for c in _CONCRETE_C},
    **{f"{c}_concrete_powder": [f"{c}_concrete_powder"] for c in _CONCRETE_C},
    **{f"{c} concrete powder": [f"{c}_concrete_powder"] for c in _CONCRETE_C},

    # ══════════════════════════════════════════════════════════════════════════
    # GLASS + GLASS PANES
    # ══════════════════════════════════════════════════════════════════════════
    "glass":       ["glass"] + _colored("stained_glass"),
    "stained_glass": _colored("stained_glass"),
    "stained glass": _colored("stained_glass"),
    "glass_pane":  ["glass_pane"] + _colored("stained_glass_pane"),
    "glass pane":  ["glass_pane"] + _colored("stained_glass_pane"),
    "glass_panes": ["glass_pane"] + _colored("stained_glass_pane"),
    "glass panes": ["glass_pane"] + _colored("stained_glass_pane"),
    **{f"{c}_stained_glass":      [f"{c}_stained_glass"]      for c in _GLASS_C},
    **{f"{c} stained glass":      [f"{c}_stained_glass"]      for c in _GLASS_C},
    **{f"{c}_stained_glass_pane": [f"{c}_stained_glass_pane"] for c in _GLASS_C},
    **{f"{c} stained glass pane": [f"{c}_stained_glass_pane"] for c in _GLASS_C},

    # ══════════════════════════════════════════════════════════════════════════
    # SAND + GRAVEL
    # ══════════════════════════════════════════════════════════════════════════
    "sand":       ["sand", "red_sand"],
    "red_sand":   ["red_sand"],
    "red sand":   ["red_sand"],
    "gravel":     ["gravel"],
    "soul_sand":  ["soul_sand"],
    "soul sand":  ["soul_sand"],
    "soul_soil":  ["soul_soil"],
    "soul soil":  ["soul_soil"],

    # ══════════════════════════════════════════════════════════════════════════
    # STONE VARIANTS
    # ══════════════════════════════════════════════════════════════════════════
    "stone":          _STONE_TYPES,
    "stones":         _STONE_TYPES,
    "cobblestone":    ["cobblestone", "mossy_cobblestone"],
    "deepslate":      [s for s in _STONE_TYPES if "deepslate" in s],
    "blackstone":     ["blackstone", "polished_blackstone", "chiseled_polished_blackstone",
                       "polished_blackstone_bricks", "cracked_polished_blackstone_bricks",
                       "gilded_blackstone"],
    "basalt":         ["basalt", "smooth_basalt", "polished_basalt"],
    "diorite":        ["diorite", "polished_diorite"],
    "granite":        ["granite", "polished_granite"],
    "andesite":       ["andesite", "polished_andesite"],
    "calcite":        ["calcite"],
    "tuff":           ["tuff", "polished_tuff", "tuff_bricks", "chiseled_tuff"],
    "dripstone":      ["dripstone_block", "pointed_dripstone"],

    # ══════════════════════════════════════════════════════════════════════════
    # STAIRS / SLABS / WALLS
    # ══════════════════════════════════════════════════════════════════════════
    "stairs":     _stairs(_STONE_TYPES + _WOOD_TYPES),
    "slabs":      _slabs(_STONE_TYPES + _WOOD_TYPES),
    "walls":      _walls(["cobblestone", "mossy_cobblestone", "stone_brick",
                           "mossy_stone_brick", "andesite", "diorite", "granite",
                           "sandstone", "red_sandstone", "nether_brick",
                           "red_nether_brick", "blackstone", "polished_blackstone",
                           "polished_blackstone_brick", "cobbled_deepslate",
                           "polished_deepslate", "deepslate_brick", "deepslate_tile",
                           "mud_brick", "tuff", "tuff_brick"]),
    **{f"{w}_stairs": [f"{w}_stairs"] for w in _WOOD_TYPES + _STONE_TYPES},
    **{f"{w}_slab":   [f"{w}_slab"]   for w in _WOOD_TYPES + _STONE_TYPES},
    **{f"{w} stairs": [f"{w}_stairs"] for w in _WOOD_TYPES + _STONE_TYPES},
    **{f"{w} slab":   [f"{w}_slab"]   for w in _WOOD_TYPES + _STONE_TYPES},

    # ══════════════════════════════════════════════════════════════════════════
    # FENCES / GATES / DOORS / TRAPDOORS
    # ══════════════════════════════════════════════════════════════════════════
    "fence":        _fences(),
    "fences":       _fences(),
    "fence_gate":   _fence_gates(),
    "fence_gates":  _fence_gates(),
    "fence gate":   _fence_gates(),
    "fence gates":  _fence_gates(),
    "door":         _doors(),
    "doors":        _doors(),
    "trapdoor":     _trapdoors(),
    "trapdoors":    _trapdoors(),
    **{f"{w}_fence":      [f"{w}_fence"]      for w in _WOOD_TYPES},
    **{f"{w}_fence_gate": [f"{w}_fence_gate"] for w in _WOOD_TYPES},
    **{f"{w}_door":       [f"{w}_door"]       for w in _WOOD_TYPES},
    **{f"{w}_trapdoor":   [f"{w}_trapdoor"]   for w in _WOOD_TYPES},
    **{f"{w} fence":      [f"{w}_fence"]      for w in _WOOD_TYPES},
    **{f"{w} fence gate": [f"{w}_fence_gate"] for w in _WOOD_TYPES},
    **{f"{w} door":       [f"{w}_door"]       for w in _WOOD_TYPES},
    **{f"{w} trapdoor":   [f"{w}_trapdoor"]   for w in _WOOD_TYPES},

    # ══════════════════════════════════════════════════════════════════════════
    # BUTTONS / PRESSURE PLATES
    # ══════════════════════════════════════════════════════════════════════════
    "button":           _buttons(),
    "buttons":          _buttons(),
    "pressure_plate":   _pressure_plates(),
    "pressure_plates":  _pressure_plates(),
    "pressure plate":   _pressure_plates(),
    "pressure plates":  _pressure_plates(),
    **{f"{w}_button":         [f"{w}_button"]         for w in _WOOD_TYPES},
    **{f"{w}_pressure_plate": [f"{w}_pressure_plate"] for w in _WOOD_TYPES},
    **{f"{w} button":         [f"{w}_button"]         for w in _WOOD_TYPES},
    **{f"{w} pressure plate": [f"{w}_pressure_plate"] for w in _WOOD_TYPES},

    # ══════════════════════════════════════════════════════════════════════════
    # RAILS
    # ══════════════════════════════════════════════════════════════════════════
    "rail":      ["rail", "powered_rail", "detector_rail", "activator_rail"],
    "rails":     ["rail", "powered_rail", "detector_rail", "activator_rail"],
    "powered_rail":   ["powered_rail"],
    "detector_rail":  ["detector_rail"],
    "activator_rail": ["activator_rail"],

    # ══════════════════════════════════════════════════════════════════════════
    # ICE
    # ══════════════════════════════════════════════════════════════════════════
    "ice":         ["ice", "packed_ice", "blue_ice"],
    "packed_ice":  ["packed_ice"],
    "blue_ice":    ["blue_ice"],
    "packed ice":  ["packed_ice"],
    "blue ice":    ["blue_ice"],

    # ══════════════════════════════════════════════════════════════════════════
    # CORALS
    # ══════════════════════════════════════════════════════════════════════════
    "coral":       [f"{c}_coral"       for c in _CORAL_TYPES] +
                   [f"{c}_coral_block" for c in _CORAL_TYPES] +
                   [f"{c}_coral_fan"   for c in _CORAL_TYPES],
    "corals":      [f"{c}_coral"       for c in _CORAL_TYPES] +
                   [f"{c}_coral_block" for c in _CORAL_TYPES] +
                   [f"{c}_coral_fan"   for c in _CORAL_TYPES],
    "dead_coral":  [f"dead_{c}_coral"       for c in _CORAL_TYPES] +
                   [f"dead_{c}_coral_block" for c in _CORAL_TYPES] +
                   [f"dead_{c}_coral_fan"   for c in _CORAL_TYPES],
    **{f"{c}_coral":       [f"{c}_coral"]       for c in _CORAL_TYPES},
    **{f"{c}_coral_block": [f"{c}_coral_block"] for c in _CORAL_TYPES},
    **{f"{c}_coral_fan":   [f"{c}_coral_fan"]   for c in _CORAL_TYPES},
    **{f"{c} coral":       [f"{c}_coral"]       for c in _CORAL_TYPES},
    **{f"{c} coral block": [f"{c}_coral_block"] for c in _CORAL_TYPES},
    **{f"{c} coral fan":   [f"{c}_coral_fan"]   for c in _CORAL_TYPES},

    # ══════════════════════════════════════════════════════════════════════════
    # NYLIUM / MUSHROOM BLOCKS / FIRE
    # ══════════════════════════════════════════════════════════════════════════
    "nylium":        ["crimson_nylium", "warped_nylium"],
    "crimson_nylium": ["crimson_nylium"],
    "warped_nylium":  ["warped_nylium"],
    "mushroom_block": ["red_mushroom_block", "brown_mushroom_block", "mushroom_stem"],
    "mushroom blocks":["red_mushroom_block", "brown_mushroom_block", "mushroom_stem"],
    "mushroom_grow_block": ["red_mushroom_block", "brown_mushroom_block", "mushroom_stem"],
    "fire":          ["fire", "soul_fire"],
    "soul_fire":     ["soul_fire"],
    "soul fire":     ["soul_fire"],

    # ══════════════════════════════════════════════════════════════════════════
    # BANNERS
    # ══════════════════════════════════════════════════════════════════════════
    "banner":    [f"{c}_banner"      for c in _BANNER_COLORS] +
                 [f"{c}_wall_banner" for c in _BANNER_COLORS],
    "banners":   [f"{c}_banner"      for c in _BANNER_COLORS] +
                 [f"{c}_wall_banner" for c in _BANNER_COLORS],
    **{f"{c}_banner":      [f"{c}_banner"]      for c in _BANNER_COLORS},
    **{f"{c} banner":      [f"{c}_banner"]      for c in _BANNER_COLORS},
    **{f"{c}_wall_banner": [f"{c}_wall_banner"] for c in _BANNER_COLORS},

    # ══════════════════════════════════════════════════════════════════════════
    # BEDS
    # ══════════════════════════════════════════════════════════════════════════
    "bed":   [f"{c}_bed" for c in _BED_COLORS],
    "beds":  [f"{c}_bed" for c in _BED_COLORS],
    **{f"{c}_bed": [f"{c}_bed"] for c in _BED_COLORS},
    **{f"{c} bed": [f"{c}_bed"] for c in _BED_COLORS},

    # ══════════════════════════════════════════════════════════════════════════
    # CANDLES
    # ══════════════════════════════════════════════════════════════════════════
    "candle":   ["candle"] + [f"{c}_candle" for c in _CANDLE_COLORS],
    "candles":  ["candle"] + [f"{c}_candle" for c in _CANDLE_COLORS],
    **{f"{c}_candle": [f"{c}_candle"] for c in _CANDLE_COLORS},
    **{f"{c} candle": [f"{c}_candle"] for c in _CANDLE_COLORS},

    # ══════════════════════════════════════════════════════════════════════════
    # CAULDRONS
    # ══════════════════════════════════════════════════════════════════════════
    "cauldron":  ["cauldron", "water_cauldron", "lava_cauldron", "powder_snow_cauldron"],
    "cauldrons": ["cauldron", "water_cauldron", "lava_cauldron", "powder_snow_cauldron"],
    "lava_cauldron":         ["lava_cauldron"],
    "water_cauldron":        ["water_cauldron"],
    "powder_snow_cauldron":  ["powder_snow_cauldron"],

    # ══════════════════════════════════════════════════════════════════════════
    # CROPS / PLANTS
    # ══════════════════════════════════════════════════════════════════════════
    "crop":   ["wheat", "carrots", "potatoes", "beetroots",
               "melon_stem", "pumpkin_stem", "nether_wart",
               "sweet_berry_bush", "cocoa", "torchflower_crop", "pitcher_crop"],
    "crops":  ["wheat", "carrots", "potatoes", "beetroots",
               "melon_stem", "pumpkin_stem", "nether_wart",
               "sweet_berry_bush", "cocoa", "torchflower_crop", "pitcher_crop"],
    "wheat":         ["wheat"],
    "carrots":       ["carrots"],
    "potatoes":      ["potatoes"],
    "beetroots":     ["beetroots"],
    "nether_wart":   ["nether_wart"],
    "sweet_berry_bush": ["sweet_berry_bush"],

    # ══════════════════════════════════════════════════════════════════════════
    # FLOWERS
    # ══════════════════════════════════════════════════════════════════════════
    "flower":  ["dandelion", "poppy", "blue_orchid", "allium", "azure_bluet",
                "red_tulip", "orange_tulip", "white_tulip", "pink_tulip",
                "oxeye_daisy", "cornflower", "lily_of_the_valley",
                "wither_rose", "sunflower", "lilac", "peony", "rose_bush",
                "torchflower", "pitcher_plant"],
    "flowers": ["dandelion", "poppy", "blue_orchid", "allium", "azure_bluet",
                "red_tulip", "orange_tulip", "white_tulip", "pink_tulip",
                "oxeye_daisy", "cornflower", "lily_of_the_valley",
                "wither_rose", "sunflower", "lilac", "peony", "rose_bush",
                "torchflower", "pitcher_plant"],

    # ══════════════════════════════════════════════════════════════════════════
    # DYES
    # ══════════════════════════════════════════════════════════════════════════
    "dye":    [f"{c}_dye" for c in _DYE_COLORS],
    "dyes":   [f"{c}_dye" for c in _DYE_COLORS],
    **{f"{c}_dye": [f"{c}_dye"] for c in _DYE_COLORS},
    **{f"{c} dye": [f"{c}_dye"] for c in _DYE_COLORS},

    # ══════════════════════════════════════════════════════════════════════════
    # TOOLS – all tiers
    # ══════════════════════════════════════════════════════════════════════════
    "tool":              sum([_tools(t) for t in _TIERS], []),
    "tools":             sum([_tools(t) for t in _TIERS], []),
    "pickaxe":           [f"{t}_pickaxe" for t in _TIERS],
    "pickaxes":          [f"{t}_pickaxe" for t in _TIERS],
    "axe":               [f"{t}_axe"     for t in _TIERS],
    "axes":              [f"{t}_axe"     for t in _TIERS],
    "shovel":            [f"{t}_shovel"  for t in _TIERS],
    "shovels":           [f"{t}_shovel"  for t in _TIERS],
    "hoe":               [f"{t}_hoe"     for t in _TIERS],
    "hoes":              [f"{t}_hoe"     for t in _TIERS],
    "sword":             [f"{t}_sword"   for t in _TIERS],
    "swords":            [f"{t}_sword"   for t in _TIERS],

    **{f"{t}_tools":    _tools(t)          for t in _TIERS},
    **{f"{t} tools":    _tools(t)          for t in _TIERS},
    **{f"{t}_pickaxe":  [f"{t}_pickaxe"]   for t in _TIERS},
    **{f"{t} pickaxe":  [f"{t}_pickaxe"]   for t in _TIERS},
    **{f"{t}_axe":      [f"{t}_axe"]       for t in _TIERS},
    **{f"{t} axe":      [f"{t}_axe"]       for t in _TIERS},
    **{f"{t}_shovel":   [f"{t}_shovel"]    for t in _TIERS},
    **{f"{t} shovel":   [f"{t}_shovel"]    for t in _TIERS},
    **{f"{t}_hoe":      [f"{t}_hoe"]       for t in _TIERS},
    **{f"{t} hoe":      [f"{t}_hoe"]       for t in _TIERS},
    **{f"{t}_sword":    [f"{t}_sword"]     for t in _TIERS},
    **{f"{t} sword":    [f"{t}_sword"]     for t in _TIERS},

    # gold alias
    "gold tools":  _tools("golden"),
    "gold_tools":  _tools("golden"),

    # ══════════════════════════════════════════════════════════════════════════
    # ARMOR – all tiers
    # ══════════════════════════════════════════════════════════════════════════
    "armor":             sum([_armor(t) for t in _TIERS + ["chainmail", "leather", "turtle"]], []),
    **{f"{t}_armor":    _armor(t) for t in _TIERS},
    **{f"{t} armor":    _armor(t) for t in _TIERS},
    "gold armor":        _armor("golden"),
    "gold_armor":        _armor("golden"),
    "chainmail_armor":   _armor("chainmail"),
    "chainmail armor":   _armor("chainmail"),
    "leather_armor":     _armor("leather"),
    "leather armor":     _armor("leather"),
    "turtle_helmet":     ["turtle_helmet"],
    "turtle helmet":     ["turtle_helmet"],
    "netherite_armor":   _armor("netherite"),

    # armor pieces
    "helmet":     [f"{t}_helmet" for t in _TIERS + ["chainmail", "leather", "turtle"]],
    "chestplate": [f"{t}_chestplate" for t in _TIERS + ["chainmail", "leather"]],
    "leggings":   [f"{t}_leggings" for t in _TIERS + ["chainmail", "leather"]],
    "boots":      [f"{t}_boots" for t in _TIERS + ["chainmail", "leather"]],
    **{f"{t}_helmet":     [f"{t}_helmet"]     for t in _TIERS + ["chainmail", "leather"]},
    **{f"{t}_chestplate": [f"{t}_chestplate"] for t in _TIERS + ["chainmail", "leather"]},
    **{f"{t}_leggings":   [f"{t}_leggings"]   for t in _TIERS + ["chainmail", "leather"]},
    **{f"{t}_boots":      [f"{t}_boots"]       for t in _TIERS + ["chainmail", "leather"]},

    # armor layers (for texture pack renders)
    "armor layers": [
        "humanoid/diamond", "humanoid/iron", "humanoid/gold", "humanoid/netherite",
        "humanoid/chainmail", "humanoid/leather", "humanoid/copper", "humanoid/turtle_scute",
        "humanoid_leggings/diamond", "humanoid_leggings/iron", "humanoid_leggings/gold",
        "humanoid_leggings/netherite", "humanoid_leggings/chainmail",
        "humanoid_leggings/leather", "humanoid_leggings/copper",
    ],

    # ══════════════════════════════════════════════════════════════════════════
    # SPEARS (custom / modded)
    # ══════════════════════════════════════════════════════════════════════════
    "spear":         [f"{t}_spear" for t in _TIERS],
    "spear_in_hand": [f"{t}_spear_in_hand" for t in _TIERS],
    "spear in hand": [f"{t}_spear_in_hand" for t in _TIERS],
    **{f"{t}_spear":        [f"{t}_spear"]        for t in _TIERS},
    **{f"{t} spear":        [f"{t}_spear"]        for t in _TIERS},
    **{f"{t}_spear_in_hand":[f"{t}_spear_in_hand"] for t in _TIERS},
    **{f"{t} spear in hand":[f"{t}_spear_in_hand"] for t in _TIERS},

    # ══════════════════════════════════════════════════════════════════════════
    # RANGED / PROJECTILES
    # ══════════════════════════════════════════════════════════════════════════
    "bow":        ["bow"],
    "crossbow":   ["crossbow"],
    "bows":       ["bow", "crossbow"],
    "crossbows":  ["crossbow"],
    "arrow":      ["arrow", "tipped_arrow", "spectral_arrow"],
    "arrows":     ["arrow", "tipped_arrow", "spectral_arrow"],
    "tipped_arrow":   ["tipped_arrow"],
    "spectral_arrow": ["spectral_arrow"],
    "trident":    ["trident"],
    "mace":       ["mace"],

    # ══════════════════════════════════════════════════════════════════════════
    # FISH / FOOD
    # ══════════════════════════════════════════════════════════════════════════
    "fish":   [f"{f}" for f in _FISH_TYPES] + [f"cooked_{f}" for f in _FISH_TYPES[:2]],
    "fishes": [f"{f}" for f in _FISH_TYPES] + [f"cooked_{f}" for f in _FISH_TYPES[:2]],
    "cooked_fish": [f"cooked_{f}" for f in _FISH_TYPES[:2]],
    **{f: [f] for f in _FISH_TYPES},

    "food":  ["bread", "apple", "golden_apple", "enchanted_golden_apple",
              "cooked_beef", "cooked_porkchop", "cooked_chicken", "cooked_mutton",
              "cooked_rabbit", "cooked_cod", "cooked_salmon",
              "baked_potato", "beetroot_soup", "mushroom_stew", "rabbit_stew",
              "suspicious_stew", "pumpkin_pie", "cake", "cookie", "honey_bottle",
              "sweet_berries", "glow_berries", "dried_kelp", "melon_slice",
              "carrot", "potato", "poisonous_potato", "beetroot",
              "chorus_fruit", "torchflower_seeds", "pitcher_pod"],
    "foods": ["bread", "apple", "golden_apple", "enchanted_golden_apple",
              "cooked_beef", "cooked_porkchop", "cooked_chicken", "cooked_mutton",
              "cooked_rabbit", "cooked_cod", "cooked_salmon",
              "baked_potato", "beetroot_soup", "mushroom_stew", "rabbit_stew",
              "suspicious_stew", "pumpkin_pie", "cake", "cookie", "honey_bottle"],

    # ══════════════════════════════════════════════════════════════════════════
    # BOATS / CHEST BOATS
    # ══════════════════════════════════════════════════════════════════════════
    "boat":        [f"{w}_boat" for w in _WOOD_TYPES],
    "boats":       [f"{w}_boat" for w in _WOOD_TYPES],
    "chest_boat":  [f"{w}_chest_boat" for w in _WOOD_TYPES],
    "chest_boats": [f"{w}_chest_boat" for w in _WOOD_TYPES],
    "chest boat":  [f"{w}_chest_boat" for w in _WOOD_TYPES],
    **{f"{w}_boat":       [f"{w}_boat"]       for w in _WOOD_TYPES},
    **{f"{w} boat":       [f"{w}_boat"]       for w in _WOOD_TYPES},
    **{f"{w}_chest_boat": [f"{w}_chest_boat"] for w in _WOOD_TYPES},
    **{f"{w} chest boat": [f"{w}_chest_boat"] for w in _WOOD_TYPES},

    # ══════════════════════════════════════════════════════════════════════════
    # MUSIC DISCS
    # ══════════════════════════════════════════════════════════════════════════
    "music_disc":  [f"music_disc_{d}" for d in _MUSIC_DISCS],
    "music_discs": [f"music_disc_{d}" for d in _MUSIC_DISCS],
    "music disc":  [f"music_disc_{d}" for d in _MUSIC_DISCS],
    "music discs": [f"music_disc_{d}" for d in _MUSIC_DISCS],
    "disc":        [f"music_disc_{d}" for d in _MUSIC_DISCS],
    **{f"music_disc_{d}": [f"music_disc_{d}"] for d in _MUSIC_DISCS},
    **{f"music disc {d}": [f"music_disc_{d}"] for d in _MUSIC_DISCS},

    # ══════════════════════════════════════════════════════════════════════════
    # TRIM MATERIALS + TEMPLATES
    # ══════════════════════════════════════════════════════════════════════════
    "trim_material":   [f"{m}_ingot" if m not in ("quartz","amethyst","redstone","lapis","resin","diamond","emerald") else m
                        for m in _TRIM_MATS],
    "trim_materials":  [f"{m}_ingot" if m not in ("quartz","amethyst","redstone","lapis","resin","diamond","emerald") else m
                        for m in _TRIM_MATS],
    "trim material":   [f"{m}_ingot" if m not in ("quartz","amethyst","redstone","lapis","resin","diamond","emerald") else m
                        for m in _TRIM_MATS],
    "trim_template":   [f"{t}_armor_trim_smithing_template" for t in _TRIM_TMPL],
    "trim_templates":  [f"{t}_armor_trim_smithing_template" for t in _TRIM_TMPL],
    "trim template":   [f"{t}_armor_trim_smithing_template" for t in _TRIM_TMPL],
    "smithing_template": [f"{t}_armor_trim_smithing_template" for t in _TRIM_TMPL] +
                         ["netherite_upgrade_smithing_template"],
    **{f"{t}_trim":         [f"{t}_armor_trim_smithing_template"] for t in _TRIM_TMPL},
    **{f"{t} trim":         [f"{t}_armor_trim_smithing_template"] for t in _TRIM_TMPL},
    **{f"{t}_armor_trim_smithing_template": [f"{t}_armor_trim_smithing_template"] for t in _TRIM_TMPL},

    # ══════════════════════════════════════════════════════════════════════════
    # INGOTS / GEMS / NUGGETS / RAW MATERIALS
    # ══════════════════════════════════════════════════════════════════════════
    "ingot":   ["iron_ingot", "gold_ingot", "copper_ingot", "netherite_ingot",
                "brick", "nether_brick"],
    "ingots":  ["iron_ingot", "gold_ingot", "copper_ingot", "netherite_ingot"],
    "iron_ingot":      ["iron_ingot"],
    "gold_ingot":      ["gold_ingot"],
    "copper_ingot":    ["copper_ingot"],
    "netherite_ingot": ["netherite_ingot"],
    "iron ingot":      ["iron_ingot"],
    "gold ingot":      ["gold_ingot"],
    "copper ingot":    ["copper_ingot"],
    "netherite ingot": ["netherite_ingot"],

    "nugget":  ["iron_nugget", "gold_nugget"],
    "nuggets": ["iron_nugget", "gold_nugget"],
    "iron_nugget":  ["iron_nugget"],
    "gold_nugget":  ["gold_nugget"],
    "iron nugget":  ["iron_nugget"],
    "gold nugget":  ["gold_nugget"],

    "gem":    ["diamond", "emerald", "amethyst_shard", "quartz", "lapis_lazuli",
               "prismarine_crystals", "nether_quartz"],
    "gems":   ["diamond", "emerald", "amethyst_shard", "quartz", "lapis_lazuli",
               "prismarine_crystals"],
    "diamond":          ["diamond"],
    "emerald":          ["emerald"],
    "amethyst_shard":   ["amethyst_shard"],
    "quartz":           ["quartz"],
    "lapis_lazuli":     ["lapis_lazuli"],

    "raw_material":  ["raw_iron", "raw_gold", "raw_copper"],
    "raw_materials": ["raw_iron", "raw_gold", "raw_copper"],
    "raw material":  ["raw_iron", "raw_gold", "raw_copper"],
    "raw materials": ["raw_iron", "raw_gold", "raw_copper"],
    "raw_iron":     ["raw_iron"],
    "raw_gold":     ["raw_gold"],
    "raw_copper":   ["raw_copper"],
    "raw iron":     ["raw_iron"],
    "raw gold":     ["raw_gold"],
    "raw copper":   ["raw_copper"],

    # dust/powder
    "dust":      ["redstone", "glowstone_dust", "gunpowder", "sugar"],
    "dusts":     ["redstone", "glowstone_dust", "gunpowder", "sugar"],
    "redstone":  ["redstone"],
    "glowstone_dust": ["glowstone_dust"],
    "gunpowder": ["gunpowder"],

    # storage blocks
    "storage_block":   ["iron_block", "gold_block", "copper_block", "diamond_block",
                        "emerald_block", "netherite_block", "lapis_block", "redstone_block",
                        "coal_block", "amethyst_block", "raw_iron_block", "raw_gold_block",
                        "raw_copper_block"],
    "storage_blocks":  ["iron_block", "gold_block", "copper_block", "diamond_block",
                        "emerald_block", "netherite_block", "lapis_block", "redstone_block",
                        "coal_block", "amethyst_block"],
    "storage block":   ["iron_block", "gold_block", "copper_block", "diamond_block",
                        "emerald_block", "netherite_block", "lapis_block", "redstone_block",
                        "coal_block", "amethyst_block"],

    # ══════════════════════════════════════════════════════════════════════════
    # COALS
    # ══════════════════════════════════════════════════════════════════════════
    "coal":   ["coal", "charcoal"],
    "coals":  ["coal", "charcoal"],
    "charcoal": ["charcoal"],

    # cluster harvestables
    "cluster_max_harvestables": ["amethyst_cluster", "large_amethyst_bud",
                                  "medium_amethyst_bud", "small_amethyst_bud"],
    "amethyst_cluster":  ["amethyst_cluster"],
    "amethyst cluster":  ["amethyst_cluster"],

    # ══════════════════════════════════════════════════════════════════════════
    # SEEDS
    # ══════════════════════════════════════════════════════════════════════════
    "seed":   ["wheat_seeds", "melon_seeds", "pumpkin_seeds", "beetroot_seeds",
               "torchflower_seeds", "pitcher_pod"],
    "seeds":  ["wheat_seeds", "melon_seeds", "pumpkin_seeds", "beetroot_seeds",
               "torchflower_seeds", "pitcher_pod"],
    "wheat_seeds":    ["wheat_seeds"],
    "melon_seeds":    ["melon_seeds"],
    "pumpkin_seeds":  ["pumpkin_seeds"],
    "beetroot_seeds": ["beetroot_seeds"],

    # ══════════════════════════════════════════════════════════════════════════
    # BOOKS / BOOKSHELVES
    # ══════════════════════════════════════════════════════════════════════════
    "book":          ["book", "written_book", "enchanted_book", "knowledge_book"],
    "books":         ["book", "written_book", "enchanted_book", "knowledge_book"],
    "bookshelf_books": ["book", "written_book", "enchanted_book", "knowledge_book"],
    "bookshelf book":  ["book", "written_book", "enchanted_book", "knowledge_book"],
    "enchanted_book":  ["enchanted_book"],
    "written_book":    ["written_book"],

    # ══════════════════════════════════════════════════════════════════════════
    # BEACON PAYMENT
    # ══════════════════════════════════════════════════════════════════════════
    "beacon_payment_items": ["iron_ingot", "gold_ingot", "diamond", "emerald", "netherite_ingot"],
    "beacon payment":       ["iron_ingot", "gold_ingot", "diamond", "emerald", "netherite_ingot"],

    # ══════════════════════════════════════════════════════════════════════════
    # POTIONS / MISC ITEMS
    # ══════════════════════════════════════════════════════════════════════════
    "potion":  ["potion", "splash_potion", "lingering_potion"],
    "potions": ["potion", "splash_potion", "lingering_potion"],
    "splash_potion":   ["splash_potion"],
    "lingering_potion":["lingering_potion"],
    "ender_pearl":   ["ender_pearl"],
    "ender pearl":   ["ender_pearl"],
    "ender_eye":     ["ender_eye"],
    "eye of ender":  ["ender_eye"],
    "blaze_rod":     ["blaze_rod"],
    "blaze rod":     ["blaze_rod"],
    "blaze_powder":  ["blaze_powder"],
    "nether_star":   ["nether_star"],
    "totem_of_undying": ["totem_of_undying"],
    "totem":         ["totem_of_undying"],
    "elytra":        ["elytra"],
    "shield":        ["shield"],
    "map":           ["map", "filled_map"],
    "compass":       ["compass", "recovery_compass"],
    "clock":         ["clock"],
    "spyglass":      ["spyglass"],
    "lead":          ["lead"],
    "name_tag":      ["name_tag"],
    "name tag":      ["name_tag"],
    "saddle":        ["saddle"],
    "bundle":        ["bundle"],

    # ══════════════════════════════════════════════════════════════════════════
    # WATER / LAVA / BUCKETS
    # ══════════════════════════════════════════════════════════════════════════
    "water":   ["water_bucket", "water"],
    "lava":    ["lava_bucket", "lava"],
    "bucket":  ["bucket", "water_bucket", "lava_bucket", "milk_bucket",
                "powder_snow_bucket", "axolotl_bucket", "cod_bucket",
                "salmon_bucket", "tropical_fish_bucket", "pufferfish_bucket"],
    "buckets": ["bucket", "water_bucket", "lava_bucket", "milk_bucket",
                "powder_snow_bucket"],

    # ══════════════════════════════════════════════════════════════════════════
    # GRASS / DIRT
    # ══════════════════════════════════════════════════════════════════════════
    "grass":    ["grass_block", "grass"],
    "dirt":     ["dirt", "coarse_dirt", "rooted_dirt", "podzol", "mycelium",
                 "mud", "muddy_mangrove_roots"],

    # ══════════════════════════════════════════════════════════════════════════
    # FORGE / NEOFORGE TAGS  (map tag → vanilla equivalents)
    # ══════════════════════════════════════════════════════════════════════════
    "forge:ores":          _ores() + _ores(deepslate=True),
    "forge:ingots":        ["iron_ingot", "gold_ingot", "copper_ingot", "netherite_ingot"],
    "forge:nuggets":       ["iron_nugget", "gold_nugget"],
    "forge:gems":          ["diamond", "emerald", "amethyst_shard", "quartz", "lapis_lazuli"],
    "forge:dusts":         ["redstone", "glowstone_dust", "gunpowder"],
    "forge:plates":        [],   # mod-added; no vanilla items
    "forge:rods":          ["blaze_rod", "stick"],
    "forge:gears":         [],   # mod-added
    "forge:storage_blocks":["iron_block", "gold_block", "copper_block", "diamond_block",
                             "emerald_block", "netherite_block", "lapis_block",
                             "redstone_block", "coal_block"],
    "forge:raw_materials": ["raw_iron", "raw_gold", "raw_copper"],
    "forge:seeds":         ["wheat_seeds", "melon_seeds", "pumpkin_seeds", "beetroot_seeds"],
    "forge:foods":         ["bread", "apple", "golden_apple", "cooked_beef",
                            "cooked_porkchop", "cooked_chicken", "baked_potato"],
    "forge:tools":         sum([_tools(t) for t in _TIERS], []),
    "forge:armor":         sum([_armor(t) for t in _TIERS + ["chainmail", "leather"]], []),

    # ══════════════════════════════════════════════════════════════════════════
    # IN-HAND VARIANTS (generic)
    # ══════════════════════════════════════════════════════════════════════════
    "in hand":    ["_in_hand"],
    "_in_hand":   [],
    "in_hand":    ["_in_hand"],
}

# ─────────────────────────────────────────────────────────────────────────────
# SEARCH FOLDERS  (order matters – item first, then block, then entity, then armors)
# ─────────────────────────────────────────────────────────────────────────────

SEARCH_FOLDERS = [
    ("textures/item",   ".png"),
    ("textures/block",  ".png"),
    ("textures/entity", ".png"),
    ("armors/humanoid",          ".png"),
    ("armors/humanoid_leggings", ".png"),
    ("armors",                   ".png"),
]

# ─────────────────────────────────────────────────────────────────────────────
# resolve_names  – smarter multi-strategy lookup
# ─────────────────────────────────────────────────────────────────────────────

def resolve_names(raw: str) -> list[str]:
    """
    Convert a user query into a list of asset filenames to search for.

    Strategy (in order):
    1. Direct alias lookup (raw and underscored form)
    2. Forge tag lookup  (forge:xxx)
    3. Suffix-wildcard  (ends with known suffix like '_ore', '_sword', …)
    4. Partial key match in alias dict
    5. Fallback: treat as literal filename
    """
    key = raw.strip().lower()
    underscore_key = key.replace(" ", "_")
    space_key = key.replace("_", " ")

    # 1 – direct
    for k in (key, underscore_key, space_key):
        if k in ITEM_ALIASES:
            return list(dict.fromkeys(ITEM_ALIASES[k]))

    # 2 – forge tag (user may omit "forge:" prefix)
    forge_key = f"forge:{key}"
    if forge_key in ITEM_ALIASES:
        return list(dict.fromkeys(ITEM_ALIASES[forge_key]))

    # 3 – known suffix expansion
    _KNOWN_SUFFIXES = {
        "_ore":           lambda m: [f"{m}_ore", f"deepslate_{m}_ore"],
        "_sword":         lambda m: [f"{m}_sword"],
        "_pickaxe":       lambda m: [f"{m}_pickaxe"],
        "_axe":           lambda m: [f"{m}_axe"],
        "_shovel":        lambda m: [f"{m}_shovel"],
        "_hoe":           lambda m: [f"{m}_hoe"],
        "_helmet":        lambda m: [f"{m}_helmet"],
        "_chestplate":    lambda m: [f"{m}_chestplate"],
        "_leggings":      lambda m: [f"{m}_leggings"],
        "_boots":         lambda m: [f"{m}_boots"],
        "_log":           lambda m: [f"{m}_log", f"stripped_{m}_log"],
        "_planks":        lambda m: [f"{m}_planks"],
        "_leaves":        lambda m: [f"{m}_leaves"],
        "_sapling":       lambda m: [f"{m}_sapling"],
        "_wool":          lambda m: [f"{m}_wool"],
        "_concrete":      lambda m: [f"{m}_concrete"],
        "_concrete_powder": lambda m: [f"{m}_concrete_powder"],
        "_terracotta":    lambda m: [f"{m}_terracotta", f"{m}_glazed_terracotta"],
        "_stained_glass": lambda m: [f"{m}_stained_glass", f"{m}_stained_glass_pane"],
        "_bed":           lambda m: [f"{m}_bed"],
        "_banner":        lambda m: [f"{m}_banner"],
        "_candle":        lambda m: [f"{m}_candle"],
        "_dye":           lambda m: [f"{m}_dye"],
        "_boat":          lambda m: [f"{m}_boat", f"{m}_chest_boat"],
        "_spear":         lambda m: [f"{m}_spear", f"{m}_spear_in_hand"],
        "_stairs":        lambda m: [f"{m}_stairs"],
        "_slab":          lambda m: [f"{m}_slab"],
        "_wall":          lambda m: [f"{m}_wall"],
        "_fence":         lambda m: [f"{m}_fence", f"{m}_fence_gate"],
        "_door":          lambda m: [f"{m}_door"],
        "_trapdoor":      lambda m: [f"{m}_trapdoor"],
        "_button":        lambda m: [f"{m}_button"],
        "_pressure_plate":lambda m: [f"{m}_pressure_plate"],
    }
    for suffix, builder in _KNOWN_SUFFIXES.items():
        if underscore_key.endswith(suffix):
            material = underscore_key[: -len(suffix)]
            return list(dict.fromkeys(builder(material)))

    # 4 – partial key match
    partial: list[str] = []
    for alias_key, alias_vals in ITEM_ALIASES.items():
        if key in alias_key or alias_key in key \
                or underscore_key in alias_key or alias_key in underscore_key:
            partial.extend(alias_vals)
    if partial:
        return list(dict.fromkeys(partial))

    # 5 – literal fallback (try both forms)
    return list(dict.fromkeys([underscore_key, space_key.replace(" ", "_")]))

# ─────────────────────────────────────────────────────────────────────────────
# BLOCK MODEL RESOLVER — پیدا کردن تکسچرِ واقعیِ هر وجه از روی مدل بلاک
#
# خیلی از بلاک‌ها (مثل Command Block, Furnace, Crafting Table) اصلاً فایل
# «block_name.png» ندارن؛ تکسچرشون توی چند فایل جدا (مثل command_block_front.png،
# command_block_side.png، command_block_back.png) هست و این‌که کدوم فایل روی
# کدوم وجه (up/down/north/south/east/west) میره، توی JSON مدل بلاک
# (assets/minecraft/models/block/<name>.json) مشخص شده. این تابع همون زنجیره‌ی
# parent → parent → ... رو دنبال می‌کنه (دقیقاً کاری که خود ماینکرافت انجام
# می‌ده) تا وجه‌ها رو resolve کنه. برای بلاک‌های ساده (مثل dirt که پرنتش
# block/cube_all هست) هر ۶ وجه به یک فایل ختم می‌شن؛ برای بلاک‌های جهت‌دار
# (مثل command_block که پرنتش block/cube_directional/orientable هست) هر وجه
# ممکنه فایل جدا داشته باشه.
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_model_json(session: aiohttp.ClientSession, model_path: str):
    """model_path مثل 'block/dirt' — کش می‌شه که برای درخواست‌های بعدی دوباره فچ نشه."""
    if model_path in _block_model_cache:
        return _block_model_cache[model_path]
    url = f"{MC_ASSETS_BASE}/models/{model_path}.json"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status != 200:
                _block_model_cache[model_path] = None
                return None
            data = await resp.json(content_type=None)
            _block_model_cache[model_path] = data
            return data
    except Exception:
        _block_model_cache[model_path] = None
        return None


async def resolve_block_cube_faces(session: aiohttp.ClientSession, block_name: str):
    """
    اگر block_name یک بلاک مکعبیِ ساده (۶ وجه، from=[0,0,0] to=[16,16,16]) باشه،
    دیکشنری {"up":file.png,"down":...,"north":...,"south":...,"east":...,"west":...}
    برمی‌گردونه. برای بلاک‌های شکل‌خاص (پله، اسلب، نرده و ...) None برمی‌گردونه،
    چون هندسه‌شون مکعب ساده نیست و ساخت آیتم سه‌بعدی مکعبی براشون معنی نداره.
    """
    chain = []
    current = f"block/{block_name}"
    seen = set()
    for _ in range(12):
        if current in seen:
            break
        seen.add(current)
        data = await _fetch_model_json(session, current)
        if not data:
            break
        chain.append(data)
        parent = data.get("parent")
        if not parent:
            break
        current = parent.replace("minecraft:", "")

    if not chain:
        return None

    # اولین مدل (از برگ به‌سمت ریشه) که "elements" داره، هندسه‌ی واقعی بلاکه
    elements = None
    for m in chain:
        if "elements" in m:
            elements = m["elements"]
            break

    if not elements:
        return None  # مدل بلاک هندسه‌ی صریح نداره

    # بعضی بلاک‌ها (مثل grass_block) چند element دارن (مثلاً یک لایه‌ی overlay
    # نیمه‌شفاف رنگی روی مکعب اصلی) — element اصلیِ تمام‌مکعب (۶ وجه کامل، از
    # ۰,۰,۰ تا ۱۶,۱۶,۱۶) رو انتخاب می‌کنیم و overlayها رو نادیده می‌گیریم.
    needed = ("up", "down", "north", "south", "east", "west")
    elem = None
    for e in elements:
        if e.get("from") == [0, 0, 0] and e.get("to") == [16, 16, 16] and all(f in e.get("faces", {}) for f in needed):
            elem = e
            break

    if not elem:
        return None  # بلاک تمام‌مکعب نیست (پله/اسلب/نرده/...) → مکعب ساده معنی نداره

    faces_def = elem.get("faces", {})

    # ادغام «textures» از ریشه به‌سمت برگ (برگ مقدارهای دقیق‌تر رو override می‌کنه)
    merged = {}
    for m in reversed(chain):
        merged.update(m.get("textures", {}))

    def resolve_var(val, depth=0):
        if depth > 10 or not isinstance(val, str):
            return None
        if val.startswith("#"):
            return resolve_var(merged.get(val[1:]), depth + 1)
        return val.replace("minecraft:", "")

    # بعضی وجه‌ها (مثلاً "up" برای grass_block) توی مدل JSON فیلد "tintindex"
    # دارن — یعنی ماینکرافت رنگ اون وجه رو بر اساس رنگ بایوم (چمن/برگ/آب و...)
    # ضرب می‌کنه، و تکسچر خام‌اش (مثل grass_block_top.png) عمداً خاکستریِ
    # بی‌رنگه. قبلاً این تینت اصلاً اعمال نمی‌شد و همون خاکستری خام رندر
    # می‌شد (باگی که باعث می‌شد روی grass_block بجای سبز، خاکستری دیده بشه).
    # این‌جا وجه‌هایی که tintindex دارن رو جدا نگه می‌داریم تا build_block_cube_obj
    # بتونه موقع ساخت متریال، یک تینت پیش‌فرض (سبز) روش اعمال کنه.
    result = {}
    tint_faces = set()
    for face in needed:
        face_obj = faces_def[face]
        resolved = resolve_var(face_obj.get("texture"))
        if not resolved:
            return None
        result[face] = resolved.split("/")[-1] + ".png"  # فقط اسم فایل، پوشه‌ش همیشه textures/block هست
        if "tintindex" in face_obj:
            tint_faces.add(face)

    if tint_faces:
        # کلید مخصوص که اسم وجه واقعی نیست، پس تو حلقه‌های face_defs.items() نادیده
        # گرفته می‌شه؛ فقط توی build_block_cube_obj و فیلترهای FACE_KEYS خونده می‌شه.
        result["_tint"] = ",".join(sorted(tint_faces))

    return result


async def download_block_textures(session: aiohttp.ClientSession, texture_files: set, dest_dir: str) -> dict:
    """تکسچرهای لازم رو از ریپو دانلود می‌کنه. خروجی: {filename: local_path} فقط برای اونایی که موفق بودن."""
    os.makedirs(dest_dir, exist_ok=True)
    local_paths = {}

    async def _dl(fname):
        url = f"{MC_ASSETS_BASE}/textures/block/{fname}"
        local_path = os.path.join(dest_dir, fname)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    with open(local_path, "wb") as f:
                        f.write(data)
                    local_paths[fname] = local_path
        except Exception:
            pass

    await asyncio.gather(*[_dl(f) for f in texture_files])
    return local_paths


# اسم ۶ وجه واقعی مکعب — برای فیلتر کردن کلیدهای کمکی مثل "_tint" که واقعی نیستن
FACE_KEYS = ("up", "down", "north", "south", "east", "west")

# رنگ پیش‌فرض تینت چمن/بایوم (نزدیک به رنگ پیش‌فرض بایوم Plains در ماینکرافت).
# دقیق‌ترین رنگ واقعی بسته به بایومه، ولی چون این‌جا بایوم واقعی نداریم همین
# یک رنگ ثابت و معقول رو برای هر وجهی که tintindex داره (فعلاً فقط بالای
# grass_block) استفاده می‌کنیم.
GRASS_TINT_RGB = (124, 189, 107)


def build_block_cube_obj(faces: dict, texture_local_paths: dict, out_dir: str, base_name: str):
    """
    یک OBJ مکعبیِ استاندارد (واحد ۱×۱×۱) می‌سازه که هر کدوم از ۶ وجهش یک متریال
    جدا داره (usemtl جدا برای هر وجه) و یک MTL که هر متریال رو به فایل تکسچر
    درستش (map_Kd) وصل می‌کنه. برای بلاک‌هایی مثل dirt که هر ۶ متریال به یک
    فایل ختم می‌شن هم همین ساختار کار می‌کنه (فقط نتیجه یک تکسچر یکسان رو نشون
    می‌ده)، پس نیازی به مسیر جدا برای «بلاک ساده» نیست.
    """
    os.makedirs(out_dir, exist_ok=True)
    obj_path = os.path.join(out_dir, f"{base_name}.obj")
    mtl_path = os.path.join(out_dir, f"{base_name}.mtl")

    # هشت رأس مکعب واحد، مرکز روی مبدأ
    verts = [
        (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),  # back  (0-3)
        (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),      # front (4-7)
    ]
    # هر وجه: نام متریال، ایندکس ۴ رأس (پادساعت‌گرد از بیرون)، UV استاندارد
    uv_quad = [(0, 0), (1, 0), (1, 1), (0, 1)]
    face_defs = {
        "north": [1, 0, 3, 2],  # -Z را می‌بینیم از -Z به سمت +Z... (north = -Z در نگاشت ماینکرافت)
        "south": [4, 5, 6, 7],
        "west":  [0, 4, 7, 3],
        "east":  [5, 1, 2, 6],
        "up":    [3, 7, 6, 2],
        "down":  [4, 0, 1, 5],
    }

    lines_obj = ["# generated by bot.py – block cube (multi-material)"]
    lines_obj.append(f"mtllib {base_name}.mtl")
    for x, y, z in verts:
        lines_obj.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for u, v in uv_quad:
        lines_obj.append(f"vt {u:.6f} {v:.6f}")

    # وجه‌هایی که resolve_block_cube_faces علامت‌شون زده (مثلاً "up" برای
    # grass_block) — باید متریال جدا (تینت‌دار) بگیرن، نه اینکه با وجه‌های
    # دیگه‌ای که از همون فایل تکسچر استفاده می‌کنن یکی بشن.
    tint_faces = set(faces.get("_tint", "").split(",")) if faces.get("_tint") else set()

    mat_texfile = {}  # matName -> (texture filename, is_tinted)
    seen_mats = {}     # (texfile, is_tinted) -> matName (reuse material اگر دقیقاً همون ترکیب تکرار شد)

    for face_name, vidx in face_defs.items():
        tex_file = faces.get(face_name)
        if not tex_file or tex_file not in texture_local_paths:
            continue  # اگر تکسچر این وجه دانلود نشد، وجه رد میشه (به‌جای کرش کردن)
        is_tinted = face_name in tint_faces
        dedup_key = (tex_file, is_tinted)
        mat_name = seen_mats.get(dedup_key)
        if not mat_name:
            suffix = "_tint" if is_tinted else ""
            mat_name = f"mat_{len(seen_mats) + 1}_{os.path.splitext(tex_file)[0]}{suffix}"
            seen_mats[dedup_key] = mat_name
            mat_texfile[mat_name] = (tex_file, is_tinted)
        lines_obj.append(f"usemtl {mat_name}")
        i1, i2, i3, i4 = [i + 1 for i in vidx]
        lines_obj.append(f"f {i1}/1 {i2}/2 {i3}/3")
        lines_obj.append(f"f {i1}/1 {i3}/3 {i4}/4")

    with open(obj_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_obj) + "\n")

    lines_mtl = []
    for mat_name, (tex_file, is_tinted) in mat_texfile.items():
        lines_mtl.append(f"newmtl {mat_name}")
        lines_mtl.append("Ka 1.000 1.000 1.000")
        lines_mtl.append("Kd 1.000 1.000 1.000")
        lines_mtl.append(f"map_Kd {tex_file}")
        if is_tinted:
            # خط غیراستاندارد (مای‌تی‌ال معمولی همچین چیزی نداره) که فقط
            # block_render.mjs می‌خونتش تا رنگ این وجه رو با تینت ضرب کنه.
            tr, tg, tb = GRASS_TINT_RGB
            lines_mtl.append(f"# tint {tr} {tg} {tb}")
        lines_mtl.append("")
    with open(mtl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_mtl))

    # کپی تکسچرها کنار OBJ (block_render.mjs انتظار داره تکسچرها هم‌پوشه‌ی OBJ باشن)
    for tex_file, local_path in texture_local_paths.items():
        dest = os.path.join(out_dir, tex_file)
        if os.path.abspath(dest) != os.path.abspath(local_path):
            with open(local_path, "rb") as src, open(dest, "wb") as dst:
                dst.write(src.read())

    return obj_path, mtl_path


async def search_mc_assets(names: list[str]) -> tuple[list[dict], dict]:
    """خروجی: (لیست فایل‌های پیداشده, دیکشنری block_models برای دکمه‌ی ساخت سه‌بعدی)"""
    found = []
    seen = set()

    armors_dir = BASE_DIR / "armors"
    print(f"[DEBUG] BASE_DIR: {BASE_DIR}")
    print(f"[DEBUG] armors_dir: {armors_dir} | وجود: {armors_dir.exists()}")

    for raw_name in names:
        # پشتیبانی از فرمت humanoid/diamond و humanoid_leggings/...
        if "/" in raw_name:
            subfolder, name_part = raw_name.split("/", 1)
            name_clean = name_part.strip().lower().replace(".png", "")
            if subfolder == "humanoid_leggings":
                label_prefix = "leggings"
            else:
                label_prefix = "main"
            subfolders_to_check = [(subfolder, label_prefix)]
        else:
            name_clean = raw_name.strip().lower().replace(".png", "")
            subfolders_to_check = [
                ("humanoid_leggings", "leggings"),
                ("humanoid", "main"),
                ("", "main")
            ]

        for sub, label_prefix in subfolders_to_check:
            local_file = armors_dir / sub / f"{name_clean}.png"
            
            if local_file.exists():
                local_url = str(local_file)
                if local_url not in seen:
                    seen.add(local_url)
                    found.append({
                        "name": f"{name_clean}.png",
                        "url": local_url,
                        "ext": ".png",
                        "label": f"🖼 [{label_prefix}] {name_clean}.png"
                    })
                    print(f"✅ پیدا شد: [{label_prefix}] {name_clean}.png")
                    break  # فقط یک بار اضافه شود

    # جستجو در گیت‌هاب اگر محلی پیدا نشد
    if not found:
        print("[DEBUG] هیچ فایل محلی پیدا نشد → جستجو در گیت‌هاب")
        async with aiohttp.ClientSession() as session:
            seen_urls = set()
            tasks = []
            for name in names:
                name_clean = name.strip().lower().replace(".png", "").replace(".json", "")
                for folder, ext in SEARCH_FOLDERS:
                    if "armors" in folder:
                        continue
                    url = f"{MC_ASSETS_BASE}/{folder}/{name_clean}{ext}"
                    tasks.append((name_clean, folder, ext, url))

            async def check_url(name_clean, folder, ext, url):
                if url in seen_urls:
                    return None
                try:
                    async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            seen_urls.add(url)
                            folder_type = "item" if "item" in folder else "block"
                            return {
                                "name": f"{name_clean}{ext}",
                                "url": url,
                                "ext": ext,
                                "label": f"🖼 [{folder_type}] {name_clean}{ext}"
                            }
                except:
                    pass
                return None

            results = await asyncio.gather(*[check_url(*t) for t in tasks])
            found.extend([r for r in results if r is not None])

    # ── جستجوی بر پایه‌ی مدل بلاک (برای بلاک‌های چندتکسچره مثل Command Block) ──
    # خیلی از بلاک‌ها فایل «block_name.png» ندارن (تکسچرشون چندتاست: front/side/back/…)
    # پس جدا از HEAD-check بالا، مدل بلاک رو resolve می‌کنیم تا اسم واقعی فایل‌ها
    # پیدا بشه و هم به لیست جستجو اضافه بشن، هم برای دکمه‌ی «ساخت آیتم سه‌بعدی» ذخیره بشن.
    block_models: dict[str, dict] = {}  # block_name -> {"up":file, "down":file, ...}
    seen_urls_model = {f["url"] for f in found}
    async with aiohttp.ClientSession() as session:
        for name in names:
            name_clean = name.strip().lower().replace(".png", "").replace(".json", "").replace(" ", "_")
            if "/" in name_clean:
                continue  # فرمت armor (humanoid/...) اینجا معنی نداره
            faces = await resolve_block_cube_faces(session, name_clean)
            if not faces:
                continue
            block_models[name_clean] = faces
            for face, tex_file in faces.items():
                url = f"{MC_ASSETS_BASE}/textures/block/{tex_file}"
                if url in seen_urls_model:
                    continue
                seen_urls_model.add(url)
                found.append({
                    "name": tex_file,
                    "url": url,
                    "ext": ".png",
                    "label": f"🖼 [block] {tex_file}"
                })

    return found, block_models
    
def build_asset_keyboard(files: list, selected: list, block_models: dict | None = None) -> InlineKeyboardMarkup:
    """ساخت کیبورد با دکمه‌های سبز برای موارد انتخاب‌شده + دکمه‌ی «ساخت آیتم سه‌بعدی» برای بلاک‌های resolve‌شده"""
    selected_urls = {f["url"] for f in selected}
    kb_rows = []
    for i, file in enumerate(files):
        is_selected = file["url"] in selected_urls
        if is_selected:
            btn_text = f"✅ {file['label']}"
        else:
            btn_text = file["label"]
        kb_rows.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"asset_select:{i}"
            )
        ])

    kb_rows.append([
        InlineKeyboardButton(text="📤 ارسال انتخاب‌ها (حداکثر ۵)", callback_data="asset_send"),
        InlineKeyboardButton(text="✅ ارسال همه", callback_data="asset_send_all"),
    ])

    # دکمه‌ی «ساخت آیتم سه‌بعدی» — یک دکمه به‌ازای هر بلاکِ قابل‌ساخت (معمولاً یکی)
    for block_name in (block_models or {}):
        kb_rows.append([
            InlineKeyboardButton(
                text=f"🧊 ساخت آیتم سه‌بعدی: {block_name}",
                callback_data=f"asset_build3d:{block_name}"
            )
        ])

    kb_rows.append([
        InlineKeyboardButton(text="❌ لغو", callback_data="asset_cancel")
    ])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


@dp.message(F.text, lambda m: user_modes.get(m.from_user.id) == "minecraft_assets")
async def handle_minecraft_asset_name(message: types.Message):
    if get_access_block_message(message.from_user.id):
        return

    raw_input = message.text.strip()
    user_id = message.from_user.id

    await message.answer(
        f"🔍 <b>در حال جستجو برای «{raw_input}»...</b>\n\n"
        "<blockquote>⏱ چند لحظه صبر کن، معمولاً ۲ تا ۵ ثانیه طول می‌کشه.</blockquote>",
        parse_mode="HTML"
    )

    names_to_search = resolve_names(raw_input)
    found_files, block_models = await search_mc_assets(names_to_search)

    if not found_files:
        await message.answer(
            f"❌ <b>هیچ فایلی برای «{raw_input}» پیدا نشد</b>\n\n"
            "<blockquote>💡 نکات:\n"
            "• اسم انگلیسی دقیق بنویس (مثل <code>iron_sword</code>)\n"
            "• می‌تونی از فاصله یا آندرلاین استفاده کنی\n"
            "• برای زره بنویس: <code>iron armor</code> یا <code>diamond armor</code></blockquote>",
            parse_mode="HTML"
        )
        user_modes.pop(user_id, None)
        return

    user_selections[user_id] = []
    user_data[user_id] = {"files": found_files, "item_name": raw_input, "block_models": block_models}

    # ساخت کیبورد
    kb = build_asset_keyboard(found_files, user_selections[user_id], block_models)

    block3d_hint = (
        "\n🧊 <b>این بلاک قابل ساخت به‌صورت آیتم سه‌بعدی (مکعب با تکسچر دقیق هر وجه) هست.</b>"
        if block_models else ""
    )

    sent = await message.answer(
        f"✅ <b>{len(found_files)} فایل پیدا شد!</b>\n"
        f"(جستجو در {len(names_to_search)} نام: {', '.join(names_to_search[:5])}{'...' if len(names_to_search) > 5 else ''})\n\n"
        "🔘 <b>۰ از ۵ فایل انتخاب شده</b>\n"
        f"{block3d_hint}\n\n"
        "<blockquote>💡 فایل‌های مورد نظرتو با دکمه‌ها انتخاب کن (حداکثر ۵تا)، بعد «ارسال انتخاب‌ها» یا «ارسال همه» رو بزن.</blockquote>",
        reply_markup=kb,
        parse_mode="HTML"
    )

    user_data[user_id]["msg_id"] = sent.message_id
    user_data[user_id]["found_count"] = len(found_files)
    user_data[user_id]["names_to_search"] = names_to_search


@dp.callback_query(F.data.startswith("asset_select:"))
async def select_asset(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    idx = int(callback.data.split(":")[1])

    if user_id not in user_selections:
        user_selections[user_id] = []

    files = user_data.get(user_id, {}).get("files", [])
    if idx >= len(files):
        await callback.answer("❌ فایل پیدا نشد!", show_alert=True)
        return

    selected = files[idx]
    already = any(f["url"] == selected["url"] for f in user_selections[user_id])

    if already:
        user_selections[user_id] = [f for f in user_selections[user_id] if f["url"] != selected["url"]]
        toast = f"❎ {selected['name']} از انتخاب حذف شد"
    elif len(user_selections[user_id]) >= 5:
        await callback.answer("⚠️ حداکثر ۵ فایل می‌تونی انتخاب کنی!", show_alert=True)
        return
    else:
        user_selections[user_id].append(selected)
        toast = f"✅ {selected['name']} انتخاب شد ({len(user_selections[user_id])}/5)"

    # به‌روزرسانی متن و کیبورد پیام
    count = len(user_selections[user_id])
    selected_names = "، ".join(f["name"] for f in user_selections[user_id]) if count > 0 else "هیچی انتخاب نشده"
    names_to_search = user_data[user_id].get("names_to_search", [])
    found_count = user_data[user_id].get("found_count", len(files))

    new_text = (
        f"✅ <b>{found_count} فایل پیدا شد!</b>\n"
        f"(جستجو در {len(names_to_search)} نام: {', '.join(names_to_search[:5])}{'...' if len(names_to_search) > 5 else ''})\n\n"
        f"🟢 <b>{count} از ۵ فایل انتخاب شده</b>\n"
        f"{'📋 انتخاب‌ها: ' + selected_names if count > 0 else '🔘 هنوز چیزی انتخاب نشده'}\n\n"
        "<blockquote>💡 فایل‌های مورد نظرتو با دکمه‌ها انتخاب کن، بعد «ارسال انتخاب‌ها» یا «ارسال همه» رو بزن.</blockquote>"
    )
    new_kb = build_asset_keyboard(files, user_selections[user_id], user_data[user_id].get("block_models"))

    try:
        await callback.message.edit_text(new_text, reply_markup=new_kb, parse_mode="HTML")
    except Exception:
        pass  # اگر متن تغییر نکرده باشه تلگرام خطا می‌ده، نادیده می‌گیریم

    await callback.answer(toast)


@dp.callback_query(F.data == "asset_send")
async def send_selected_assets(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    selected = user_selections.get(user_id, [])

    if not selected:
        await callback.answer("هیچ فایلی انتخاب نشده!", show_alert=True)
        return

    await _send_asset_files(callback, selected, user_id)


@dp.callback_query(F.data == "asset_send_all")
async def send_all_assets(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    files = user_data.get(user_id, {}).get("files", [])

    if not files:
        await callback.answer("فایلی پیدا نشد!", show_alert=True)
        return

    await _send_asset_files(callback, files, user_id)


@dp.callback_query(F.data == "asset_cancel")
async def cancel_asset(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_selections.pop(user_id, None)
    user_data.pop(user_id, None)
    user_modes.pop(user_id, None)
    await callback.message.edit_text("❌ عملیات لغو شد.")

@dp.callback_query(F.data.startswith("asset_build3d:"))
async def build_asset_3d(callback: types.CallbackQuery):
    """
    ساخت آیتم سه‌بعدیِ یک بلاک: مکعبی که هر وجهش تکسچر درست خودش رو داره (مثل
    Command Block که هر جهتش یک تکسچر جداست) یا برای بلاک‌های ساده مثل Dirt
    هر ۶ وجه یک تکسچر یکسان. فقط هر ۱۰ دقیقه یک‌بار برای هر کاربر مجازه.
    """
    user_id = callback.from_user.id
    block_msg = get_access_block_message(user_id)
    if block_msg:
        await callback.answer(strip_html_tags(block_msg), show_alert=True)
        return

    block_name = callback.data.split(":", 1)[1]
    block_models = user_data.get(user_id, {}).get("block_models", {})
    faces = block_models.get(block_name)
    if not faces:
        await callback.answer("❌ اطلاعات این بلاک منقضی شده، دوباره جستجو کن.", show_alert=True)
        return

    # ── چک لیمیت ۱۰ دقیقه‌ای ──
    now = datetime.utcnow()
    last = block3d_last_use.get(user_id)
    if last and (now - last) < timedelta(minutes=BLOCK3D_COOLDOWN_MINUTES):
        remaining = timedelta(minutes=BLOCK3D_COOLDOWN_MINUTES) - (now - last)
        remaining_min = int(remaining.total_seconds() // 60) + 1
        await callback.answer(
            f"⏳ فقط هر {BLOCK3D_COOLDOWN_MINUTES} دقیقه یک‌بار می‌تونی آیتم سه‌بعدی بسازی.\n"
            f"~{remaining_min} دقیقه‌ی دیگه دوباره امتحان کن.",
            show_alert=True
        )
        return

    block3d_last_use[user_id] = now  # همین الان قفل می‌کنیم که کلیک‌های موازی هم مسدود بشن
    await callback.answer()

    status_msg = await callback.message.answer(
        f"🧊 <b>در حال ساخت آیتم سه‌بعدی «{block_name}»...</b>\n\n"
        "<blockquote>⏱ چند ثانیه صبر کن، دانلود تکسچرها + رندر انجام میشه.</blockquote>",
        parse_mode="HTML"
    )

    work_id = uuid.uuid4().hex[:10]
    work_dir = os.path.join(OUTPUT_DIR, "block3d", work_id)

    try:
        async with aiohttp.ClientSession() as session:
            # "_tint" یک کلید کمکیه (نه اسم وجه واقعی)، نباید به‌عنوان فایل تکسچر دانلود بشه
            texture_files = {v for k, v in faces.items() if k in FACE_KEYS}
            local_textures = await download_block_textures(session, texture_files, work_dir)

        missing = texture_files - set(local_textures.keys())
        if missing:
            await status_msg.edit_text(
                f"❌ <b>دانلود تکسچر ناموفق:</b> {', '.join(missing)}\n"
                "دوباره امتحان کن.",
                parse_mode="HTML"
            )
            return

        obj_path, mtl_path = build_block_cube_obj(faces, local_textures, work_dir, block_name)
        icon_path = os.path.join(work_dir, f"{block_name}_icon.png")
        await run_block_render(obj_path, icon_path, size=512)

        zip_path = os.path.join(OUTPUT_DIR, f"{block_name}_3d.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(obj_path, os.path.basename(obj_path))
            zf.write(mtl_path, os.path.basename(mtl_path))
            zf.write(icon_path, os.path.basename(icon_path))
            for fname, fpath in local_textures.items():
                zf.write(fpath, fname)

        unique_faces = len({v for k, v in faces.items() if k in FACE_KEYS})
        face_info = (
            "یک تکسچر روی هر ۶ وجه (بلاک ساده)"
            if unique_faces == 1 else
            f"{unique_faces} تکسچر مختلف روی وجه‌های مختلف (بلاک جهت‌دار)"
        )

        await callback.message.answer_photo(
            FSInputFile(icon_path),
            caption=(
                f"🧊 <b>{block_name}</b>\n"
                f"📐 {face_info}\n"
                f"⏳ ساخت بعدی: {BLOCK3D_COOLDOWN_MINUTES} دقیقه‌ی دیگه"
            ),
            parse_mode="HTML"
        )
        await callback.message.answer_document(
            FSInputFile(zip_path),
            caption=f"📦 فایل OBJ + MTL + تکسچرها برای {block_name}"
        )
        await status_msg.delete()

    except Exception as e:
        print(f"[block3d] failed for {block_name!r} (user={user_id}):\n{e}", flush=True)
        await status_msg.edit_text(
            f"❌ خطا در ساخت آیتم سه‌بعدی:\n<code>{str(e)[:300]}</code>",
            parse_mode="HTML"
        )
    finally:
        try:
            if os.path.isdir(work_dir):
                for f in os.listdir(work_dir):
                    os.remove(os.path.join(work_dir, f))
                os.rmdir(work_dir)
        except Exception:
            pass


async def _send_asset_files(callback: types.CallbackQuery, files: list, user_id: int):
    """هلپر ارسال فایل‌ها با فرمت دلخواه"""
    await callback.message.answer(
        f"📤 <b>در حال ارسال {len(files)} فایل...</b>\n\n"
        "<blockquote>⏱ چند ثانیه صبر کن، بسته به تعداد فایل‌ها کمی طول می‌کشه.</blockquote>",
        parse_mode="HTML"
    )

    success = 0
    for file in files:
        try:
            file_path = file["url"]
            file_name = file["name"]

            # Caption دقیقاً طبق خواسته شما
            if os.path.exists(file_path):  # فایل محلی (مثل armor layers)
                caption = f"<b>{file_name}</b>\n<code>{file_path}</code>"
            else:  # فایل از گیت‌هاب
                caption = f"<b>{file_name}</b>\n<code>{file_path}</code>"

            # ارسال فایل
            if os.path.exists(file_path):
                await callback.message.answer_document(
                    FSInputFile(file_path),
                    caption=caption,
                    parse_mode="HTML"
                )
            else:
                # دانلود و ارسال فایل ریموت
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_path, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            temp_path = os.path.join(OUTPUT_DIR, file_name)
                            with open(temp_path, "wb") as f:
                                f.write(data)
                            
                            await callback.message.answer_document(
                                FSInputFile(temp_path),
                                caption=caption,
                                parse_mode="HTML"
                            )
                            os.remove(temp_path)
                        else:
                            await callback.message.answer(f"❌ دانلود {file_name} ناموفق")
            success += 1

        except Exception as e:
            await callback.message.answer(f"❌ خطا در ارسال {file_name}:\n{str(e)[:100]}")

    await callback.message.answer(f"✅ {success} از {len(files)} فایل ارسال شد!")

    # پاک کردن داده‌ها
    user_selections.pop(user_id, None)
    user_data.pop(user_id, None)
    user_modes.pop(user_id, None)

# ====================== HUD OVERLAY - تنظیمات و انتخاب درصد XP ======================

async def _process_resource_pack(answer_target, user_id: int, input_path: str, output_path: str, doc_name: str, xp_percent: float):
    """اجرای واقعی پردازش پک و ارسال نتیجه یا خطا. answer_target هر آبجکتی با متد answer/answer_document (message یا callback.message)."""
    try:
        await run_node_processor(input_path, output_path, xp_percent=xp_percent)
        user_modes.pop(user_id, None)
        await answer_target.answer_document(FSInputFile(output_path), caption="✅ ریسورس پک پردازش و UI ساخته شد!")
    except Exception as e:
        err_text = str(e)
        # همیشه خطای کامل رو تو لاگ سرور چاپ کن، چون پیام کوتاه‌شده‌ای که به
        # کاربر نشون داده میشه ممکنه دلیل واقعی خطا رو نشون نده
        print(f"[resource_pack] processing failed for {doc_name!r} (user={user_id}):\n{err_text}", flush=True)

        is_mcpack = doc_name.endswith(".mcpack")
        lowered = err_text.lower()
        missing_sprite_file = ("پیدا نشد:" in err_text or "یک فایل نیست" in err_text or "missing sprite sheet" in lowered)
        path_error = is_mcpack and missing_sprite_file

        if path_error:
            await _send_manual_upload_prompt(
                answer_target, user_id,
                reason=f"مسیر فایل‌ها در mcpack با Java فرق داره\n<code>{err_text[:200]}</code>"
            )
        else:
            await answer_target.answer(
                f"❌ خطا در پردازش:\n<code>{err_text[:300]}</code>\n\n"
                "میتونی فایل‌ها رو دستی بفرستی 👇",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📤 ارسال دستی فایل‌ها", callback_data="manual_pack_start")
                ]])
            )


@dp.callback_query(ResourcePackSettingsState.choosing_percent, F.data == "rp_xp_noop")
async def rp_xp_noop(callback: types.CallbackQuery, state: FSMContext):
    # این دکمه فقط درصد فعلی رو نشون می‌ده و کاری انجام نمی‌ده
    await callback.answer()


async def _rp_adjust_percent(callback: types.CallbackQuery, state: FSMContext, delta: int):
    data = await state.get_data()
    current = _rp_xp_percent_int(data)
    new_value = max(0, min(100, current + delta))
    await state.update_data(rp_xp_percent_int=new_value)
    await callback.message.edit_text(
        build_rp_percent_text(new_value),
        parse_mode="HTML",
        reply_markup=build_rp_percent_kb(new_value)
    )
    await callback.answer()


@dp.callback_query(ResourcePackSettingsState.choosing_percent, F.data == "rp_xp_dec10")
async def rp_xp_dec10(callback: types.CallbackQuery, state: FSMContext):
    await _rp_adjust_percent(callback, state, -10)


@dp.callback_query(ResourcePackSettingsState.choosing_percent, F.data == "rp_xp_dec5")
async def rp_xp_dec5(callback: types.CallbackQuery, state: FSMContext):
    await _rp_adjust_percent(callback, state, -5)


@dp.callback_query(ResourcePackSettingsState.choosing_percent, F.data == "rp_xp_inc5")
async def rp_xp_inc5(callback: types.CallbackQuery, state: FSMContext):
    await _rp_adjust_percent(callback, state, 5)


@dp.callback_query(ResourcePackSettingsState.choosing_percent, F.data == "rp_xp_inc10")
async def rp_xp_inc10(callback: types.CallbackQuery, state: FSMContext):
    await _rp_adjust_percent(callback, state, 10)


@dp.callback_query(ResourcePackSettingsState.choosing_percent, F.data == "rp_xp_start")
async def rp_xp_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    input_path = data.get("rp_input_path")
    output_path = data.get("rp_output_path")
    doc_name = data.get("rp_doc_name", "")
    xp_percent = _rp_xp_percent_int(data) / 100

    if not input_path or not os.path.exists(input_path):
        await callback.answer("❌ فایل پک پیدا نشد. لطفاً دوباره ارسال کن.", show_alert=True)
        await state.clear()
        user_modes.pop(user_id, None)
        return

    await callback.answer()
    await callback.message.edit_text(
        "🔄 <b>در حال پردازش ریسورس پک...</b>\n\n"
        "<blockquote>⏱ چند ثانیه صبر کن، معمولاً ۱۰ تا ۲۵ ثانیه طول می‌کشه.</blockquote>",
        reply_markup=None,
        parse_mode="HTML"
    )
    await _process_resource_pack(callback.message, user_id, input_path, output_path, doc_name, xp_percent)
    await state.clear()


# ====================== RESOURCE PACK MANUAL UPLOAD ======================

async def _send_manual_upload_prompt(message: types.Message, user_id: int, reason: str):
    """وقتی پک آپلود‌شده قابل پردازش خودکار نیست، راهنمای ارسال دستی رو نشون میده"""
    user_modes.pop(user_id, None)
    await message.answer(
        f"⚠️ <b>پردازش خودکار ممکن نشد</b>\n"
        f"📌 دلیل: {reason}\n\n"
        "میتونی فایل‌های پک رو <b>دستی</b> بفرستی تا بات پردازش کنه 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📤 ارسال دستی فایل‌ها", callback_data="manual_pack_start")
        ]])
    )


@dp.callback_query(F.data == "manual_pack_start")
async def manual_pack_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    block_msg = get_access_block_message(user_id)
    if block_msg:
        await callback.answer(strip_html_tags(block_msg), show_alert=True)
        return

    await state.set_state(ResourcePackManualState.waiting_icon)
    await callback.message.edit_text(
        "📋 <b>مرحله ۱ از ۲ — ارسال icons.png</b>\n\n"
        "این فایل هر اسمی داشته باشه مهم نیست،\n"
        "بات خودش اسمشو به <code>icons.png</code> تغییر میده.\n\n"
        "📁 <b>Java Pack (zip):</b>\n"
        "  مسیر: <code>assets/minecraft/textures/gui/icons.png</code>\n\n"
        "📁 <b>Bedrock (mcpack):</b>\n"
        "  مسیر: <code>textures/gui/icons.png ( هر اسم دیگری شبیه این )</code>\n\n"
        "➡️ <b>فایل PNG رو بفرست:</b>\n\n"
        "<blockquote>💡 بعد از این، یه فایل PNG دیگه (widgets.png) هم ازت می‌خوایم و در نهایت پک ساخته و پردازش میشه.\n"
        "⏱ زمان تقریبی کل فرایند: ۱۵ تا ۳۰ ثانیه</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ لغو", callback_data="manual_pack_cancel")
        ]])
    )


@dp.callback_query(F.data == "manual_pack_cancel")
async def manual_pack_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_modes.pop(callback.from_user.id, None)
    user_data.pop(callback.from_user.id, None)
    await callback.message.edit_text("❌ عملیات دستی لغو شد.\n\nمیتونی دوباره از منو شروع کنی.")


@dp.message(ResourcePackManualState.waiting_icon, F.document)
async def manual_receive_icon(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    doc = message.document

    if not doc.file_name.lower().endswith(".png"):
        await message.answer(
            "❌ فقط فایل <b>PNG</b> قبول میشه.\n"
            "هر اسمی داشته باشه مشکلی نیست، فقط باید PNG باشه.",
            parse_mode="HTML"
        )
        return

    # هر اسمی داشت، به icons.png تغییر میده
    icons_path = os.path.join(INPUT_DIR, f"manual_{user_id}_icons.png")
    file = await bot.get_file(doc.file_id)
    await bot.download_file(file.file_path, destination=icons_path)

    await state.update_data(icons_path=icons_path)
    await state.set_state(ResourcePackManualState.waiting_inventory)

    await message.answer(
        "✅ <b>icons.png دریافت شد!</b>\n\n"
        "📋 <b>مرحله ۲ از ۲ — ارسال widgets.png</b>\n\n"
        "این فایل هم هر اسمی داشته باشه مهم نیست،\n"
        "بات اسمشو به <code>widgets.png</code> تغییر میده.\n\n"
        "📁 <b>Java Pack (zip):</b>\n"
        "  مسیر: <code>assets/minecraft/textures/gui/widgets.png</code>\n\n"
        "📁 <b>Bedrock (mcpack):</b>\n"
        "  مسیر: <code>textures/gui/gui.png ( هر اسم دیگری شبیه این )</code>\n\n"
        "➡️ <b>فایل PNG رو بفرست:</b>\n\n"
        "<blockquote>💡 بعد از دریافت این فایل، پک ساخته میشه و بلافاصله پردازش شروع میشه.\n"
        "⏱ زمان تقریبی پردازش: ۱۰ تا ۲۰ ثانیه</blockquote>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ لغو", callback_data="manual_pack_cancel")
        ]])
    )


@dp.message(ResourcePackManualState.waiting_inventory, F.document)
async def manual_receive_inventory(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    doc = message.document

    if not doc.file_name.lower().endswith(".png"):
        await message.answer(
            "❌ فقط فایل <b>PNG</b> قبول میشه.\n"
            "هر اسمی داشته باشه مشکلی نیست، فقط باید PNG باشه.",
            parse_mode="HTML"
        )
        return

    state_data = await state.get_data()
    icons_path = state_data.get("icons_path")

    if not icons_path or not os.path.exists(icons_path):
        await message.answer(
            "❌ فایل icons.png پیدا نشد. لطفاً دوباره از اول شروع کن.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 شروع مجدد", callback_data="manual_pack_start")
            ]])
        )
        await state.clear()
        return

    await message.answer(
        "🔄 <b>در حال ساخت پک و پردازش...</b>\n\n"
        "<blockquote>⏱ چند ثانیه صبر کن، معمولاً ۱۰ تا ۲۰ ثانیه طول می‌کشه.</blockquote>",
        parse_mode="HTML"
    )

    # هر اسمی داشت، به widgets.png تغییر میده
    widgets_path = os.path.join(INPUT_DIR, f"manual_{user_id}_widgets.png")
    fake_zip_path = os.path.join(INPUT_DIR, f"manual_{user_id}_pack.zip")
    output_path = os.path.join(OUTPUT_DIR, f"manual_{user_id}_ui.png")

    try:
        file = await bot.get_file(doc.file_id)
        await bot.download_file(file.file_path, destination=widgets_path)

        # ساخت zip با مسیرهای دقیقی که processor.mjs انتظار داره
        with zipfile.ZipFile(fake_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(icons_path,   "assets/minecraft/textures/gui/icons.png")
            zf.write(widgets_path, "assets/minecraft/textures/gui/widgets.png")

        await run_node_processor(fake_zip_path, output_path)

        await message.answer_document(
            FSInputFile(output_path),
            caption="✅ پردازش موفق!\n🎨 UI پک از فایل‌های دستی ساخته شد."
        )
        await state.clear()
        user_modes.pop(user_id, None)
        user_data.pop(user_id, None)

    except Exception as e:
        await message.answer(
            f"❌ خطا در پردازش:\n<code>{str(e)[:400]}</code>",
            parse_mode="HTML"
        )
        await state.clear()

    finally:
        for p in [icons_path, widgets_path, fake_zip_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


# ====================== LICENSE EXPIRY CHECKER ======================
async def license_expiry_checker():
    """هر دقیقه لایسنس‌های منقضی‌شده رو پیدا می‌کند و یک‌بار به کاربر اطلاع می‌دهد."""
    while True:
        try:
            session = Session()
            try:
                now = datetime.utcnow()
                expired = session.query(License).filter(
                    License.used == True,
                    License.expires_at.isnot(None),
                    License.expires_at <= now,
                    License.expired_notified == False
                ).all()

                for lic in expired:
                    lic.expired_notified = True
                    if lic.user_id:
                        # فقط اگر کاربر هیچ لایسنس فعال دیگه‌ای نداره اطلاع بده و منو رو حذف کن
                        if get_access_block_message(lic.user_id):
                            try:
                                await bot.send_message(
                                    lic.user_id,
                                    "⏳ <b>لایسنس شما به پایان رسیده</b>\n\n"
                                    "<blockquote>مدت اعتبار لایسنس‌تون تموم شده و دسترسیتون به امکانات ربات غیرفعال شد.\n\n"
                                    "برای خرید مجدد و ادامه استفاده از امکانات ربات:\n\n"
                                    "📩 @AmirMah198</blockquote>",
                                    parse_mode="HTML",
                                    reply_markup=types.ReplyKeyboardRemove()
                                )
                                user_modes.pop(lic.user_id, None)
                            except Exception as e:
                                print(f"Failed to notify expired license user {lic.user_id}: {e}")

                if expired:
                    session.commit()
            finally:
                session.close()
        except Exception as e:
            print(f"Error in license_expiry_checker: {e}")

        await asyncio.sleep(60)


# ====================== MAIN ======================
async def main():
    print("🚀 Bot started successfully")
    asyncio.create_task(license_expiry_checker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
