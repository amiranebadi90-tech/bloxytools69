# ====================== SEARCH & DOWNLOAD MOD / RESOURCE PACK ======================
# جستجوی مود / ریسورس‌پک / مپ توی Planet Minecraft, MCPEDL, PvPRP, Modrinth
# ساختار مثل link_download_handler.py هست: یک تابع register_mod_search_handlers(dp, bot, get_access_block_message)
#
# نکته‌ی مهم (حتما بخون):
# - Modrinth یک API عمومی و رسمی داره (api.modrinth.com) بنابراین جستجو، نسخه‌ها، لودرها (Fabric/Forge/Quilt/NeoForge)
#   و فایل دانلود دقیقاً و ۱۰۰٪ درست کار می‌کنن.
# - Planet Minecraft و MCPEDL API عمومی ندارن؛ اینجا با اسکرپ کردن HTML صفحه‌ی سرچشون کار می‌کنیم.
#   اگه این سایت‌ها ساختار HTML‌شون رو عوض کنن، ممکنه لازم بشه سلکتورهای پایین (تابع‌های search_planetminecraft
#   و search_mcpedl) رو آپدیت کنی.
# - PvPRP به نظر میاد صفحه‌ی سرچش با جاوااسکریپت رندر میشه و پشت Cloudflare هست؛ کد اسکرپش تلاش خودشو می‌کنه
#   ولی ممکنه نتیجه‌ای برنگردونه. اگه همیشه صفر نتیجه داد، یعنی نیاز به یک هدلس براوزر (Playwright) داره که
#   خارج از حوصله‌ی یک بات سبک تلگرامیه؛ فعلاً بی‌خیالش شو یا بعداً جدا اضافه‌ش کن.
# - برای PMC/MCPEDL/PvPRP لینک دانلود مستقیم فایل همیشه پیدا نمیشه (خیلی وقتا لینک واقعی پشت تبلیغ/صفحه‌ی
#   جاوااسکریپتیه). اگه پیدا نشد، بات به جای خطا دادن، لینک صفحه رو میده که کاربر خودش دستی دانلود کنه.
#
# نصب پیش‌نیاز:
#   pip install beautifulsoup4 aiohttp

import asyncio
import difflib
import os
import re
from urllib.parse import quote, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from aiogram import F, types
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from link_download_handler import (
    human_size,
    safe_filename,
    DOWNLOAD_DIR,
    MAX_DOWNLOAD_SIZE,
    CHUNK_SIZE,
)

# ====================== CONFIG ======================
PAGE_SIZE = 6
CHECKMARK_DELAY = 1.1  # ثانیه، فاصله‌ی بین زدن تیک سبز و رفتن به مرحله‌ی بعد

SEARCH_TIMEOUT = aiohttp.ClientTimeout(total=15)
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
MODRINTH_HEADERS = {"User-Agent": "BloxyDesignBot/1.0 (contact: t.me/BloxyDesign)"}

FILE_EXT_PATTERN = re.compile(
    r'href=["\']([^"\']+\.(?:zip|mcpack|mcaddon|mcworld|jar|rar|7z))["\']', re.IGNORECASE
)

# نگهداری موقت state هر کاربر (حافظه، نه دیتابیس)
search_sessions = {}     # user_id -> {"query":..., "results":[...], "page": int}
selection_sessions = {}  # user_id -> {"result":..., "mc_version":..., "loader":..., "versions_data":[...], ...}


# ====================== STATE ======================
class ModSearchState(StatesGroup):
    waiting_query = State()
    waiting_manual_mcver = State()


MCVER_PAGE_SIZE = 9  # تعداد نسخه‌ی ماینکرافت در هر صفحه (۳ ستون x ۳ ردیف)


# ====================== FUZZY SEARCH ======================
NOISE_WORDS = {
    "mod", "mods", "addon", "addons", "pack", "packs", "texture", "textures",
    "resource", "resourcepack", "resource-pack", "for", "minecraft", "mcpe",
    "java", "bedrock", "edition", "the", "a", "an", "v", "version", "official",
}


