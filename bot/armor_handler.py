# ====================== ARMOR TRIM HANDLER ======================
#  Kafan tarin armor make  minecraft
#  baray estefade local :
# pip install Pillow
#  with a little AI help (: tnx ( grok.ccom ) 

from pathlib import Path
import io

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

try:
    from PIL import Image, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ====================== CONSTANTS ======================

# آرمورهای موجود (stone و wooden حذف شدند)
ARMOR_TYPES = ["leather", "iron", "chainmail", "golden", "diamond", "netherite"]

ARMOR_FILE_MAP = {
    "leather":   "leather",
    "iron":      "iron",
    "chainmail": "chainmail",
    "golden":    "gold",
    "diamond":   "diamond",
    "netherite": "netherite",
}

ARMOR_TRIMS = [
    "none", "coast", "dune", "eye", "host", "raiser", "rib",
    "sentry", "shaper", "silence", "snout", "spire", "tide",
    "vex", "ward", "wayfinder", "wild",
]

# رنگ‌های دقیق مواد تریم
TRIM_MATERIALS = {
    "amethyst":  (154,  90, 200),
    "copper":    (178, 100,  72),
    "diamond":   ( 68, 214, 202),
    "emerald":   ( 71, 201,  95),
    "gold":      (237, 197,  58),
    "iron":      (216, 216, 216),
    "lapis":     ( 45,  78, 167),
    "netherite": ( 52,  47,  55),
    "quartz":    (226, 219, 210),
    "redstone":  (171,   9,   9),
}

# 16 رنگ دقیق ماینکرافت Java برای آرمور چرمی
# رنگ‌ها از کد منبع ماینکرافت (DyeColor RGB)
LEATHER_COLORS = {
    "none":       ( 160, 101,  64),   # قهوه‌ای پیش‌فرض چرم
    "white":      (249, 255, 254),
    "orange":     (249, 128,  29),
    "magenta":    (199,  78, 189),
    "light_blue": ( 58, 179, 218),
    "yellow":     (254, 216,  61),
    "lime":       (128, 199,  31),
    "pink":       (243, 139, 170),
    "gray":       ( 71,  79,  82),
    "light_gray": (157, 157, 151),
    "cyan":       ( 22, 156, 156),
    "purple":     (137,  50, 184),
    "blue":       ( 60,  68, 170),
    "brown":      (131,  84,  50),
    "green":      ( 94, 124,  22),
    "red":        (176,  46,  38),
    "black":      ( 29,  29,  33),
}

LEATHER_COLOR_EMOJI = {
    "none":       "🟫",
    "white":      "⬜",
    "orange":     "🟠",
    "magenta":    "🟣",
    "light_blue": "🔵",
    "yellow":     "🟡",
    "lime":       "🟢",
    "pink":       "🩷",
    "gray":       "⬛",
    "light_gray": "🩶",
    "cyan":       "🩵",
    "purple":     "💜",
    "blue":       "🔷",
    "brown":      "🟤",
    "green":      "🌿",
    "red":        "🔴",
    "black":      "🖤",
}

LEATHER_COLOR_NAMES_FA = {
    "none":       "پیش‌فرض (قهوه‌ای)",
    "white":      "سفید",
    "orange":     "نارنجی",
    "magenta":    "ارغوانی",
    "light_blue": "آبی روشن",
    "yellow":     "زرد",
    "lime":       "سبز لیمویی",
    "pink":       "صورتی",
    "gray":       "خاکستری",
    "light_gray": "خاکستری روشن",
    "cyan":       "فیروزه‌ای",
    "purple":     "بنفش",
    "blue":       "آبی",
    "brown":      "قهوه‌ای تیره",
    "green":      "سبز",
    "red":        "قرمز",
    "black":      "مشکی",
}

BASE_DIR = Path(__file__).resolve().parent
ARMORS_DIR = BASE_DIR / "armors"

# state هر کاربر
user_skins: dict[int, bytes] = {}
armor_build_state: dict[int, dict] = {}


# ====================== KEYBOARDS ======================

def kb_armor(current: str) -> InlineKeyboardMarkup:
    idx = ARMOR_TYPES.index(current) if current in ARMOR_TYPES else 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔄 آرمور: {current.upper()}  ({idx+1}/{len(ARMOR_TYPES)})",
            callback_data="a_armor_cycle"
        )],
        [InlineKeyboardButton(
            text="➡️ مرحله بعد",
            callback_data="a_after_armor"
        )],
    ])


