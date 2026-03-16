#!/usr/bin/env python3
"""
Yupoo Downloader Bot v3
pip install python-telegram-bot requests beautifulsoup4
"""

import os, re, io, sys, time, logging, zipfile
from datetime import datetime
from urllib.parse import urlparse, urljoin

try:
    import requests
    from bs4 import BeautifulSoup
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from telegram.constants import ParseMode
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
        "python-telegram-bot", "requests", "beautifulsoup4"])
    import requests
    from bs4 import BeautifulSoup
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from telegram.constants import ParseMode

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("YUPOO_BOT_TOKEN", "8562968989:AAFayidjXx9QRRuYraIITZdtj0RLkeEMrVQ")
TG_LIMIT  = 50 * 1024 * 1024


# ═══════════════════════════════════════════════
#  DOWNLOADER CORE
# ═══════════════════════════════════════════════
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
    "Referer": "https://www.yupoo.com/",
}
API_REST  = "https://www.yupoo.com/api/rest/"
PHOTO_CDN = "https://photo.yupoo.com"
VIDEO_CDN = "https://video.yupoo.com"

def get_session():
    s = requests.Session(); s.headers.update(HEADERS); return s

def fetch_url(session, url, retries=3, params=None, as_json=False):
    for attempt in range(retries):
        try:
            h = dict(session.headers)
            if as_json:
                h["Accept"] = "application/json, text/plain, */*"
                h["X-Requested-With"] = "XMLHttpRequest"
            r = session.get(url, headers=h, params=params, timeout=20)
            r.raise_for_status(); return r
        except requests.RequestException:
            if attempt < retries - 1: time.sleep(2)
            else: raise

def extract_api_key(html):
    for pat in [r"[,{]apiKey\s*:\s*['\"]([a-f0-9]+)['\"]", r"api_key['\"]?\s*[:=]\s*['\"]([a-f0-9]+)['\"]"]:
        m = re.search(pat, html, re.IGNORECASE)
        if m: return m.group(1)
    return ""

def yupoo_api(session, method, params):
    p = {"method": method, "format": "json", "nojsoncallback": "1", **params}
    return fetch_url(session, API_REST, params=p, as_json=True).json()

def build_photo_url(ph):
    b, k, s = ph.get("bucket",""), ph.get("key",""), ph.get("secret","")
    fmt = ph.get("originalformat") or ph.get("format") or "jpg"
    if b and k and s: return f"{PHOTO_CDN}/{b}/{k}/{s}.{fmt}"
    p = ph.get("path") or ph.get("src") or ph.get("url") or ""
    if p.startswith("http"): return p
    if p.startswith("//"): return "https:"+p
    return f"{PHOTO_CDN}/{p.lstrip('/')}" if p else ""

def build_video_url(ph):
    v = ph.get("video") or {}
    if isinstance(v, dict):
        for k in ("mp4","url","src"):
            u = v.get(k,"")
            if u: return u if u.startswith("http") else "https:"+u
    b, k, s = ph.get("bucket",""), ph.get("key",""), ph.get("secret","")
    if b and k and s: return f"{VIDEO_CDN}/{b}/{k}/{s}.mp4"
    return ""

def get_ext(url, mtype="image"):
    if mtype == "video":
        m = re.search(r"\.(mp4|mov|avi|webm)(\?|$)", url, re.I)
        return "."+m.group(1).lower() if m else ".mp4"
    m = re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.I)
    return "."+m.group(1).lower() if m else ".jpg"

def fetch_via_api(session, album_url):
    html = fetch_url(session, album_url).text
    api_key = extract_api_key(html)
    if not api_key: return []
    m = re.search(r"/albums/(\d+)", album_url)
    if not m: return []
    album_id = m.group(1)
    media, page = [], 1
    while True:
        data = yupoo_api(session, "yupoo.albums.getPhotos",
                         {"api_key": api_key, "album_id": album_id, "page": page, "per_page": 100})
        if data.get("stat") != "ok": break
        pobj = data.get("photos", {})
        plist = pobj.get("photo", [])
        npages = int(pobj.get("pages", 1))
        for ph in plist:
            pid = ph.get("id") or ph.get("photo_id") or ""
            try:
                info = yupoo_api(session, "yupoo.photos.getInfo", {"api_key": api_key, "photo_id": pid})
                if info.get("stat") == "ok":
                    pdata = info.get("photo", {})
                    if pdata.get("media") == "video":
                        vurl = build_video_url(pdata) or re.sub(r"\.\w+$", ".mp4", build_photo_url(pdata))
                        if vurl: media.append({"url": vurl, "type": "video"})
                    else:
                        u = build_photo_url(pdata)
                        if u: media.append({"url": u, "type": "image"})
                else:
                    u = build_photo_url(ph)
                    if u: media.append({"url": u, "type": "image"})
            except Exception:
                u = build_photo_url(ph)
                if u: media.append({"url": u, "type": "image"})
            time.sleep(0.04)
        if page >= npages: break
        page += 1; time.sleep(0.3)
    return media