def _normalize_tokens(text: str):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\u0600-\u06FF\s]", " ", text)
    return [t for t in text.split() if t not in NOISE_WORDS and len(t) > 1]


def fuzzy_score(query: str, title: str) -> float:
    """امتیاز شباهت بین عبارت جستجو و عنوان نتیجه.
    ترکیبی از overlap توکن‌ها + شباهت رشته‌ای، تا با تغییر جزئی اسم و ترتیب کلمات گیج نشه."""
    q_tokens = _normalize_tokens(query)
    t_tokens = _normalize_tokens(title)
    if not q_tokens or not t_tokens:
        return 0.0
    q_set, t_set = set(q_tokens), set(t_tokens)
    overlap = len(q_set & t_set) / len(q_set)
    ratio = difflib.SequenceMatcher(None, " ".join(q_tokens), " ".join(t_tokens)).ratio()
    contains_bonus = 0.15 if " ".join(q_tokens) in " ".join(t_tokens) else 0.0
    return round(0.5 * overlap + 0.4 * ratio + contains_bonus, 4)


# ====================== SITE SEARCHERS ======================
async def search_modrinth(session: aiohttp.ClientSession, query: str, limit: int = 15):
    try:
        async with session.get(
            "https://api.modrinth.com/v2/search",
            params={"query": query, "limit": str(limit)},
            headers=MODRINTH_HEADERS,
            timeout=SEARCH_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except Exception:
        return []

    results = []
    for hit in data.get("hits", []):
        results.append({
            "source": "Modrinth",
            "emoji": "🟩",
            "title": hit.get("title", "بی‌نام"),
            "url": f"https://modrinth.com/{hit.get('project_type', 'project')}/{hit.get('slug', hit.get('project_id'))}",
            "project_id": hit.get("project_id"),
            "project_type": hit.get("project_type"),
        })
    return results


async def search_planetminecraft(session: aiohttp.ClientSession, query: str, limit: int = 15):
    url = f"https://www.planetminecraft.com/resources/?keywords={quote(query)}"
    try:
        async with session.get(url, headers=BROWSER_HEADERS, timeout=SEARCH_TIMEOUT) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results, seen = [], set()
    category_pattern = re.compile(r"^/(mods|texture-packs|data-packs|maps|skins|mob-skins|banners)/([\w\-]+)/?$")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        path = urlparse(href).path if href.startswith("http") else href
        m = category_pattern.match(path)
        if not m:
            continue
        title = (a.get("title") or a.get_text(strip=True)).strip()
        if not title or len(title) < 3:
            continue
        full_url = href if href.startswith("http") else urljoin("https://www.planetminecraft.com", href)
        if full_url in seen:
            continue
        seen.add(full_url)
        results.append({
            "source": "Planet Minecraft",
            "emoji": "🌍",
            "title": title,
            "url": full_url,
            "project_type": m.group(1),
        })
        if len(results) >= limit:
            break
    return results


async def search_mcpedl(session: aiohttp.ClientSession, query: str, limit: int = 15):
    url = f"https://mcpedl.com/?s={quote(query)}"
    try:
        async with session.get(url, headers=BROWSER_HEADERS, timeout=SEARCH_TIMEOUT) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results, seen = [], set()
    candidates = soup.select(
        "article h2 a, article h1 a, h2.entry-title a, h3.entry-title a, .post-title a"
    )
    for a in candidates:
        title = a.get_text(strip=True)
        href = a.get("href")
        if not title or not href or href in seen:
            continue
        seen.add(href)
        results.append({
            "source": "MCPEDL",
            "emoji": "🟦",
            "title": title,
            "url": href,
            "project_type": "addon",
        })
        if len(results) >= limit:
            break
    return results


async def search_pvprp(session: aiohttp.ClientSession, query: str, limit: int = 15):
    url = f"https://pvprp.com/search?q={quote(query)}"
    try:
        async with session.get(url, headers=BROWSER_HEADERS, timeout=SEARCH_TIMEOUT) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"/pack/|texture-pack", href):
            continue
        title = (a.get("title") or a.get_text(strip=True)).strip()
        if not title or len(title) < 2:
            continue
        full_url = href if href.startswith("http") else urljoin("https://pvprp.com", href)
        if full_url in seen:
            continue
        seen.add(full_url)
        results.append({
            "source": "PvPRP",
            "emoji": "⚔️",
            "title": title,
            "url": full_url,
            "project_type": "resourcepack",
        })
        if len(results) >= limit:
            break
    return results