def kb_leather_color(current_key: str) -> InlineKeyboardMarkup:
    keys = list(LEATHER_COLORS.keys())
    idx = keys.index(current_key) if current_key in keys else 0
    emoji = LEATHER_COLOR_EMOJI.get(current_key, "🟫")
    fa = LEATHER_COLOR_NAMES_FA.get(current_key, current_key)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔄 رنگ: {emoji} {fa}  ({idx+1}/{len(keys)})",
            callback_data="a_lcolor_cycle"
        )],
        [InlineKeyboardButton(
            text="➡️ مرحله بعد: تریم",
            callback_data="a_to_trim"
        )],
    ])


def kb_trim(trim_idx: int) -> InlineKeyboardMarkup:
    name = ARMOR_TRIMS[trim_idx]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔄 تریم: {name}  ({trim_idx}/{len(ARMOR_TRIMS)-1})",
            callback_data="a_trim_cycle"
        )],
        [InlineKeyboardButton(
            text="➡️ مرحله بعد: ماده تریم",
            callback_data="a_to_material"
        )],
    ])


def kb_material(current: str) -> InlineKeyboardMarkup:
    mat_list = list(TRIM_MATERIALS.keys())
    idx = mat_list.index(current) if current in mat_list else 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔄 ماده: {current}  ({idx+1}/{len(mat_list)})",
            callback_data="a_mat_cycle"
        )],
        [InlineKeyboardButton(
            text="➡️ مرحله بعد: اینچنت",
            callback_data="a_to_enchant"
        )],
    ])


def kb_enchant(enchanted: bool) -> InlineKeyboardMarkup:
    status = "✨ روشن" if enchanted else "⬛ خاموش"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔄 اینچنت: {status}",
            callback_data="a_enchant_toggle"
        )],
        [InlineKeyboardButton(
            text="✅ پایان و ساخت آرمور",
            callback_data="a_finish"
        )],
    ])


# ====================== IMAGE PROCESSING ======================
def colorize_grayscale(img: Image.Image, color_rgb: tuple) -> Image.Image:
    """
    رنگ‌آمیزی تکسچر trim با رنگ ماده.
    فقط از کانال R استفاده می‌کند (trim های ماینکرافت در کانال R ذخیره‌اند).
    """
    rgba = img.convert("RGBA")
    w, h = rgba.size
    result = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    src = rgba.load()
    dst = result.load()
    mr, mg, mb = color_rgb
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            if a == 0:
                continue
            # trim های ماینکرافت: شدت در کانال R ذخیره است نه luminance
            intensity = r / 255.0
            dst[x, y] = (int(mr * intensity), int(mg * intensity), int(mb * intensity), a)
    return result

def lighten_color(color_rgb: tuple, factor: float = 1.42) -> tuple:
    """
    رنگ روشن‌تر برای overlay چرم.
    ماینکرافت از فاکتور ~1.42 استفاده می‌کند (مقدار دقیق از کد Java).
    مقادیر بالاتر از 255 کلمپ می‌شوند.
    """
    r, g, b = color_rgb
    return (
        min(255, int(r * factor)),
        min(255, int(g * factor)),
        min(255, int(b * factor)),
    )

def apply_leather_color(base: Image.Image, overlay_path: Path, color_rgb: tuple) -> Image.Image:
    """رنگ‌آمیزی دقیق آرمور چرمی مثل ماینکرافت"""
    # مرحله ۱: base را به grayscale تبدیل و رنگ‌آمیزی کن
    result = colorize_grayscale(base, color_rgb)

    # مرحله ۲: overlay (highlight)
    if overlay_path and overlay_path.exists():
        overlay_img = Image.open(overlay_path).convert("RGBA")
        if overlay_img.size != base.size:
            overlay_img = overlay_img.resize(base.size, Image.NEAREST)
        
        highlight_color = lighten_color(color_rgb, factor=1.42)
        colored_overlay = colorize_grayscale(overlay_img, highlight_color)
        result = Image.alpha_composite(result, colored_overlay)

    return result

