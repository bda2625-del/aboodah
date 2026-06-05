import logging
import requests
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
import arabic_reshaper
from bidi.algorithm import get_display
import asyncio
from dotenv import load_dotenv
import os
from PyPDF2 import PdfReader
import time
import re
import functools
from collections import defaultdict
from typing import Dict, List, Tuple

# تحميل متغيرات البيئة
load_dotenv()

# ✅ التوكنات من ملف .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 🎨 إعداد الخطوط والمسارات
FONT_DIR = "fonts"
BACKGROUND_IMAGE = "logoxx.png"
ALLOWED_USERS_FILE = "allowed_users.txt" # ملف حفظ المستخدمين الموافق عليهم

# إنشاء مجلد الخطوط إذا لم يكن موجوداً
if not os.path.exists(FONT_DIR):
    os.makedirs(FONT_DIR)

# ✅ مسار الخط الجديد
FONT_PATH = os.path.join(FONT_DIR, "IBMPlexSansArabic-Medium.ttf")

# ✅ إعداد السجل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 👑 إعدادات الأدمن والمستخدمين 👑
ADMIN_ID = 6662640296  # 🔴🔴 ضع الـ ID الخاص بك هنا 🔴🔴

def load_allowed_users():
    """تحميل قائمة المستخدمين المسموح لهم من الملف"""
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, 'r') as f:
            return set(int(line.strip()) for line in f if line.strip().isdigit())
    return set()

def save_allowed_user(user_id):
    """حفظ مستخدم جديد في القائمة والملف"""
    ALLOWED_USERS.add(user_id)
    with open(ALLOWED_USERS_FILE, 'a') as f:
        f.write(f"{user_id}\n")

def remove_allowed_user(user_id):
    """حذف مستخدم من القائمة والملف"""
    if user_id in ALLOWED_USERS:
        ALLOWED_USERS.remove(user_id)
        # إعادة كتابة الملف بدون هذا المستخدم
        with open(ALLOWED_USERS_FILE, 'w') as f:
            for uid in ALLOWED_USERS:
                f.write(f"{uid}\n")
        return True
    return False

ALLOWED_USERS = load_allowed_users()
ALLOWED_USERS.add(ADMIN_ID) # ضمان أن الأدمن دائماً مسموح له


# OpenRouter API endpoints
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# قائمة النماذج المحددة
AVAILABLE_MODELS = {
    "google/gemini-3-flash-preview": "⚡ جيمني 3 فلاش",
    "google/gemini-2.5-flash": "🚀 جيمني2.5 فلاش"
}

# المتغيرات العامة
CURRENT_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-3-flash-preview")
CURRENT_SUBJECT = "الرياضيات"  # المادة الافتراضية


# التخزين المؤقت للخطوط
@functools.lru_cache(maxsize=10)
def load_font(font_size):
    try:
        if os.path.exists(FONT_PATH):
            return ImageFont.truetype(FONT_PATH, font_size)
        else:
            return ImageFont.load_default()
    except Exception as e:
        print(f"⚠️ خطأ في تحميل الخط: {e}")
        return ImageFont.load_default()


# أحجام الخطوط الأساسية
BASE_FONT_SIZE = 42
ANSWER_FONT_SIZE = 42

# تحميل الخلفية
try:
    BACKGROUND = Image.open(BACKGROUND_IMAGE).convert("RGBA")
    print("✅ تم تحميل الخلفية بنجاح")
except Exception as e:
    BACKGROUND = Image.new("RGBA", (800, 600), (255, 255, 255, 255))
    print("📋 استخدام خلفية افتراضية")