async def search_all_sources(query: str):
    async with aiohttp.ClientSession() as session:
        results_per_site = await asyncio.gather(
            search_modrinth(session, query),
            search_planetminecraft(session, query),
            search_mcpedl(session, query),
            search_pvprp(session, query),
            return_exceptions=True,
        )

    all_results = []
    for r in results_per_site:
        if isinstance(r, list):
            all_results.extend(r)

    for item in all_results:
        item["score"] = fuzzy_score(query, item["title"])
    all_results.sort(key=lambda x: x["score"], reverse=True)
    return all_results


# ====================== KEYBOARD HELPERS ======================
def build_results_keyboard(results, page: int):
    start = page * PAGE_SIZE
    chunk = results[start:start + PAGE_SIZE]
    rows = []
    for i, item in enumerate(chunk, start=start):
        label = f"{item['emoji']} {item['title'][:45]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"msr_pick:{i}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"msr_page:{page - 1}"))
    if start + PAGE_SIZE < len(results):
        nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"msr_page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="❌ لغو", callback_data="msr_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def check_then(callback: types.CallbackQuery, advance_coro, delay: float = CHECKMARK_DELAY):
    """کنار دکمه‌ای که کاربر زده یک ✅ می‌ذاره، چند ثانیه صبر می‌کنه و بعد مرحله‌ی بعد رو اجرا می‌کنه."""
    kb = callback.message.reply_markup
    if kb:
        new_rows = []
        for row in kb.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data == callback.data and not btn.text.startswith("✅"):
                    new_row.append(InlineKeyboardButton(text=f"✅ {btn.text}", callback_data=btn.callback_data))
                else:
                    new_row.append(btn)
            new_rows.append(new_row)
        try:
            await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows))
        except Exception:
            pass

    await callback.answer()
    await asyncio.sleep(delay)
    await advance_coro()


# ====================== DOWNLOAD HELPER (مشترک) ======================
async def _download_and_send(bot, chat_id, status_message, url, filename, extra_caption=""):
    temp_path = os.path.join(DOWNLOAD_DIR, f"msr_{chat_id}_{safe_filename(filename)}")
    try:
        await status_message.edit_text(f"⬇️ در حال دانلود <code>{filename}</code> ...", parse_mode="HTML")
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=300)
        async with aiohttp.ClientSession(timeout=timeout, headers=BROWSER_HEADERS) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"سرور با کد خطای {resp.status} پاسخ داد")
                downloaded = 0
                with open(temp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_SIZE:
                            raise RuntimeError("حجم فایل هنگام دانلود از حد مجاز عبور کرد")

        await status_message.edit_text(f"⬆️ در حال آپلود <code>{filename}</code> ...", parse_mode="HTML")
        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(temp_path, filename=filename),
            caption=f"✅ <b>{filename}</b>{extra_caption}",
            parse_mode="HTML",
        )
        try:
            await status_message.delete()
        except Exception:
            pass

    except Exception as e:
        err = str(e).lower()
        if "too large" in err or "entity too large" in err or "file is too big" in err:
            await status_message.edit_text(
                "❌ حجم فایل برای آپلود در تلگرام زیاده.\n"
                f"محدودیت فعلی: {human_size(MAX_DOWNLOAD_SIZE)}"
            )
        else:
            await status_message.edit_text(
                f"❌ خطا در دانلود/آپلود فایل:\n<code>{str(e)[:300]}</code>", parse_mode="HTML"
            )
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