def apply_enchant_glint(img: Image.Image) -> Image.Image:
    """
    افکت Enchant Glint واقعی با تکسچر استخراج‌شده از ماینکرافت
    (شبیه Mine-imator و خود بازی)
    """
    base = img.convert("RGBA")
    w, h = base.size

    glint_path = ARMORS_DIR / "enchanted_glint.png"
    
    if not glint_path.exists():
        print(f"[armor] ⚠️ فایل enchanted_glint.png پیدا نشد")
        return base  # fallback

    try:
        glint_tex = Image.open(glint_path).convert("RGBA")
        
        # تطبیق اندازه با روش پیکسلی (مهم برای تکسچر ماینکرافت)
        if glint_tex.size != (w, h):
            glint_tex = glint_tex.resize((w, h), Image.NEAREST)

        GLINT_STRENGTH = 0.82   # ← این عدد رو می‌تونی تنظیم کنی (0.6 تا 1.0)

        # ساخت لایه glint
        glint_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gl_px = glint_layer.load()
        tex_px = glint_tex.load()

        for y in range(h):
            for x in range(w):
                r, g, b, a = tex_px[x, y]
                if a == 0:
                    continue
                intensity = (r + g + b) / (3 * 255.0)
                alpha = int(255 * intensity * GLINT_STRENGTH)
                gl_px[x, y] = (103, 25, 255, min(255, alpha))

        # Screen Blend — دقیقاً مثل ماینکرافت
        result = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        res = result.load()
        base_px = base.load()
        gl2 = glint_layer.load()

        for y in range(h):
            for x in range(w):
                br, bg, bb, ba = base_px[x, y]
                gr, gg, gb, ga = gl2[x, y]
                
                if ga == 0:
                    res[x, y] = (br, bg, bb, ba)
                    continue

                t = ga / 255.0
                
                screen_r = 255 - int((255 - br) * (255 - gr) / 255)
                screen_g = 255 - int((255 - bg) * (255 - gg) / 255)
                screen_b = 255 - int((255 - bb) * (255 - gb) / 255)

                nr = int(br + (screen_r - br) * t)
                ng = int(bg + (screen_g - bg) * t)
                nb = int(bb + (screen_b - bb) * t)

                res[x, y] = (min(255, nr), min(255, ng), min(255, nb), ba)

        return result

    except Exception as e:
        print(f"[armor] خطا در اعمال glint texture: {e}")
        return base

def apply_armor_on_skin(armor_layer: Image.Image, skin_bytes: bytes) -> Image.Image:
    """چسباندن آرمور روی اسکین"""
    skin = Image.open(io.BytesIO(skin_bytes)).convert("RGBA")
    
    # resize armor به اندازه اسکین (معمولاً ۱۶x۱۶ → ۶۴x۶۴)
    if armor_layer.size != skin.size:
        armor_layer = armor_layer.resize(skin.size, Image.NEAREST)
    
    # composite
    result = Image.alpha_composite(skin, armor_layer)
    return result
        
def apply_enchant_glint_fallback(base: Image.Image) -> Image.Image:
    """fallback ساده در صورت نبود فایل glint"""
    print("[armor] استفاده از glint fallback")
    w, h = base.size
    GR, GG, GB = 103, 25, 255
    result = base.copy()
    px = result.load()
    
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            intensity = (r + g + b) / (3 * 255.0)
            t = 0.22 + 0.18 * intensity  # شدت ملایم fallback
            sr = 255 - int((255 - r) * (255 - GR) / 255)
            sg = 255 - int((255 - g) * (255 - GG) / 255)
            sb = 255 - int((255 - b) * (255 - GB) / 255)
            px[x, y] = (
                min(255, int(r + (sr - r) * t)),
                min(255, int(g + (sg - g) * t)),
                min(255, int(b + (sb - b) * t)),
                a
            )
    return result