def optimize_image_processing(img):
    max_size = 1800
    if img.width > max_size or img.height > max_size:
        ratio = min(max_size / img.width, max_size / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffered = BytesIO()
    img = img.convert("RGB")
    img.save(buffered, format="JPEG", quality=85, optimize=True)
    return buffered


def auto_font_size(text, max_width, max_font_size, min_font_size=20):
    for size in range(max_font_size, min_font_size, -3):
        try:
            font = load_font(size)
            dummy_img = Image.new("RGB", (1, 1))
            draw = ImageDraw.Draw(dummy_img)
            bbox = draw.textbbox((0, 0), text, font=font)
            width = bbox[2] - bbox[0]
            if width <= max_width:
                return font
        except:
            continue
    return load_font(min_font_size)


def draw_text_with_outline(draw, position, text, font, fill="black", outline="white", outline_width=2):
    x, y = position
    draw.text((x, y), text, font=font, fill=fill)


def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + (" " if current_line else "") + word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_width = bbox[2] - bbox[0]
        if line_width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines


def reshape_text(text, lang='ar'):
    try:
        if lang == 'ar' and any('\u0600' <= ch <= '\u06FF' for ch in text):
            reshaped = arabic_reshaper.reshape(text)
            bidi_text = get_display(reshaped)
            return bidi_text
        else:
            return text
    except Exception:
        return text


def extract_answer_code(answer_text):
    cleaned_answer = re.sub(r'\([^)]*\)', '', answer_text)
    cleaned_answer = re.sub(r'\[[^\]]*\]', '', cleaned_answer)
    cleaned_answer = re.sub(r'\{[^}]*\}', '', cleaned_answer)
    matches = re.findall(r'\b([A-G]|True|False|T|F|\d+)\b', cleaned_answer, re.IGNORECASE)
    if matches:
        return matches[0].upper()
    else:
        cleaned_answer = re.sub(r'[\(\)\[\]\{\}]', '', answer_text)
        return cleaned_answer.strip()


def stitch_images_vertically(images):
    if not images:
        return None
    widths, heights = zip(*(i.size for i in images))
    total_width = max(widths)
    total_height = sum(heights)
    new_im = Image.new('RGB', (total_width, total_height), (255, 255, 255))
    y_offset = 0
    for im in images:
        if im.mode != 'RGB':
            im = im.convert('RGB')
        new_im.paste(im, (0, y_offset))
        y_offset += im.size[1]
    return new_im


# ==================== مخزن الألبوم المؤقت ====================
class MediaAlbumBuffer:
    def __init__(self):
        self.buffer: Dict[str, Dict[int, Tuple[Update, ContextTypes.DEFAULT_TYPE]]] = defaultdict(dict)
        self.last_received: Dict[str, float] = {}
        self.processing: Dict[str, bool] = {}

    def add_to_buffer(self, media_group_id: str, message_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.buffer[media_group_id][message_id] = (update, context)
        self.last_received[media_group_id] = time.time()

    def get_sorted_messages(self, media_group_id: str) -> List[Tuple[Update, ContextTypes.DEFAULT_TYPE]]:
        if media_group_id not in self.buffer:
            return []
        sorted_messages = sorted(
            self.buffer[media_group_id].items(),
            key=lambda x: x[0]
        )
        return [item[1] for item in sorted_messages]

    def remove_buffer(self, media_group_id: str):
        if media_group_id in self.buffer:
            del self.buffer[media_group_id]
        if media_group_id in self.last_received:
            del self.last_received[media_group_id]
        if media_group_id in self.processing:
            del self.processing[media_group_id]


album_buffer = MediaAlbumBuffer()


async def cleanup_buffer_task():
    while True:
        await asyncio.sleep(300)
        try:
            current_time = time.time()
            to_remove = []
            for media_group_id, last_time in album_buffer.last_received.items():
                if current_time - last_time > 600:
                    to_remove.append(media_group_id)
            for media_group_id in to_remove:
                album_buffer.remove_buffer(media_group_id)
        except Exception as e:
            pass


# ==================== أوامر لوحة تحكم المدير ====================
async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المستخدمين (للمدير فقط)"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if len(ALLOWED_USERS) <= 1:
        await update.message.reply_text("📋 لا يوجد مستخدمين تمت الموافقة عليهم حالياً غيرك.")
        return
        
    msg = "📋 **قائمة المستخدمين المصرح لهم:**\n\n"
    for uid in ALLOWED_USERS:
        if uid == ADMIN_ID:
            msg += f"👑 `{uid}` (أنت - المدير)\n"
        else:
            msg += f"👤 `{uid}`\n"
            
    msg += "\n🗑️ لحذف أي شخص، انسخ الآيدي الخاص به واكتب:\n`/remove ID`"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف مستخدم (للمدير فقط)"""
    if update.effective_user.id != ADMIN_ID:
        return
        
    if not context.args:
        await update.message.reply_text("⚠️ يرجى إدخال الـ ID الخاص بالشخص المراد حذفه.\nمثال: `/remove 123456789`", parse_mode="Markdown")
        return
        
    try:
        target_id = int(context.args[0])
        
        if target_id == ADMIN_ID:
            await update.message.reply_text("❌ لا يمكنك حذف نفسك!")
            return
            
        if remove_allowed_user(target_id):
            await update.message.reply_text(f"✅ تم سحب الصلاحية من المستخدم `{target_id}` بنجاح.\nلن يتمكن من استخدام البوت بعد الآن.", parse_mode="Markdown")
            try:
                await context.bot.send_message(chat_id=target_id, text="⛔ تم سحب صلاحية استخدام البوت منك من قبل الإدارة.")
            except:
                pass
        else:
            await update.message.reply_text("❌ هذا الـ ID غير موجود في قائمة المسموح لهم.")
            
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال أرقام صحيحة فقط للـ ID.")


# ==================== أوامر البوت ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # ✅ فحص إذا كان المستخدم غير مسموح له (يحتاج موافقة)
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⏳ أهلاً بك! البوت مخصص حالياً، طلبك قيد المراجعة. تم إرسال إشعار للمسؤول، يرجى الانتظار.")
        
        # إرسال إشعار للمدير
        keyboard = [
            [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{user_id}"),
             InlineKeyboardButton("❌ رفض", callback_data=f"reject_{user_id}")]
        ]
        username = f"@{user.username}" if user.username else "لا يوجد معرف"
        admin_msg = f"👤 **طلب انضمام جديد**:\n\nالاسم: {user.first_name}\nالمعرف: {username}\nالآيدي: `{user_id}`"
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"لم يتمكن من إرسال رسالة للمسؤول: {e}")
        return

    # المستخدم مسموح له - عرض القائمة
    keyboard = [
        [InlineKeyboardButton("🔄 تغيير النموذج", callback_data='change_model')],
        [InlineKeyboardButton("📖 طريقة الاستخدام", callback_data='help')],
        [InlineKeyboardButton("🤖 النماذج المتاحة", callback_data='models')]
    ]
    await update.message.reply_text(
        f"👑 ** ملك الساحة إسكندر**\n\n🔄 النموذج: `{CURRENT_MODEL}`\n📚 المادة: `{CURRENT_SUBJECT}`\n📸 أرسل صورة أو PDF",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def slash_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    await start(update, context)


async def set_subject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    global CURRENT_SUBJECT
    if not context.args:
        await update.message.reply_text(
            f"📚 المادة الحالية هي: **{CURRENT_SUBJECT}**\n\nلتغييرها اكتب الأمر ثم اسم المادة، مثال:\n`/subject فيزياء`\n`/subject تاريخ`",
            parse_mode="Markdown")
        return

    new_subject = " ".join(context.args)
    CURRENT_SUBJECT = new_subject
    await update.message.reply_text(
        f"✅ تم تغيير التخصص إلى: **{CURRENT_SUBJECT}**\nسأقوم بحل الأسئلة بناءً على قوانين وقواعد {CURRENT_SUBJECT}.",
        parse_mode="Markdown")


async def change_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    global CURRENT_MODEL
    if not context.args:
        keyboard = []
        for model_id in AVAILABLE_MODELS.keys():
            model_name = model_id.split('/')[-1]
            keyboard.append([InlineKeyboardButton(
                f"{'🟢' if model_id == CURRENT_MODEL else '⚪'} {model_name}",
                callback_data=f"set_model_{model_id}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data='back_to_start')])
        await update.message.reply_text("🤖 **اختر النموذج:**", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    new_model = context.args[0]
    if new_model in AVAILABLE_MODELS:
        CURRENT_MODEL = new_model
        await update.message.reply_text(f"✅ تم التغيير لـ: `{CURRENT_MODEL}`")
    else:
        await update.message.reply_text("❌ النموذج غير معروف")


async def show_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    models_text = "🤖 **النماذج المتاحة:**\n\n"
    for model_id, description in AVAILABLE_MODELS.items():
        current_indicator = " 🟢" if model_id == CURRENT_MODEL else ""
        models_text += f"• `{model_id}` - {description}{current_indicator}\n"
    await update.message.reply_text(models_text)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # ✅ أزرار الموافقة والرفض للمدير
    if query.data.startswith('approve_'):
        if update.effective_user.id != ADMIN_ID: return
        target_user_id = int(query.data.split('_')[1])
        save_allowed_user(target_user_id)
        await query.edit_message_text(f"✅ تمت الموافقة بنجاح على المستخدم (`{target_user_id}`).", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=target_user_id, text="🎉 **تمت الموافقة!**\nيمكنك الآن استخدام البوت بحرية. اضغط /start للبدء.", parse_mode="Markdown")
        except:
            pass
        return
        
    elif query.data.startswith('reject_'):
        if update.effective_user.id != ADMIN_ID: return
        target_user_id = int(query.data.split('_')[1])
        await query.edit_message_text(f"❌ تم رفض المستخدم (`{target_user_id}`).", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=target_user_id, text="⛔ عذراً، تم رفض طلبك للوصول إلى البوت.")
        except:
            pass
        return

    # الأزرار العادية للمستخدمين المصرح لهم
    if update.effective_user.id not in ALLOWED_USERS: return

    if query.data == 'help':
        await query.edit_message_text(
            f"📖 أرسل صورة أو PDF وسأجيبك.\n\n📚 المادة الحالية: {CURRENT_SUBJECT}\nاستخدم /subject لتغييرها.")
    elif query.data == 'models':
        models_text = "🤖 **النماذج المتاحة:**\n\n"
        for model_id, desc in AVAILABLE_MODELS.items():
            ind = " 🟢" if model_id == CURRENT_MODEL else ""
            models_text += f"• `{model_id}` - {desc}{ind}\n"
        await query.edit_message_text(models_text)
    elif query.data == 'change_model':
        keyboard = []
        for model_id in AVAILABLE_MODELS.keys():
            m_name = model_id.split('/')[-1]
            keyboard.append([InlineKeyboardButton(f"{'🟢' if model_id == CURRENT_MODEL else '⚪'} {m_name}",
                                                  callback_data=f"set_model_{model_id}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data='back_to_start')])
        await query.edit_message_text("اختر النموذج:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith('set_model_'):
        new_model = query.data.replace('set_model_', '')
        await set_current_model(new_model, query)
    elif query.data == 'back_to_start':
        await query.edit_message_text(
            f"👑 **ملك الساحة عبود**\n🔄 النموذج: `{CURRENT_MODEL}`\n📚 المادة: `{CURRENT_SUBJECT}`",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 تغيير النموذج", callback_data='change_model')]]))


async def set_current_model(new_model, query):
    global CURRENT_MODEL
    CURRENT_MODEL = new_model
    await query.edit_message_text(f"✅ تم التغيير لـ: `{CURRENT_MODEL}`")


def requests_post_with_retry(url, json_payload, headers=None, retries=2, delay=1):
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(url, json=json_payload, headers=headers, timeout=60)
            if response.status_code == 404: return None
            response.raise_for_status()
            return response
        except Exception:
            if attempt < retries: time.sleep(delay * attempt)
    return None


def pdf_to_images_fast(file_bytes):
    try:
        from pdf2image import convert_from_bytes
        return convert_from_bytes(file_bytes, dpi=120, fmt='jpeg', thread_count=2, use_pdftocairo=True)
    except ImportError:
        try:
            pdf_reader = PdfReader(BytesIO(file_bytes))
            images = []
            for i in range(len(pdf_reader.pages)):
                img = Image.new('RGB', (600, 800), (255, 255, 255))
                draw = ImageDraw.Draw(img)
                draw.text((50, 50), f"Page {i + 1}", fill='black', font=load_font(30))
                images.append(img)
            return images
        except:
            return []
    except:
        return []


def call_openrouter_api_fast(image_base64):
    system_prompt = (
        f"أنت بروفيسور وخبير أكاديمي متخصص في مادة ({CURRENT_SUBJECT}).\n"
        f"مهمتك هي تحليل الصور وحل أسئلة ({CURRENT_SUBJECT}) بدقة متناهية.\n"
        "يجب عليك اتباع الخطوات التالية بالترتيب لكل سؤال في الصورة:\n\n"
        "1. تحديد رقم السؤال (مهم جداً: اكتب الرقم الأصلي المطبوع في الصورة كما هو بالضبط، لا تقم بترقيم الأسئلة تسلسلياً من عندك 1، 2، 3...).\n"
        f"2. ((مهم جداً)) حل السؤال حلاً كاملاً ومفصلاً خطوة بخطوة بناءً على نظريات وقوانين ({CURRENT_SUBJECT}) للوصول للإجابة الصحيحة.\n"
        "3. كتابة نص السؤال الأصلي كما هو في الصورة.\n"
        "4. كتابة الخيارات المتاحة (A, B, C, D, E, F, G).\n"
        "5. تحديد رمز الإجابة الصحيحة النهائي.\n\n"
        "تنسيق المخرجات المطلوب لكل سؤال:\n"
        "Question <ضع الرقم الأصلي هنا بدون أي أقواس>\n"
        "Solution: <اكتب هنا خطوات الحل التفصيلية الكاملة>\n"
        "Question: <نص السؤال>\n"
        "A: <الخيار الأول>\n"
        "B: <الخيار الثاني>\n"
        "C: <الخيار الثالث>\n"
        "D: <الخيار الرابع>\n"
        "E: <الخيار الخامس إن وجد>\n"
        "F: <الخيار السادس إن وجد>\n"
        "G: <الخيار السابع إن وجد>\n"
        "Answer: <الحرف الصحيح فقط A-G أو T/F>\n\n"
        "مهم جداً: لا تستخدم أي تنسيق Markdown مثل ** أو ## أو ``` في ردك.\n"
        "إذا لم تجد أسئلة، أرسل 'لا توجد أسئلة في الصورة'"
    )

    payload = {
        "model": CURRENT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": f"قم بحل واستخراج أسئلة مادة {CURRENT_SUBJECT} من هذه الصورة:"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]}
        ],
        "max_tokens": 4096,
        "temperature": 0.2
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "[https://bot.local](https://bot.local)",
        "X-Title": "Academic Solver Bot"
    }

    print(f"📤 إرسال طلب (النموذج: {CURRENT_MODEL} | المادة: {CURRENT_SUBJECT})...")
    return requests_post_with_retry(OPENROUTER_API_URL, payload, headers)


def extract_question_number(question_block):
    try:
        first_line = question_block.strip().split('\n')[0]
        match = re.search(r'(?:Question|Q|q|#)?\s*(\d+)', first_line)
        if match:
            return match.group(1)
    except:
        pass
    return None


def clean_markdown(text):
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'`+', '', text)
    text = re.sub(r'_{1,2}', '', text)
    text = re.sub(r'~~', '', text)
    return text.strip()


def parse_question_block(block):
    lines = [clean_markdown(ln) for ln in block.splitlines() if ln.strip()]

    q_lines = []
    opts = []
    ans_lines = []
    solution_lines = []

    for l in lines:
        clean_line = l.strip()
        if not clean_line:
            continue

        if re.match(r'^Solution\s*:', clean_line, re.IGNORECASE):
            sol_text = re.sub(r'^Solution\s*:\s*', '', clean_line, flags=re.IGNORECASE)
            solution_lines.append(sol_text)

        elif re.match(r'^(?:Question(?:\s*\d+)?|Q\.?\d*)\s*:', clean_line, re.IGNORECASE):
            q_text_only = re.sub(r'^(?:Question(?:\s*\d+)?|Q\.?\d*)\s*:\s*', '', clean_line, flags=re.IGNORECASE)
            if q_text_only:
                q_lines.append(f"Question: {q_text_only}")

        elif re.match(r'^[A-G]\s*[:.)\s]', clean_line, re.IGNORECASE) or \
             re.match(r'^\([A-G]\)', clean_line, re.IGNORECASE):
            normalized = re.sub(r'^\(?([A-G])\)?[\s.:)]+\s*', r'\1: ', clean_line, flags=re.IGNORECASE)
            opts.append(normalized)

        elif re.match(r'^(?:Answer|Ans|الإجابة|الجواب|الإجابة الصحيحة)\s*[:\-]', clean_line, re.IGNORECASE):
            ans_lines.append(clean_line)

        elif re.match(r'^[A-G]$', clean_line.strip(), re.IGNORECASE) and opts:
            ans_lines.append(f"Answer: {clean_line.strip()}")

    return q_lines, opts, ans_lines, solution_lines


async def process_single_page(page_data):
    page_num, img, update, context = page_data
    try:
        buffered = optimize_image_processing(img)
        image_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        response = await asyncio.to_thread(lambda: call_openrouter_api_fast(image_base64))

        if not response:
            return page_num, [], 0, []

        result = response.json()

        if "choices" not in result:
            return page_num, [], 0, []

        content = result["choices"][0]["message"]["content"]
        content = clean_markdown(content)

        question_blocks = [
            b.strip() for b in re.split(
                r'(?=(?:Question|Q\.?)\s*\d+)', content, flags=re.IGNORECASE
            ) if b.strip()
        ]

        if not question_blocks or len(question_blocks) == 0:
            question_blocks = [content]

        temp_pil_images = []
        question_info = []

        for block in question_blocks:
            if not block.strip():
                continue

            q_num = extract_question_number(block)
            q_lines, opts, ans_lines, solution_lines = parse_question_block(block)

            if not ans_lines:
                continue

            ans_raw = re.sub(r'^(?:Answer|Ans|الإجابة|الجواب|الإجابة الصحيحة)\s*[:\-]\s*', '',
                             ans_lines[0], flags=re.IGNORECASE).strip()
            ans_code = extract_answer_code(ans_raw)

            if q_num and ans_code:
                question_info.append({
                    'question_number': q_num,
                    'answer_code': ans_code
                })

            img_output = BACKGROUND.copy()
            draw = ImageDraw.Draw(img_output)
            x, y = 50, 50
            max_width = img_output.width - 100

            qn_text = f"Question {q_num}" if q_num else "Question"
            draw_text_with_outline(draw, (x, y + 20), reshape_text(qn_text), load_font(BASE_FONT_SIZE))
            y += 90

            if q_lines:
                q_text = reshape_text(q_lines[0].replace("Question:", "").strip())
                q_font = auto_font_size(q_text, max_width, 40, 24)
                for line in wrap_text(q_text, q_font, max_width, draw):
                    draw_text_with_outline(draw, (x, y), line, q_font)
                    y += 80

            y += 25

            for opt in opts:
                o_text = reshape_text(opt.strip())
                draw_text_with_outline(draw, (x + 30, y), o_text, auto_font_size(o_text, max_width, 36, 22))
                y += 65

            ans_text = reshape_text(ans_code)
            ans_font = auto_font_size(ans_text, max_width, 55, 35)
            full_ans = "Answer: " + ans_text
            bbox = draw.textbbox((0, 0), full_ans, font=ans_font)
            text_w = bbox[2] - bbox[0]
            x_center = (img_output.width - text_w) // 2
            y_bottom = img_output.height - 150
            draw_text_with_outline(draw, (x_center, y_bottom), full_ans, ans_font, fill="red")

            temp_pil_images.append(img_output)

        if temp_pil_images:
            stitched_image = stitch_images_vertically(temp_pil_images)
            out = BytesIO()
            stitched_image.save(out, format="JPEG", quality=85)
            out.seek(0)
            return page_num, [out], 1, question_info
        else:
            return page_num, [], 0, []

    except Exception as e:
        logger.error(f"Error page {page_num}: {e}")
        return page_num, [], 0, []


async def send_image_with_markdown(context, chat_id, image_bytes, question_info):
    try:
        image_bytes.seek(0)
        await context.bot.send_photo(chat_id=chat_id, photo=image_bytes)

        if question_info:
            markdown_lines = []
            for info in question_info:
                ans = info['answer_code']
                if ans in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:
                    emoji = "💚"
                elif ans in ['TRUE', 'T']:
                    emoji = "✅"
                elif ans in ['FALSE', 'F']:
                    emoji = "❌"
                else:
                    emoji = "🔢"
                markdown_lines.append(f"⚡️ تم حل السؤال {info['question_number']} {emoji}")

            markdown_text = "\n".join(markdown_lines)
            await context.bot.send_message(
                chat_id=chat_id,
                text=markdown_text,
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Error sending image with markdown: {e}")
        try:
            image_bytes.seek(0)
            await context.bot.send_photo(chat_id=chat_id, photo=image_bytes)
        except:
            pass


async def handle_media_fast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # ✅ الحماية: منع أي شخص غير موجود في القائمة من إرسال الصور
        if update.effective_user.id not in ALLOWED_USERS:
            await update.message.reply_text("⛔ البوت مغلق للاستخدام الشخصي فقط. اضغط /start لطلب الإذن.")
            return

        media_group_id = None
        if update.message and update.message.media_group_id:
            media_group_id = update.message.media_group_id

        if media_group_id:
            album_buffer.add_to_buffer(media_group_id, update.message.message_id, update, context)

            if media_group_id in album_buffer.processing and album_buffer.processing[media_group_id]:
                return

            album_buffer.processing[media_group_id] = True
            await asyncio.sleep(1.5)

            messages = album_buffer.get_sorted_messages(media_group_id)
            if not messages:
                album_buffer.remove_buffer(media_group_id)
                return

            all_images = []
            for msg_update, msg_context in messages:
                try:
                    images = []
                    if msg_update.message.photo:
                        f = await msg_context.bot.get_file(msg_update.message.photo[-1].file_id)
                        fb = await f.download_as_bytearray()
                        images = [Image.open(BytesIO(fb))]
                    elif msg_update.message.document:
                        f = await msg_context.bot.get_file(msg_update.message.document.file_id)
                        fb = await f.download_as_bytearray()
                        if msg_update.message.document.mime_type == "application/pdf":
                            images = pdf_to_images_fast(fb)
                        else:
                            images = [Image.open(BytesIO(fb))]
                    if images:
                        all_images.extend(images)
                except Exception as e:
                    logger.error(f"Error processing album image: {e}")

            if all_images:
                try:
                    wait_msg = await update.message.reply_text(
                        f"⏳ جاري معالجة {len(all_images)} صورة (المادة: {CURRENT_SUBJECT})...")
                except:
                    wait_msg = None

                tasks = [asyncio.create_task(process_single_page((i + 1, img, update, context)))
                         for i, img in enumerate(all_images)]
                results = await asyncio.gather(*tasks)

                if wait_msg:
                    try:
                        await wait_msg.delete()
                    except:
                        pass

                chat_id = update.message.chat_id
                total_questions = 0

                for page_num, imgs, _, questions_info in results:
                    if imgs and questions_info:
                        for img_bytes in imgs:
                            await send_image_with_markdown(context, chat_id, img_bytes, questions_info)
                            total_questions += len(questions_info)

                if total_questions > 0:
                    await context.bot.send_message(chat_id=chat_id,
                                                   text=f"✅ تم الانتهاء\n📊 الأسئلة المحلولة: {total_questions}",
                                                   parse_mode="Markdown")
                else:
                    await update.message.reply_text("❌ لم يتم العثور على أسئلة.")

            album_buffer.remove_buffer(media_group_id)

        else:
            images = []
            if update.message.photo:
                f = await context.bot.get_file(update.message.photo[-1].file_id)
                fb = await f.download_as_bytearray()
                images = [Image.open(BytesIO(fb))]
            elif update.message.document:
                f = await context.bot.get_file(update.message.document.file_id)
                fb = await f.download_as_bytearray()
                if update.message.document.mime_type == "application/pdf":
                    images = pdf_to_images_fast(fb)
                else:
                    images = [Image.open(BytesIO(fb))]

            if not images:
                await update.message.reply_text("❌ لم يتم استلام صور.")
                return

            try:
                wait_msg = await update.message.reply_text(f"⏳ جاري المعالجة (المادة: {CURRENT_SUBJECT})...")
            except:
                wait_msg = None

            tasks = [asyncio.create_task(process_single_page((i + 1, img, update, context)))
                     for i, img in enumerate(images)]
            results = await asyncio.gather(*tasks)

            if wait_msg:
                try:
                    await wait_msg.delete()
                except:
                    pass

            chat_id = update.message.chat_id
            total_questions = 0

            for page_num, imgs, _, questions_info in results:
                if imgs and questions_info:
                    for img_bytes in imgs:
                        await send_image_with_markdown(context, chat_id, img_bytes, questions_info)
                        total_questions += len(questions_info)

            if total_questions > 0:
                await context.bot.send_message(chat_id=chat_id, text=f"✅ تم المعالجة\n📊 الأسئلة: {total_questions}",
                                               parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ لم يتم العثور على أسئلة.")

    except Exception as e:
        logger.error(f"Handler Error: {e}")
        try:
            if update.message and update.message.media_group_id:
                album_buffer.remove_buffer(update.message.media_group_id)
        except:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ التوكن غير موجود. تأكد من إعداد ملف .env")
        return

    print("🚀 بدء تشغيل البوت الأكاديمي الشامل...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("slash", slash_command))
    app.add_handler(CommandHandler("changemodel", change_model))
    app.add_handler(CommandHandler("models", show_models))
    app.add_handler(CommandHandler("subject", set_subject_command))
    
    # ✅ أوامر الإدارة الجديدة
    app.add_handler(CommandHandler("users", list_users_command))
    app.add_handler(CommandHandler("remove", remove_user_command))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_media_fast))
    app.add_error_handler(error_handler)

    loop = asyncio.get_event_loop()
    loop.create_task(cleanup_buffer_task())

    print(f"✅ البوت يعمل الآن.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