# ====================== REGISTER ======================
def register_mod_search_handlers(dp, bot, get_access_block_message):

    # ---------- شروع جستجو ----------
    @dp.message(F.text == "🔎 جستجوی مود/ریسورس‌پک")
    async def start_mod_search(message: types.Message, state: FSMContext):
        block_msg = get_access_block_message(message.from_user.id)
        if block_msg:
            await message.answer(block_msg)
            return

        await state.set_state(ModSearchState.waiting_query)
        await message.answer(
            "🔎 <b>جستجوی مود، ریسورس‌پک، مپ و ...</b>\n\n"
            "اسم چیزی که دنبالشی رو بفرست تا هم‌زمان توی:\n"
            "🟩 Modrinth   🌍 Planet Minecraft   🟦 MCPEDL   ⚔️ PvPRP\n"
            "سرچ کنم و بهترین نتیجه‌ها رو بیارم.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ لغو", callback_data="msr_cancel")
            ]])
        )

    @dp.message(ModSearchState.waiting_query, F.text)
    async def receive_query(message: types.Message, state: FSMContext):
        query = message.text.strip()
        if len(query) < 2:
            await message.answer("❌ حداقل ۲ حرف بفرست.")
            return

        searching_msg = await message.answer("🔎 در حال جستجو توی ۴ سایت... چند ثانیه صبر کن.")
        results = await search_all_sources(query)
        await state.clear()

        if not results:
            await searching_msg.edit_text(
                "❌ چیزی پیدا نشد.\n"
                "یه اسم دیگه یا کلمه‌ی ساده‌تر امتحان کن (مثلاً به‌جای اسم کامل، فقط بخشی از اسم مود)."
            )
            return

        search_sessions[message.from_user.id] = {"query": query, "results": results, "page": 0}
        await searching_msg.edit_text(
            f"✅ {len(results)} نتیجه برای «{query}» پیدا شد:\n"
            "روی هرکدوم بزنی، تیک سبز می‌خوره و میریم مرحله‌ی بعد 👇",
            reply_markup=build_results_keyboard(results, 0)
        )

    # ---------- صفحه‌بندی نتایج ----------
    @dp.callback_query(F.data.startswith("msr_page:"))
    async def page_nav(callback: types.CallbackQuery, state: FSMContext):
        sess = search_sessions.get(callback.from_user.id)
        if not sess:
            await callback.answer("⏳ نشست منقضی شده، دوباره جستجو کن.", show_alert=True)
            return
        page = int(callback.data.split(":")[1])
        sess["page"] = page
        await callback.message.edit_reply_markup(reply_markup=build_results_keyboard(sess["results"], page))
        await callback.answer()

    # ---------- انتخاب یک نتیجه ----------
    @dp.callback_query(F.data.startswith("msr_pick:"))
    async def pick_result(callback: types.CallbackQuery, state: FSMContext):
        user_id = callback.from_user.id
        sess = search_sessions.get(user_id)
        if not sess:
            await callback.answer("⏳ نشست منقضی شده، دوباره جستجو کن.", show_alert=True)
            return

        idx = int(callback.data.split(":")[1])
        item = sess["results"][idx]
        selection_sessions[user_id] = {"result": item}

        async def advance():
            if item["source"] == "Modrinth":
                await ask_mc_version_modrinth(callback, item)
            else:
                await ask_generic_confirm(callback, item)

        await check_then(callback, advance)

    # ---------- مسیر Modrinth: انتخاب نسخه‌ی ماینکرافت ----------
    def build_mcver_keyboard(game_versions, page: int):
        start = page * MCVER_PAGE_SIZE
        chunk = game_versions[start:start + MCVER_PAGE_SIZE]
        rows = []
        for i in range(0, len(chunk), 3):
            rows.append([
                InlineKeyboardButton(text=gv, callback_data=f"msr_mcver:{gv}")
                for gv in chunk[i:i + 3]
            ])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"msr_mcver_page:{page - 1}"))
        if start + MCVER_PAGE_SIZE < len(game_versions):
            nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"msr_mcver_page:{page + 1}"))
        if nav:
            rows.append(nav)

        rows.append([InlineKeyboardButton(text="✏️ نوشتن دستی نسخه", callback_data="msr_mcver_manual")])
        rows.append([InlineKeyboardButton(text="❌ لغو", callback_data="msr_cancel")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def ask_mc_version_modrinth(callback: types.CallbackQuery, item):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.modrinth.com/v2/project/{item['project_id']}/version",
                    headers=MODRINTH_HEADERS,
                    timeout=SEARCH_TIMEOUT,
                ) as resp:
                    versions_data = await resp.json() if resp.status == 200 else []
        except Exception:
            versions_data = []

        game_versions = []
        for v in versions_data:
            for gv in v.get("game_versions", []):
                if gv not in game_versions:
                    game_versions.append(gv)

        sess = selection_sessions[callback.from_user.id]
        sess["versions_data"] = versions_data
        sess["game_versions"] = game_versions
        sess["mcver_page"] = 0

        if not game_versions:
            await callback.message.edit_text("❌ نسخه‌ای برای این آیتم پیدا نشد؛ شاید پروژه دیگه فایلی نداره.")
            return

        await callback.message.edit_text(
            f"📦 <b>{item['title']}</b>\n🟩 Modrinth\n\nنسخه‌ی ماینکرافت رو انتخاب کن:",
            parse_mode="HTML",
            reply_markup=build_mcver_keyboard(game_versions, 0),
        )

    @dp.callback_query(F.data.startswith("msr_mcver_page:"))
    async def mcver_page_nav(callback: types.CallbackQuery, state: FSMContext):
        sess = selection_sessions.get(callback.from_user.id)
        if not sess or "game_versions" not in sess:
            await callback.answer("⏳ نشست منقضی شده.", show_alert=True)
            return
        page = int(callback.data.split(":")[1])
        sess["mcver_page"] = page
        await callback.message.edit_reply_markup(reply_markup=build_mcver_keyboard(sess["game_versions"], page))
        await callback.answer()

    @dp.callback_query(F.data == "msr_mcver_manual")
    async def mcver_manual_start(callback: types.CallbackQuery, state: FSMContext):
        sess = selection_sessions.get(callback.from_user.id)
        if not sess:
            await callback.answer("⏳ نشست منقضی شده.", show_alert=True)
            return

        async def advance():
            await state.set_state(ModSearchState.waiting_manual_mcver)
            await callback.message.edit_text(
                "✏️ نسخه‌ی ماینکرافت رو دقیق بنویس، مثلاً:\n<code>1.20.1</code>",
                parse_mode="HTML",
            )

        await check_then(callback, advance)

    @dp.message(ModSearchState.waiting_manual_mcver, F.text)
    async def mcver_manual_receive(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        sess = selection_sessions.get(user_id)
        if not sess or "game_versions" not in sess:
            await state.clear()
            await message.answer("❌ نشست منقضی شده، دوباره جستجو کن.")
            return

        typed = message.text.strip()
        game_versions = sess["game_versions"]

        mc_version = None
        for gv in game_versions:
            if gv.lower() == typed.lower():
                mc_version = gv
                break

        if not mc_version:
            close = difflib.get_close_matches(typed, game_versions, n=1, cutoff=0.6)
            if close:
                mc_version = close[0]

        if not mc_version:
            await message.answer(
                f"❌ نسخه‌ی <code>{typed}</code> برای این آیتم پیدا نشد.\n"
                "دوباره امتحان کن یا از لیست دکمه‌ها انتخاب کن.",
                parse_mode="HTML",
            )
            return

        await state.clear()
        sess["mc_version"] = mc_version
        confirm_msg = await message.answer(f"✅ نسخه‌ی <code>{mc_version}</code> انتخاب شد.", parse_mode="HTML")
        await asyncio.sleep(CHECKMARK_DELAY)
        await ask_loader_message(confirm_msg, user_id)

    async def ask_loader_message(message: types.Message, user_id: int):
        """مثل ask_loader ولی روی یک Message عادی کار می‌کنه (برای وقتی ورودی از تایپ دستی میاد، نه callback)."""
        sess = selection_sessions[user_id]
        mc_version = sess["mc_version"]
        loaders = set()
        for v in sess["versions_data"]:
            if mc_version in v.get("game_versions", []):
                loaders.update(v.get("loaders", []))

        if not loaders:
            await message.edit_text("❌ برای این نسخه لودری پیدا نشد.")
            return

        rows = [[InlineKeyboardButton(text=l.capitalize(), callback_data=f"msr_loader:{l}")] for l in sorted(loaders)]
        rows.append([InlineKeyboardButton(text="❌ لغو", callback_data="msr_cancel")])
        await message.edit_text(
            f"⚙️ لودر رو انتخاب کن (نسخه‌ی {mc_version}):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @dp.callback_query(F.data.startswith("msr_mcver:"))
    async def pick_mcver(callback: types.CallbackQuery, state: FSMContext):
        mc_version = callback.data.split(":", 1)[1]
        sess = selection_sessions.get(callback.from_user.id)
        if not sess:
            await callback.answer("⏳ نشست منقضی شده.", show_alert=True)
            return
        sess["mc_version"] = mc_version

        async def advance():
            await ask_loader(callback)

        await check_then(callback, advance)


    async def ask_loader(callback: types.CallbackQuery):
        sess = selection_sessions[callback.from_user.id]
        mc_version = sess["mc_version"]
        loaders = set()
        for v in sess["versions_data"]:
            if mc_version in v.get("game_versions", []):
                loaders.update(v.get("loaders", []))

        if not loaders:
            await callback.message.edit_text("❌ برای این نسخه لودری پیدا نشد.")
            return

        rows = [[InlineKeyboardButton(text=l.capitalize(), callback_data=f"msr_loader:{l}")] for l in sorted(loaders)]
        rows.append([InlineKeyboardButton(text="❌ لغو", callback_data="msr_cancel")])
        await callback.message.edit_text(
            f"⚙️ لودر رو انتخاب کن (نسخه‌ی {mc_version}):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @dp.callback_query(F.data.startswith("msr_loader:"))
    async def pick_loader(callback: types.CallbackQuery, state: FSMContext):
        loader = callback.data.split(":", 1)[1]
        sess = selection_sessions.get(callback.from_user.id)
        if not sess:
            await callback.answer("⏳ نشست منقضی شده.", show_alert=True)
            return
        sess["loader"] = loader

        async def advance():
            await ask_mod_version(callback)

        await check_then(callback, advance)

    async def ask_mod_version(callback: types.CallbackQuery):
        sess = selection_sessions[callback.from_user.id]
        mc_version, loader = sess["mc_version"], sess["loader"]
        matches = [
            v for v in sess["versions_data"]
            if mc_version in v.get("game_versions", []) and loader in v.get("loaders", [])
        ]
        if not matches:
            await callback.message.edit_text("❌ فایلی با این ترکیب نسخه/لودر پیدا نشد.")
            return

        sess["matches"] = matches
        rows = []
        for i, v in enumerate(matches[:10]):
            label = v.get("version_number") or v.get("name") or f"نسخه {i + 1}"
            rows.append([InlineKeyboardButton(text=f"📄 {label}", callback_data=f"msr_ver:{i}")])
        rows.append([InlineKeyboardButton(text="❌ لغو", callback_data="msr_cancel")])

        await callback.message.edit_text(
            f"🧩 ورژن مود رو انتخاب کن ({mc_version} | {loader}):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @dp.callback_query(F.data.startswith("msr_ver:"))
    async def pick_version(callback: types.CallbackQuery, state: FSMContext):
        idx = int(callback.data.split(":", 1)[1])
        sess = selection_sessions.get(callback.from_user.id)
        if not sess:
            await callback.answer("⏳ نشست منقضی شده.", show_alert=True)
            return
        sess["chosen_version_idx"] = idx

        async def advance():
            await download_modrinth_file(callback)

        await check_then(callback, advance)

    async def download_modrinth_file(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        sess = selection_sessions.get(user_id)
        if not sess:
            await callback.message.edit_text("❌ نشست منقضی شده، دوباره جستجو کن.")
            return

        block_msg = get_access_block_message(user_id)
        if block_msg:
            await callback.message.edit_text(block_msg)
            return

        version = sess["matches"][sess["chosen_version_idx"]]
        files = version.get("files", [])
        primary = next((f for f in files if f.get("primary")), files[0] if files else None)
        if not primary:
            await callback.message.edit_text("❌ فایلی برای دانلود پیدا نشد.")
            return

        await _download_and_send(
            bot, callback.message.chat.id, callback.message,
            primary["url"], primary["filename"],
            extra_caption=f"\n📦 {human_size(primary.get('size'))} | 🟩 Modrinth",
        )
        selection_sessions.pop(user_id, None)

    # ---------- مسیر PMC / MCPEDL / PvPRP (اسکرپ) ----------
    async def ask_generic_confirm(callback: types.CallbackQuery, item):
        user_id = callback.from_user.id
        selection_sessions[user_id] = {"result": item}
        text = (
            f"{item['emoji']} <b>{item['title']}</b>\n"
            f"🌐 منبع: {item['source']}\n"
            f"🔗 <a href=\"{item['url']}\">صفحه‌ی ریسورس</a>\n\n"
            "برای دانلود، دکمه‌ی زیر رو بزن. اگه این سایت لینک مستقیم رو پشت تبلیغ یا "
            "صفحه‌ی جاوااسکریپتی مخفی کرده باشه، به‌جاش همون لینک صفحه رو بهت میدم تا خودت "
            "دستی دانلودش کنی."
        )
        rows = [
            [InlineKeyboardButton(text="📥 پیدا کردن و دانلود فایل", callback_data="msr_gen_dl")],
            [InlineKeyboardButton(text="❌ لغو", callback_data="msr_cancel")],
        ]
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            disable_web_page_preview=True,
        )

    @dp.callback_query(F.data == "msr_gen_dl")
    async def generic_download(callback: types.CallbackQuery, state: FSMContext):
        async def advance():
            user_id = callback.from_user.id
            sess = selection_sessions.get(user_id)
            if not sess:
                await callback.message.edit_text("❌ نشست منقضی شده، دوباره جستجو کن.")
                return

            block_msg = get_access_block_message(user_id)
            if block_msg:
                await callback.message.edit_text(block_msg)
                return

            item = sess["result"]
            await callback.message.edit_text("🔎 در حال پیدا کردن لینک مستقیم فایل...", parse_mode="HTML")

            try:
                async with aiohttp.ClientSession(headers=BROWSER_HEADERS, timeout=SEARCH_TIMEOUT) as session:
                    async with session.get(item["url"]) as resp:
                        html = await resp.text()
            except Exception as e:
                await callback.message.edit_text(
                    f"❌ خطا در باز کردن صفحه:\n<code>{str(e)[:200]}</code>", parse_mode="HTML"
                )
                return

            direct_url = None
            for m in FILE_EXT_PATTERN.findall(html):
                if any(skip in m.lower() for skip in ("logo", "icon", "favicon")):
                    continue
                direct_url = m if m.startswith("http") else urljoin(item["url"], m)
                break

            if not direct_url:
                await callback.message.edit_text(
                    "⚠️ نتونستم لینک مستقیم فایل رو پیدا کنم؛ این سایت معمولاً لینک واقعی رو پشت "
                    "صفحه‌ی دانلود یا تبلیغ مخفی می‌کنه.\n\n"
                    f"می‌تونی خودت از اینجا دانلود کنی:\n{item['url']}",
                    disable_web_page_preview=True,
                )
                selection_sessions.pop(user_id, None)
                return

            filename = safe_filename(os.path.basename(urlparse(direct_url).path) or f"{item['title']}.zip")
            await _download_and_send(
                bot, callback.message.chat.id, callback.message,
                direct_url, filename, extra_caption=f"\n🌐 {item['source']}",
            )
            selection_sessions.pop(user_id, None)

        await check_then(callback, advance)

    # ---------- لغو ----------
    @dp.callback_query(F.data == "msr_cancel")
    async def cancel_search(callback: types.CallbackQuery, state: FSMContext):
        search_sessions.pop(callback.from_user.id, None)
        selection_sessions.pop(callback.from_user.id, None)
        await state.clear()
        try:
            await callback.message.edit_text("❌ جستجو لغو شد.")
        except Exception:
            pass
        await callback.answer()