def fetch_photo_video_url(session, base_url, photo_id):
    try:
        soup = BeautifulSoup(fetch_url(session, f"{base_url}/photos/{photo_id}").text, "html.parser")
        for el in soup.find_all(["video","source"]):
            u = el.get("src") or el.get("data-src") or ""
            if u and "undefined" not in u:
                if not u.startswith("http"): u = "https:"+u if u.startswith("//") else base_url+u
                return u
        for script in soup.find_all("script"):
            for pat in [r'"videoUrl"\s*:\s*"([^"]+)"', r'"mp4"\s*:\s*"([^"]+)"']:
                m = re.search(pat, script.string or "")
                if m:
                    u = m.group(1).replace("\\/","/")
                    if "undefined" not in u: return u
    except Exception: pass
    return ""

def fetch_via_html(session, album_url):
    parsed = urlparse(album_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    resp = fetch_url(session, album_url)
    soup = BeautifulSoup(resp.text, "html.parser")
    pages = [album_url]; seen_p = {album_url}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"[?&]page=\d+", href):
            full = urljoin(base_url, href) if not href.startswith("http") else href
            clean = re.sub(r"[&?]_=\d+", "", full)
            if clean not in seen_p: seen_p.add(clean); pages.append(full)
    media = []
    for i, purl in enumerate(pages, 1):
        if i > 1: soup = BeautifulSoup(fetch_url(session, purl).text, "html.parser")
        for img in soup.find_all("img", attrs={"data-origin-src": True}):
            u = img["data-origin-src"].strip()
            if not u: continue
            if not u.startswith("http"): u = urljoin(base_url, u)
            media.append({"url": u.split("?")[0], "type": "image"})
        for container in soup.find_all(attrs={"data-type": "video"}):
            pid = container.get("data-id") or container.get("data-photo-id") or ""
            if pid:
                vurl = fetch_photo_video_url(session, base_url, pid)
                if vurl: media.append({"url": vurl, "type": "video"})
        for el in soup.find_all(["video","source"]):
            u = el.get("src") or el.get("data-src") or ""
            if not u or "undefined" in u: continue
            if not u.startswith("http"): u = "https:"+u if u.startswith("//") else urljoin(base_url, u)
            media.append({"url": u, "type": "video"})
        if i < len(pages): time.sleep(0.5)
    return media

def collect_media(album_url):
    session = get_session()
    try:
        media = fetch_via_api(session, album_url)
        if media: return media
    except Exception: pass
    try: return fetch_via_html(session, album_url)
    except Exception: return []

def download_bytes(session, url, mtype):
    try:
        r = session.get(url, timeout=180 if mtype=="video" else 30, stream=True)
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type",""): return None
        data = b""
        for chunk in r.iter_content(65536): data += chunk
        return data
    except Exception: return None


# ═══════════════════════════════════════════════
#  STATO UTENTI
# ═══════════════════════════════════════════════
sessions: dict[int, dict] = {}

def get_user(uid: int) -> dict:
    if uid not in sessions:
        sessions[uid] = {
            "url": None,
            "history": [],        # lista di url scaricati
            "total_albums": 0,
            "total_files": 0,
            "total_mb": 0.0,
            "started": datetime.now(),
        }
    return sessions[uid]


# ═══════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════
def kb_mode():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🖼 Album Telegram", callback_data="mode_album"),
        InlineKeyboardButton("🗜 ZIP",            callback_data="mode_zip"),
    ]])

def kb_after_download():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Scarica un altro album",  callback_data="new_download")],
        [
            InlineKeyboardButton("📊 Le mie statistiche", callback_data="show_stats"),
            InlineKeyboardButton("📋 Cronologia",         callback_data="show_history"),
        ],
    ])