def build_layer(armor_key: str, leather_color_key: str,
                trim_name: str, mat_color: tuple, layer: str,
                enchanted: bool = False) -> bytes | None:
    """
    ترتیب صحیح لایه‌ها (مثل ماینکرافت Java):
    
    برای چرم:
      1. leather.png  رنگ‌شده با رنگ کاربر
      2. leather_overlay.png  رنگ‌شده با رنگ روشن‌تر (highlight)
      3. trim  رنگ‌شده با رنگ ماده تریم  ← جدا از رنگ چرم
      4. (enchant glint)
    
    برای بقیه:
      1. armor.png  (base)
      2. trim  رنگ‌شده با رنگ ماده تریم
      3. (enchant glint)
    
    نکته مهم: رنگ چرم و رنگ ماده تریم کاملاً مجزا هستند و
    هیچ‌وقت با هم قاطی نمی‌شوند.
    """
    if not PIL_AVAILABLE:
        return None

    armor_file = ARMOR_FILE_MAP.get(armor_key, armor_key)
    armor_path = ARMORS_DIR / layer / f"{armor_file}.png"

    if not armor_path.exists():
        print(f"[armor] ❌ آرمور پیدا نشد: {armor_path}")
        return None

    try:
        base = Image.open(armor_path).convert("RGBA")

        if armor_key == "leather":
            # مرحله ۱+۲: رنگ‌آمیزی چرم (base + overlay)
            color_rgb = LEATHER_COLORS.get(leather_color_key, LEATHER_COLORS["none"])
            overlay_path = ARMORS_DIR / layer / "leather_overlay.png"
            result = apply_leather_color(base, overlay_path, color_rgb)
        else:
            result = base.copy()

        # مرحله ۳: تریم — کاملاً مستقل
        if trim_name != "none":
            trim_path = ARMORS_DIR / "trims" / layer / f"{trim_name}.png"
            if trim_path.exists():
                trim_img = Image.open(trim_path).convert("RGBA")
                if trim_img.size != result.size:
                    trim_img = trim_img.resize(result.size, Image.NEAREST)
                colored_trim = colorize_grayscale(trim_img, mat_color)
                # blend دستی به جای alpha_composite
                # چون trim هایی مثل silence آلفای 255 دارند و کل آرمور را می‌پوشانند
                res_px = result.load()
                tr_px = colored_trim.load()
                tw, th = result.size
                for ty in range(th):
                    for tx in range(tw):
                        tr, tg, tb, ta = tr_px[tx, ty]
                        if ta == 0:
                            continue
                        br, bg, bb, ba = res_px[tx, ty]
                        t = ta / 255.0
                        res_px[tx, ty] = (
                            int(br + (tr - br) * t),
                            int(bg + (tg - bg) * t),
                            int(bb + (tb - bb) * t),
                            ba if ba > 0 else ta
                        )
            else:
                print(f"[armor] ⚠️ تریم پیدا نشد: {trim_path}")

        # مرحله ۴: enchant glint
        if enchanted:
            result = apply_enchant_glint(result)

        buf = io.BytesIO()
        result.save(buf, format="PNG")
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"[armor] خطا در build_layer({armor_key}, {layer}): {e}")
        import traceback; traceback.print_exc()
        return None


def build_preview(armor_key: str, leather_color_key: str,
                   trim_name: str, mat_color: tuple,
                   enchanted: bool = False) -> bytes | None:
    """
    پیش‌نمایش سبک: فقط لایه ۱ (humanoid)، resize شده برای سرعت.
    خروجی JPEG کوچک برای ارسال سریع به صورت عکس.
    """
    data = build_layer(armor_key, leather_color_key, trim_name, mat_color,
                        "humanoid", enchanted)
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        # بزرگ‌نمایی pixel-art با NEAREST تا واضح بماند (نه بلور)
        preview = img.resize((img.width * 6, img.height * 6), Image.NEAREST)

        # پس‌زمینه خاکستری ساده برای دیده شدن بهتر قسمت‌های شفاف
        bg = Image.new("RGBA", preview.size, (54, 57, 63, 255))
        bg.alpha_composite(preview)

        buf = io.BytesIO()
        bg.convert("RGB").save(buf, format="JPEG", quality=80)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        print(f"[armor] خطا در build_preview: {e}")
        return None


# ====================== HANDLERS ======================