def kb_back():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Torna al menu", callback_data="new_download"),
    ]])


# ═══════════════════════════════════════════════
#  HELPERS DI TESTO
# ═══════════════════════════════════════════════
def fmt_mb(b: float) -> str:
    if b < 1: return f"{b*1024:.0f} KB"
    return f"{b:.1f} MB"

def progress_bar(current: int, total: int, width: int = 12) -> str:
    filled = int(width * current / total) if total else 0
    return "█" * filled + "░" * (width - filled)


# ═══════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.effective_user.first_name or "ciao"
    get_user(uid)
    await update.message.reply_text(
        f"👋 *Ciao {name}!*\n\n"
        "Sono il tuo downloader per album Yupoo.\n"
        "Mandami un link e ti scarico tutto — foto e video.\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📌 *Comandi disponibili*\n"
        "/start — questo messaggio\n"
        "/help  — come funziono\n"
        "/stats — le tue statistiche\n"
        "/history — ultimi album scaricati\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "👇 *Inizia mandando un link album Yupoo*",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Come funziono*\n\n"
        "1️⃣ Mandami il link di un album Yupoo\n"
        "   es: `https://seller.x.yupoo.com/albums/123456`\n\n"
        "2️⃣ Scegli la modalità di invio:\n"
        "   🖼 *Album Telegram* — foto in anteprima, a gruppi di 10, le vedi subito nella chat\n"
        "   🗜 *ZIP* — tutto compresso in un file, comodo per salvare o condividere\n\n"
        "3️⃣ Aspetta che finisco e ricevi tutto!\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "⚠️ *Limiti Telegram*: max 50 MB per file\n"
        "Se lo ZIP è più grande lo spezzerò in parti automaticamente.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    data = get_user(uid)
    mins = int((datetime.now() - data["started"]).total_seconds() / 60)
    await update.message.reply_text(
        "📊 *Le tue statistiche*\n\n"
        f"📦 Album scaricati:  `{data['total_albums']}`\n"
        f"🖼 File ricevuti:    `{data['total_files']}`\n"
        f"💾 Dati scaricati:  `{fmt_mb(data['total_mb'])}`\n"
        f"⏱ Sessione attiva: `{mins} min`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back(),
    )

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    data    = get_user(uid)
    history = data.get("history", [])
    if not history:
        await update.message.reply_text("📋 Non hai ancora scaricato nessun album.", reply_markup=kb_back())
        return
    lines = "\n".join(f"`{i+1}.` {url}" for i, url in enumerate(history[-10:]))
    await update.message.reply_text(
        f"📋 *Ultimi album scaricati*\n\n{lines}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_back(),
    )


# ═══════════════════════════════════════════════
#  HANDLER MESSAGGI (link)
# ═══════════════════════════════════════════════
async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    uid  = update.effective_user.id

    if "yupoo.com" not in text or "/albums/" not in text:
        await update.message.reply_text(
            "❌ Link non valido.\n\n"
            "Deve essere tipo:\n"
            "`https://seller.x.yupoo.com/albums/123456789`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    get_user(uid)["url"] = text

    # Estrai titolo/ID album per mostrarlo
    aid = re.search(r"/albums/(\d+)", text)
    aid_str = f"#{aid.group(1)}" if aid else ""
    seller  = urlparse(text).netloc.split(".")[0]

    await update.message.reply_text(
        f"🔗 *Album ricevuto!*\n\n"
        f"🏪 Seller: `{seller}`\n"
        f"📁 Album: `{aid_str}`\n\n"
        f"Come vuoi ricevere i file?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_mode(),
    )


# ═══════════════════════════════════════════════
#  CALLBACK HANDLER (bottoni inline)
# ═══════════════════════════════════════════════
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    # ── Menu post-download ──
    if data == "new_download":
        await query.edit_message_text(
            "👇 *Mandami il link del prossimo album Yupoo*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "show_stats":
        s    = get_user(uid)
        mins = int((datetime.now() - s["started"]).total_seconds() / 60)
        await query.edit_message_text(
            "📊 *Le tue statistiche*\n\n"
            f"📦 Album scaricati:  `{s['total_albums']}`\n"
            f"🖼 File ricevuti:    `{s['total_files']}`\n"
            f"💾 Dati scaricati:  `{fmt_mb(s['total_mb'])}`\n"
            f"⏱ Sessione attiva: `{mins} min`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_back(),
        )
        return

    if data == "show_history":
        s       = get_user(uid)
        history = s.get("history", [])
        if not history:
            await query.edit_message_text("📋 Nessun album scaricato ancora.", reply_markup=kb_back())
            return
        lines = "\n".join(f"`{i+1}.` {url}" for i, url in enumerate(history[-10:]))
        await query.edit_message_text(
            f"📋 *Ultimi album*\n\n{lines}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_back(),
        )
        return

    # ── Modalità download ──
    if data not in ("mode_album", "mode_zip"):
        return

    s = get_user(uid)
    album_url = s.get("url")
    if not album_url:
        await query.edit_message_text("❌ Sessione scaduta, manda di nuovo il link.")
        return

    mode_zip = (data == "mode_zip")
    mode_str = "ZIP 🗜" if mode_zip else "Album Telegram 🖼"

    await query.edit_message_text(
        f"⏳ Avvio download in modalità *{mode_str}*...",
        parse_mode=ParseMode.MARKDOWN,
    )

    status = await query.message.reply_text("🔍 Analisi album in corso...")

    # Raccolta media
    try:
        media = collect_media(album_url)
    except Exception as e:
        await status.edit_text(f"❌ Errore analisi: {e}")
        return

    if not media:
        await status.edit_text(
            "❌ *Nessun media trovato*\n\n"
            "Possibili cause:\n"
            "• Album privato o rimosso\n"
            "• Link non valido\n"
            "• Yupoo temporaneamente non raggiungibile",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_back(),
        )
        return

    n_img = sum(1 for x in media if x["type"] == "image")
    n_vid = sum(1 for x in media if x["type"] == "video")
    await status.edit_text(
        f"📦 *{n_img}* immagini + *{n_vid}* video trovati\n"
        f"⬇️ Download in corso...\n\n"
        f"`{progress_bar(0, len(media))}` 0%",
        parse_mode=ParseMode.MARKDOWN,
    )

    session     = get_session()
    ok          = 0
    total_bytes = 0

    if mode_zip:
        ok, total_bytes = await _send_zip(query.message, session, media, album_url, status)
    else:
        ok, total_bytes = await _send_album(query.message, session, media, status)

    # Aggiorna statistiche utente
    s["total_albums"] += 1
    s["total_files"]  += ok
    s["total_mb"]     += total_bytes / 1024 / 1024
    if album_url not in s["history"]:
        s["history"].append(album_url)

    # Messaggio finale — cancella il messaggio di stato (sepolto tra le foto)
    # e manda un messaggio NUOVO in fondo alla chat, sempre visibile
    now     = datetime.now().strftime("%d.%m.%Y %H:%M")
    skipped = len(media) - ok
    summary = (
        f"✅ *Download completato!*\n"
        f"📅 `{now}`\n\n"
        f"🖼 File inviati:  `{ok}/{len(media)}`\n"
        f"💾 Dimensione:   `{fmt_mb(total_bytes/1024/1024)}`\n"
        f"📦 Totale album: `{s['total_albums']}`"
    )
    if skipped:
        summary += f"\n⚠️ Saltati: `{skipped}` (protetti o >50 MB)"

    summary += "\n\n*Cosa vuoi fare adesso?*"

    # Elimina il vecchio messaggio di progresso (era rimasto in mezzo alle foto)
    try:
        await status.delete()
    except Exception:
        pass

    # Manda il riepilogo come messaggio nuovo → appare SEMPRE in fondo
    await query.message.reply_text(
        summary,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_after_download(),
    )


# ═══════════════════════════════════════════════
#  INVIO ALBUM (media group, 10 per volta)
# ═══════════════════════════════════════════════
async def _send_album(message, session, media: list[dict], status_msg) -> tuple[int, int]:
    ok = 0; total_bytes = 0
    batch = []

    async def flush(b: list):
        nonlocal ok, total_bytes
        tg_media = []
        for i, (data, mtype, fname) in enumerate(b):
            buf = io.BytesIO(data); buf.name = fname
            caption = f"`{fname}`" if i == 0 else None
            if mtype == "video":
                tg_media.append(InputMediaVideo(media=buf, filename=fname, caption=caption,
                                                parse_mode=ParseMode.MARKDOWN, supports_streaming=True))
            else:
                tg_media.append(InputMediaPhoto(media=buf, caption=caption, parse_mode=ParseMode.MARKDOWN))
            ok += 1; total_bytes += len(data)
        try:
            await message.reply_media_group(media=tg_media)
        except Exception:
            for data, mtype, fname in b:
                buf = io.BytesIO(data); buf.name = fname
                try:
                    if mtype == "video": await message.reply_video(video=buf, filename=fname, supports_streaming=True)
                    else: await message.reply_document(document=buf, filename=fname)
                except Exception: pass

    for idx, item in enumerate(media, 1):
        url, mtype = item["url"], item["type"]
        fname = f"{idx:04d}{get_ext(url, mtype)}"

        pct = int(idx / len(media) * 100)
        try:
            await status_msg.edit_text(
                f"⬇️ *{idx}/{len(media)}* — `{fname}`\n\n"
                f"`{progress_bar(idx, len(media))}` {pct}%",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception: pass

        data = download_bytes(session, url, mtype)
        if not data or len(data) > TG_LIMIT:
            continue

        batch.append((data, mtype, fname))
        if len(batch) == 10:
            await flush(batch); batch = []; await asyncio.sleep(0.5) if False else time.sleep(0.3)

    if batch:
        await flush(batch)

    return ok, total_bytes


# ═══════════════════════════════════════════════
#  INVIO ZIP
# ═══════════════════════════════════════════════
async def _send_zip(message, session, media: list[dict], album_url: str, status_msg) -> tuple[int, int]:
    aid      = re.search(r"/albums/(\d+)", album_url)
    zip_name = f"album_{aid.group(1)}.zip" if aid else "album.zip"
    buf      = io.BytesIO()
    ok       = 0; total_bytes = 0

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for idx, item in enumerate(media, 1):
            url, mtype = item["url"], item["type"]
            fname = f"{idx:04d}{get_ext(url, mtype)}"
            pct   = int(idx / len(media) * 100)
            try:
                await status_msg.edit_text(
                    f"⬇️ *{idx}/{len(media)}* — `{fname}`\n\n"
                    f"`{progress_bar(idx, len(media))}` {pct}%",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception: pass
            data = download_bytes(session, url, mtype)
            if data:
                zf.writestr(fname, data); ok += 1; total_bytes += len(data)
            time.sleep(0.15)

    buf.seek(0); zip_bytes = buf.getvalue()
    await status_msg.edit_text(
        f"📤 Invio `{zip_name}` ({len(zip_bytes)//1024} KB)...",
        parse_mode=ParseMode.MARKDOWN,
    )

    if len(zip_bytes) <= TG_LIMIT:
        b = io.BytesIO(zip_bytes); b.name = zip_name
        await message.reply_document(document=b, filename=zip_name,
                                     caption=f"📦 {ok} file")
    else:
        parts = _split_zip(media, session, zip_name)
        for i, (pname, pbuf) in enumerate(parts, 1):
            pbuf.seek(0)
            await message.reply_document(document=pbuf, filename=pname,
                                         caption=f"📦 Parte {i}/{len(parts)}")

    return ok, total_bytes

def _split_zip(media, session, base_name):
    MAX = 45 * 1024 * 1024
    parts, part, buf, cur = [], 1, io.BytesIO(), 0
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED)
    for idx, item in enumerate(media, 1):
        data = download_bytes(session, item["url"], item["type"])
        if not data: continue
        fname = f"{idx:04d}{get_ext(item['url'], item['type'])}"
        if cur + len(data) > MAX and cur > 0:
            zf.close(); parts.append((f"{base_name}.part{part}.zip", buf))
            part += 1; buf = io.BytesIO(); zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED); cur = 0
        zf.writestr(fname, data); cur += len(data)
    zf.close()
    if cur > 0: parts.append((f"{base_name}.part{part}.zip", buf))
    return parts


# ═══════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════
def main():
    if BOT_TOKEN == "METTI_QUI_IL_TUO_TOKEN":
        print("ERRORE: configura YUPOO_BOT_TOKEN"); sys.exit(1)

    print("🤖 Yupoo Bot v3 avviato — pronto!")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