def register_armor_handlers(dp: Dispatcher, bot: Bot):

    PREVIEW_CAPTION = (
        "<b>پیش نمایش تکسچر Armor درخواستی شما👆</b>\n\n"
        "این فقط یک پیش نمایش است؛ فایل اصلی بعد از اتمام کار ارسال میشود✅"
    )

    def _preview_key(s: dict) -> tuple:
        return (s["armor"], s.get("leather_color", "none"), s["trim"], s["material"], s.get("enchanted", False))

    async def _send_preview(cb: types.CallbackQuery, s: dict):
        """
        ارسال یا آپدیت عکس پیش‌نمایش.
        - اگر state تغییر نکرده باشد → هیچ کاری نمیکند (جلوگیری از دو پیش‌نمایش)
        - اگر preview_msg_id وجود داشته باشد → edit_message_media
        - اگر نه → send_photo جدید
        پیش‌نمایش باید قبل از فراخوانی edit_text صدا زده شود تا بالاتر نمایش داده شود.
        """
        if not PIL_AVAILABLE:
            return

        armor         = s["armor"]
        leather_color = s.get("leather_color", "none")
        trim_name     = ARMOR_TRIMS[s["trim"]]
        material      = s["material"]
        mat_color     = TRIM_MATERIALS.get(material, (255, 255, 255))
        enchanted     = s.get("enchanted", False)

        # اگر پیش‌نمایش قبلاً با همین وضعیت فرستاده شده، دوباره نفرست
        current_key = _preview_key(s)
        if s.get("last_preview_key") == current_key and s.get("preview_msg_id"):
            return

        preview_bytes = build_preview(armor, leather_color, trim_name, mat_color, enchanted)
        if not preview_bytes:
            return

        chat_id = cb.message.chat.id
        photo_file = types.BufferedInputFile(preview_bytes, filename="preview.jpg")
        preview_msg_id = s.get("preview_msg_id")

        try:
            if preview_msg_id:
                try:
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=preview_msg_id,
                        media=types.InputMediaPhoto(
                            media=photo_file,
                            caption=PREVIEW_CAPTION,
                            parse_mode="HTML",
                        ),
                    )
                    s["last_preview_key"] = current_key
                    return  # موفقیت‌آمیز بود
                except Exception as e:
                    print(f"[armor] edit_message_media شکست خورد، عکس جدید ارسال می‌شود: {e}")
                    s["preview_msg_id"] = None  # ریست کن تا عکس جدید بفرستد

            # ارسال عکس جدید
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=photo_file,
                caption=PREVIEW_CAPTION,
                parse_mode="HTML",
            )
            s["preview_msg_id"] = msg.message_id
            s["last_preview_key"] = current_key

        except Exception as e2:
            print(f"[armor] خطا در ارسال پیش‌نمایش: {e2}")


    @dp.message(F.text == "🛡 ساخت آرمور با تریم")
    async def start(message: types.Message):
        uid = message.from_user.id
        try:
            from bot import get_access_block_message
            block_msg = get_access_block_message(uid)
            if block_msg:
                await message.answer(block_msg)
                return
        except ImportError:
            pass

        armor_build_state[uid] = {
            "armor":         ARMOR_TYPES[0],   # leather
            "leather_color": "none",
            "trim":          0,
            "material":      list(TRIM_MATERIALS.keys())[0],
            "enchanted":     False,
            "preview_msg_id": None,
            "last_preview_key": None,
            "skin_enabled":  False,
        }
        await message.answer(
            "🛡 <b>مرحله ۱ — نوع آرمور</b>\n\n"
            "روی دکمه کلیک کنید تا آرمور تغییر کند:",
            parse_mode="HTML",
            reply_markup=kb_armor(ARMOR_TYPES[0]),
        )

    # ─── مرحله ۱: چرخش آرمور ──────────────────────────────────────
    @dp.callback_query(F.data == "a_armor_cycle")
    async def armor_cycle(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("دوباره از منو شروع کنید.", show_alert=True); return
        idx = ARMOR_TYPES.index(s["armor"]) if s["armor"] in ARMOR_TYPES else 0
        s["armor"] = ARMOR_TYPES[(idx + 1) % len(ARMOR_TYPES)]
        await cb.message.edit_reply_markup(reply_markup=kb_armor(s["armor"]))
        await cb.answer(s["armor"].upper())

    @dp.callback_query(F.data == "a_after_armor")
    async def after_armor(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("دوباره از منو شروع کنید.", show_alert=True); return

        # پیش‌نمایش اول ارسال می‌شود تا بالاتر از پیام مرحله بعدی باشد
        await _send_preview(cb, s)

        # اگر چرم → مرحله رنگ چرم
        if s["armor"] == "leather":
            s["leather_color"] = "none"
            await cb.message.edit_text(
                f"🎨 <b>مرحله ۲ — رنگ آرمور چرمی</b>\n\n"
                f"روی دکمه کلیک کنید تا رنگ تغییر کند.\n"
                f"<b>none</b> = رنگ پیش‌فرض قهوه‌ای چرم",
                parse_mode="HTML",
                reply_markup=kb_leather_color("none"),
            )
        else:
            # سایر آرمورها → مستقیم به تریم
            await cb.message.edit_text(
                f"🎨 <b>مرحله ۲ — تریم</b>\n\n"
                f"آرمور: <b>{s['armor'].upper()}</b>\n\n"
                f"روی دکمه کلیک کنید تا تریم تغییر کند.\n"
                f"<code>none</code> = بدون تریم",
                parse_mode="HTML",
                reply_markup=kb_trim(s["trim"]),
            )

    # ─── مرحله ۲ (فقط چرم): چرخش رنگ ────────────────────────────

    @dp.callback_query(F.data == "a_lcolor_cycle")
    async def lcolor_cycle(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("خطا", show_alert=True); return
        keys = list(LEATHER_COLORS.keys())
        idx = keys.index(s["leather_color"]) if s["leather_color"] in keys else 0
        s["leather_color"] = keys[(idx + 1) % len(keys)]
        await cb.message.edit_reply_markup(reply_markup=kb_leather_color(s["leather_color"]))
        emoji = LEATHER_COLOR_EMOJI.get(s["leather_color"], "")
        fa = LEATHER_COLOR_NAMES_FA.get(s["leather_color"], s["leather_color"])
        await cb.answer(f"{emoji} {fa}")

    @dp.callback_query(F.data == "a_to_trim")
    async def to_trim(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("دوباره از منو شروع کنید.", show_alert=True); return

        armor_label = s["armor"].upper()
        extra = ""
        if s["armor"] == "leather":
            fa = LEATHER_COLOR_NAMES_FA.get(s["leather_color"], s["leather_color"])
            emoji = LEATHER_COLOR_EMOJI.get(s["leather_color"], "")
            extra = f"  |  رنگ: {emoji} {fa}"

        await cb.message.edit_text(
            f"🎨 <b>مرحله ۳ — تریم</b>\n\n"
            f"آرمور: <b>{armor_label}</b>{extra}\n\n"
            f"روی دکمه کلیک کنید تا تریم تغییر کند.\n"
            f"<code>none</code> = بدون تریم",
            parse_mode="HTML",
            reply_markup=kb_trim(s["trim"]),
        )

    # ─── مرحله ۳: چرخش تریم ──────────────────────────────────────

    @dp.callback_query(F.data == "a_trim_cycle")
    async def trim_cycle(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("خطا", show_alert=True); return
        s["trim"] = (s["trim"] + 1) % len(ARMOR_TRIMS)
        await cb.message.edit_reply_markup(reply_markup=kb_trim(s["trim"]))
        await cb.answer(ARMOR_TRIMS[s["trim"]])
        await _send_preview(cb, s)

    @dp.callback_query(F.data == "a_to_material")
    async def to_material(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("دوباره از منو شروع کنید.", show_alert=True); return

        trim_name = ARMOR_TRIMS[s["trim"]]
        if trim_name == "none":
            # بدون تریم → مستقیم به مرحله اینچنت (preview قبلاً در trim_cycle ارسال شده)
            await cb.message.edit_text(
                f"✨ <b>مرحله آخر — اینچنت</b>\n\n"
                f"آرمور: <b>{s['armor'].upper()}</b>  |  تریم: <b>none</b>\n\n"
                f"آیا می‌خواهید آرمور اینچنت باشد؟\n"
                f"(افکت بنفش درخشان روی تکسچر)",
                parse_mode="HTML",
                reply_markup=kb_enchant(s.get("enchanted", False)),
            )
            return

        await cb.message.edit_text(
            f"💎 <b>مرحله ۴ — ماده تریم</b>\n\n"
            f"آرمور: <b>{s['armor'].upper()}</b>  |  تریم: <b>{trim_name}</b>\n\n"
            f"ماده رنگ تریم را انتخاب کنید:",
            parse_mode="HTML",
            reply_markup=kb_material(s["material"]),
        )

    # ─── مرحله ۴: چرخش ماده ──────────────────────────────────────

    @dp.callback_query(F.data == "a_mat_cycle")
    async def mat_cycle(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("خطا", show_alert=True); return
        mat_list = list(TRIM_MATERIALS.keys())
        idx = mat_list.index(s["material"]) if s["material"] in mat_list else 0
        s["material"] = mat_list[(idx + 1) % len(mat_list)]
        await cb.message.edit_reply_markup(reply_markup=kb_material(s["material"]))
        await cb.answer(s["material"])

    @dp.callback_query(F.data == "a_to_enchant")
    async def to_enchant(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("دوباره از منو شروع کنید.", show_alert=True); return
        trim_name = ARMOR_TRIMS[s["trim"]]
        # پیش‌نمایش قبل از پیام مرحله
        await _send_preview(cb, s)
        await cb.message.edit_text(
            f"✨ <b>مرحله آخر — اینچنت</b>\n\n"
            f"آرمور: <b>{s['armor'].upper()}</b>  |  تریم: <b>{trim_name}</b>  |  ماده: <b>{s['material']}</b>\n\n"
            f"آیا می‌خواهید آرمور اینچنت باشد؟\n"
            f"(افکت بنفش درخشان روی تکسچر)",
            parse_mode="HTML",
            reply_markup=kb_enchant(s.get("enchanted", False)),
        )

    # ─── مرحله آخر: toggle اینچنت ────────────────────────────────
    @dp.message(
        (F.photo | F.document),
        lambda m: armor_build_state.get(m.from_user.id, {}).get("waiting_for_skin", False)
    )
    async def handle_skin_upload(message: types.Message):
        uid = message.from_user.id
        s = armor_build_state.get(uid)
        if not s or not s.get("waiting_for_skin", False):
            return

        try:
            if message.photo:
                photo = message.photo[-1]
                file = await bot.get_file(photo.file_id)
            else:
                file = await bot.get_file(message.document.file_id)

            downloaded = await bot.download_file(file.file_path)
            skin_bytes = downloaded.read()

            # چک کردن اندازه تقریبی اسکین
            img = Image.open(io.BytesIO(skin_bytes))
            if img.size not in [(64, 64), (64, 32)]:
                await message.answer("❌ فایل باید اسکین ماینکرافت (64x64 یا 64x32) باشد.")
                return

            user_skins[uid] = skin_bytes
            s["skin_enabled"] = True
            s["waiting_for_skin"] = False

            await message.answer("✅ اسکین با موفقیت بارگذاری شد!\nحالا ادامه دهید.", 
                               reply_markup=kb_trim(s["trim"]))  # یا مرحله بعدی
        except Exception as e:
            await message.answer(f"خطا در آپلود اسکین: {e}")
            
    @dp.callback_query(F.data == "a_enchant_toggle")
    async def enchant_toggle(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("خطا", show_alert=True); return
        s["enchanted"] = not s.get("enchanted", False)
        await cb.message.edit_reply_markup(reply_markup=kb_enchant(s["enchanted"]))
        status = "✨ روشن" if s["enchanted"] else "⬛ خاموش"
        await cb.answer(f"اینچنت: {status}")
        await _send_preview(cb, s)

    @dp.callback_query(F.data == "a_finish")
    async def finish(cb: types.CallbackQuery):
        uid = cb.from_user.id
        s = armor_build_state.get(uid)
        if not s:
            await cb.answer("دوباره از منو شروع کنید.", show_alert=True); return
        await _build_and_send(cb, s)

    # ─── ساخت و ارسال ─────────────────────────────────────────────

async def _build_and_send(cb: types.CallbackQuery, s: dict):
    uid = cb.from_user.id
    armor         = s["armor"]
    leather_color = s.get("leather_color", "none")
    trim_name     = ARMOR_TRIMS[s["trim"]]
    material      = s["material"]
    mat_color     = TRIM_MATERIALS.get(material, (255, 255, 255))
    enchanted     = s.get("enchanted", False)

    await cb.message.answer("⏳ در حال ساخت تکسچر آرمور...")

    if not PIL_AVAILABLE:
        await cb.message.answer("❌ Pillow نصب نیست...", parse_mode="HTML")
        armor_build_state.pop(uid, None)
        return

    # ==================== تولید دستور /give (Java 1.21+) ====================
    def generate_give_commands(armor: str, trim_name: str, material: str, leather_color: str):
        """
        فرمت صحیح Java 1.21+ با component syntax:
        /give @p minecraft:iron_chestplate[trim={pattern:"minecraft:silence",material:"minecraft:amethyst"}] 1
        برای چرم رنگی:
        /give @p minecraft:leather_chestplate[dyed_color={"rgb":DECIMAL}] 1
        یا ترکیبی از هر دو.
        """
        components_parts = []

        # Trim component
        if trim_name != "none":
            components_parts.append(
                f'trim={{pattern:"minecraft:{trim_name}",material:"minecraft:{material}"}}'
            )

        # Leather color component
        if armor == "leather":
            r, g, b = LEATHER_COLORS.get(leather_color, LEATHER_COLORS["none"])
            color_int = (r << 16) | (g << 8) | b
            components_parts.append(f'dyed_color={{"rgb":{color_int}}}')

        # آرایش نهایی component string
        if components_parts:
            comp_str = "[" + ",".join(components_parts) + "]"
        else:
            comp_str = ""

        commands = {}

        # Main Body: helmet + chestplate + boots (لایه humanoid)
        for piece in ["helmet", "chestplate", "boots"]:
            commands[piece] = f"/give @p minecraft:{armor}_{piece}{comp_str} 1"

        # Leggings: لایه humanoid_leggings
        commands["leggings"] = f"/give @p minecraft:{armor}_leggings{comp_str} 1"

        return commands

    # ==================== ساخت کپشن ====================
    lines = [f"🛡 <b>{armor.upper()}</b>"]
    if armor == "leather":
        fa = LEATHER_COLOR_NAMES_FA.get(leather_color, leather_color)
        emoji = LEATHER_COLOR_EMOJI.get(leather_color, "")
        lines.append(f"رنگ چرم: {emoji} {fa}")
    
    lines.append(f"تریم: <b>{trim_name}</b>")
    if trim_name != "none":
        lines.append(f"ماده: <b>{material}</b>")
    lines.append(f"اینچنت: {'✨ بله' if enchanted else '⬛ خیر'}")
    
    caption = "\n".join(lines)
    give_commands = generate_give_commands(armor, trim_name, material, leather_color)

    # ==================== ارسال فایل‌ها ====================
    sent = 0

    # ─── لایه ۱: Main Body (Helmet + Chestplate + Boots) ───
    data_main = build_layer(armor, leather_color, trim_name, mat_color, "humanoid", enchanted)
    if data_main:
        helmet_cmd    = give_commands.get("helmet", "")
        chest_cmd     = give_commands.get("chestplate", "")
        boots_cmd     = give_commands.get("boots", "")

        full_caption_main = (
            caption +
            f"\n\n🔵 <b>Layer 1 — Main Body (Helmet + Chestplate + Boots)</b>\n\n"
            f"<blockquote>"
            f"📋 <b>/give commands:</b>\n"
            f"<code>{helmet_cmd}</code>\n"
            f"<code>{chest_cmd}</code>\n"
            f"<code>{boots_cmd}</code>"
            f"</blockquote>"
        )

        await cb.message.answer_document(
            types.BufferedInputFile(data_main, filename=f"{armor}_humanoid.png"),
            caption=full_caption_main,
            parse_mode="HTML",
        )
        sent += 1

    # ─── لایه ۲: Leggings ───
    data_legs = build_layer(armor, leather_color, trim_name, mat_color, "humanoid_leggings", enchanted)
    if data_legs:
        leggings_cmd = give_commands.get("leggings", "")

        full_caption_legs = (
            caption +
            f"\n\n🟢 <b>Layer 2 — Leggings</b>\n\n"
            f"<blockquote>"
            f"📋 <b>/give:</b>\n"
            f"<code>{leggings_cmd}</code>"
            f"</blockquote>"
        )

        await cb.message.answer_document(
            types.BufferedInputFile(data_legs, filename=f"{armor}_humanoid_leggings.png"),
            caption=full_caption_legs,
            parse_mode="HTML",
        )
        sent += 1

    if sent:
        await cb.message.answer("✅ آرمور با موفقیت ساخته شد!")

    armor_build_state.pop(uid, None)
        # نکته: preview_msg_id داخل s بود و با pop شدن state پاک می‌شود؛
        # خود عکس پیش‌نمایش در چت باقی می‌ماند تا کاربر بتواند مقایسه کند.
