import os
import io
import re
import glob
import json
import math
import time
import random
import asyncio
import datetime
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    RESHAPE_OK = True
except ImportError:
    RESHAPE_OK = False

# ============================================================
#                        الإعدادات
# ============================================================
TOKEN = os.environ["DISCORD_TOKEN"]  # التوكن من متغيرات البيئة، لا تكتبه بالكود
DATA_FILE = "data.json"
BRAND = "NEXUS GAMING"

# أسماء ثابتة (الرتب والأقسام)
MEMBER_ROLE = "عضو"
MOD_ROLE = "🛡 مشرف"
SUPPORT_CATEGORY = "🛠 الدعم"
ADMIN_CATEGORY = "🔒 الإدارة"
CREATOR_VC = "➕ أنشئ غرفتك"
GAME_ROLES = ["🎮 ماينكرافت", "🔫 ببجي", "🕹 ألعاب أخرى"]
LEVEL_ROLES = {5: "⭐ عضو مميز"}  # مستوى: رتبة
LINK_CHANNELS = {"الميمز", "الصور-والكليبات"}  # الروابط مسموحة فيها
XP_COOLDOWN = 60

# متجر الرتب: الاسم -> السعر
SHOP = {"💎 VIP": 1000, "🔥 أسطورة": 3000}
SHOP_COLOURS = {"💎 VIP": discord.Colour.teal(), "🔥 أسطورة": discord.Colour.dark_orange()}

# إعدادات قابلة للتغيير من /config_set
DEFAULT_CONFIG = {
    "welcome_channel": "الترحيب",
    "log_channel": "السجل",
    "new_account_days": "3",
    "max_warns": "3",
    "mc_ip": "",
    "automod": "on",
}

ROLES_TO_CREATE = [
    (MOD_ROLE, discord.Colour.red(),
     discord.Permissions(kick_members=True, ban_members=True, moderate_members=True,
                         manage_messages=True, manage_channels=True), True),
    ("⭐ عضو مميز", discord.Colour.gold(), discord.Permissions.none(), True),
    (MEMBER_ROLE, discord.Colour.light_grey(), discord.Permissions.none(), False),
    (GAME_ROLES[0], discord.Colour.green(), discord.Permissions.none(), False),
    (GAME_ROLES[1], discord.Colour.orange(), discord.Permissions.none(), False),
    (GAME_ROLES[2], discord.Colour.blue(), discord.Permissions.none(), False),
]

LAYOUT = {
    "📢 معلومات": {"text": ["القوانين", "الإعلانات", "الفعاليات", "الترحيب", "اختيار-الرتب"]},
    "💬 عام": {"text": ["الدردشة", "الميمز", "الصور-والكليبات", "الاستطلاعات", "الهدايا"]},
    "🎮 الألعاب": {"text": ["ماينكرافت", "ببجي", "ألعاب-أخرى", "البحث-عن-فريق", "البطولات"]},
    "🔊 صوتي": {"voice": ["غرفة عامة", "غرفة ماينكرافت", "غرفة ببجي", CREATOR_VC]},
    SUPPORT_CATEGORY: {"text": ["فتح-تذكرة"]},
    ADMIN_CATEGORY: {"text": ["السجل"]},
}
READ_ONLY = {"القوانين", "الإعلانات", "الفعاليات", "اختيار-الرتب", "فتح-تذكرة"}
PRIVATE_CATEGORIES = {ADMIN_CATEGORY}

# ألوان
PRIMARY = discord.Colour.from_rgb(88, 101, 242)
GOLD = discord.Colour.from_rgb(255, 193, 7)
GREEN = discord.Colour.from_rgb(46, 204, 113)
RED = discord.Colour.from_rgb(231, 76, 60)
ORANGE = discord.Colour.from_rgb(243, 156, 18)

LINE = "━━━━━━━━━━━━━━━━━━"


# ============================================================
#                     حفظ البيانات
# ============================================================
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    for key, default in (("users", {}), ("autoposts", []), ("giveaways", {}),
                         ("badwords", {}), ("tempvoice", []), ("config", {})):
        data.setdefault(key, default)
    return data


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(DATA, f, ensure_ascii=False)


DATA = load_data()
last_xp = {}
spam_tracker = {}
URL_RE = re.compile(r"(https?://|www\.|discord\.gg/|discord\.com/invite/)", re.I)

intents = discord.Intents.default()
intents.members = True          # Server Members Intent
intents.message_content = True  # Message Content Intent (للحماية التلقائية)
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)


# ============================================================
#                     دوال مساعدة
# ============================================================
def cfg(guild: discord.Guild, key: str) -> str:
    return DATA["config"].get(str(guild.id), {}).get(key, DEFAULT_CONFIG[key])


def make_embed(title: str, desc: Optional[str] = None, colour: discord.Colour = PRIMARY,
               thumb: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title=title, description=desc, colour=colour,
                          timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"✦ {BRAND} ✦")
    if thumb:
        embed.set_thumbnail(url=thumb)
    return embed


async def send_log(guild: discord.Guild, embed: discord.Embed, file: Optional[discord.File] = None):
    channel = discord.utils.get(guild.text_channels, name=cfg(guild, "log_channel"))
    if channel:
        try:
            await channel.send(embed=embed, file=file)
        except discord.HTTPException:
            pass


def get_user(guild_id: int, user_id: int) -> dict:
    return DATA["users"].setdefault(f"{guild_id}:{user_id}", {"xp": 0})


def level_from_xp(xp: int) -> int:
    return int(math.sqrt(xp / 50))


def xp_for_level(level: int) -> int:
    return 50 * level * level


def add_xp(guild_id: int, user_id: int, amount: int):
    user = get_user(guild_id, user_id)
    old = level_from_xp(user["xp"])
    user["xp"] += amount
    new = level_from_xp(user["xp"])
    save_data()
    return old, new, user["xp"]


def add_coins(guild_id: int, user_id: int, amount: int) -> int:
    user = get_user(guild_id, user_id)
    user["coins"] = user.get("coins", 0) + amount
    save_data()
    return user["coins"]


async def check_level_roles(member: discord.Member, level: int):
    for lvl, name in LEVEL_ROLES.items():
        if level >= lvl:
            role = discord.utils.get(member.guild.roles, name=name)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    pass


def guild_posts(guild_id: int):
    return [p for p in DATA["autoposts"] if p["guild_id"] == guild_id]


# ============================================================
#                  أزرار اختيار الرتب
# ============================================================
class RoleButton(discord.ui.Button):
    def __init__(self, role_name: str):
        super().__init__(label=role_name, style=discord.ButtonStyle.primary,
                         custom_id=f"role:{role_name}")
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction):
        role = discord.utils.get(interaction.guild.roles, name=self.role_name)
        if role is None:
            return await interaction.response.send_message(
                "الرتبة غير موجودة، شغّل /setup_server أولاً.", ephemeral=True)
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role)
                msg = f"✅ تم إزالة رتبة {role.name}"
            else:
                await member.add_roles(role)
                msg = f"✅ تم إعطاؤك رتبة {role.name}"
        except discord.Forbidden:
            msg = "❌ ما عندي صلاحية. ارفع رتبة البوت فوق رتب الألعاب."
        await interaction.response.send_message(msg, ephemeral=True)


class RolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for r in GAME_ROLES:
            self.add_item(RoleButton(r))


# ============================================================
#                   نظام التذاكر
# ============================================================
TICKET_TYPES = {
    "support": ("🛠", "دعم-فني"),
    "complaint": ("📣", "شكوى"),
    "suggestion": ("💡", "اقتراح"),
}


async def build_transcript(channel: discord.TextChannel) -> str:
    lines = []
    async for m in channel.history(limit=500, oldest_first=True):
        stamp = m.created_at.strftime("%Y-%m-%d %H:%M")
        content = m.content or ("[مرفق أو إيمبد]" if (m.attachments or m.embeds) else "")
        lines.append(f"[{stamp}] {m.author.display_name}: {content}")
    return "\n".join(lines)


class CloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 إغلاق التذكرة", style=discord.ButtonStyle.danger,
                       custom_id="ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        is_owner = channel.topic == f"ticket:{interaction.user.id}"
        is_staff = interaction.user.guild_permissions.manage_channels
        if not (is_owner or is_staff):
            return await interaction.response.send_message("❌ ما تقدر تغلق هذي التذكرة.", ephemeral=True)
        await interaction.response.send_message("🔒 جاري حفظ المحادثة وإغلاق التذكرة...")
        transcript = await build_transcript(channel)
        file = discord.File(io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}.txt")
        embed = make_embed("🎫 تذكرة مغلقة",
                           f"**الروم:** {channel.name}\n**أغلقها:** {interaction.user.mention}", RED)
        await send_log(interaction.guild, embed, file)
        await asyncio.sleep(3)
        await channel.delete()


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=name.replace("-", " "), value=key, emoji=emoji)
                   for key, (emoji, name) in TICKET_TYPES.items()]
        super().__init__(placeholder="🎫 اختر نوع التذكرة...", options=options,
                         custom_id="ticket:type")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        emoji, name = TICKET_TYPES[self.values[0]]
        for ch in guild.text_channels:
            if ch.topic == f"ticket:{user.id}":
                return await interaction.response.send_message(
                    f"عندك تذكرة مفتوحة: {ch.mention}", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        mod = discord.utils.get(guild.roles, name=MOD_ROLE)
        if mod:
            overwrites[mod] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        category = discord.utils.get(guild.categories, name=SUPPORT_CATEGORY)

        channel = await guild.create_text_channel(
            f"{name}-{user.name}", overwrites=overwrites, category=category,
            topic=f"ticket:{user.id}")
        embed = make_embed(f"{emoji} تذكرة {name.replace('-', ' ')}",
                           f"## أهلاً {user.display_name}\nاشرح موضوعك بالتفصيل وراح يرد عليك المشرفون قريباً.",
                           GREEN)
        await channel.send(content=user.mention, embed=embed, view=CloseView())
        await interaction.response.send_message(f"✅ تم فتح تذكرتك: {channel.mention}", ephemeral=True)
        try:
            await interaction.message.edit(view=TicketView())  # يرجع القائمة لوضعها الأول
        except discord.HTTPException:
            pass


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


# ============================================================
#              تسجيل لاعبين (LFG والبطولات)
# ============================================================
class SignupView(discord.ui.View):
    def __init__(self, owner: discord.abc.User, title: str, note: str, slots: int,
                 owner_joins: bool = True, colour: discord.Colour = PRIMARY):
        super().__init__(timeout=7200)
        self.title = title
        self.note = note
        self.slots = slots
        self.colour = colour
        self.players = [owner.id] if owner_joins else []

    def render(self) -> discord.Embed:
        names = "\n".join(f"`{i}.` <@{uid}>" for i, uid in enumerate(self.players, 1))
        if not names:
            names = "— لا أحد بعد —"
        desc = f"{self.note}\n{LINE}\n**اللاعبون ({len(self.players)}/{self.slots})**\n{names}"
        return make_embed(self.title, desc, self.colour)

    @discord.ui.button(label="✅ انضم", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid in self.players:
            return await interaction.response.send_message("أنت مسجل أصلاً ✅", ephemeral=True)
        if len(self.players) >= self.slots:
            return await interaction.response.send_message("😕 العدد اكتمل.", ephemeral=True)
        self.players.append(uid)
        full = len(self.players) >= self.slots
        if full:
            button.disabled = True
        await interaction.response.edit_message(embed=self.render(), view=self)
        if full:
            mentions = " ".join(f"<@{p}>" for p in self.players)
            await interaction.channel.send(f"🎮 اكتمل العدد! جاهزين؟ {mentions}")

    @discord.ui.button(label="🚪 انسحب", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in self.players:
            return await interaction.response.send_message("أنت مو مسجل.", ephemeral=True)
        self.players.remove(uid)
        self.children[0].disabled = False
        await interaction.response.edit_message(embed=self.render(), view=self)


# ============================================================
#                       الهدايا
# ============================================================
class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎉 شارك", style=discord.ButtonStyle.success, custom_id="giveaway:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = DATA["giveaways"].get(str(interaction.message.id))
        if not g or g["ended"]:
            return await interaction.response.send_message("انتهت هذي الفعالية.", ephemeral=True)
        uid = interaction.user.id
        if uid in g["entries"]:
            g["entries"].remove(uid)
            msg = "🚪 تم سحب مشاركتك"
        else:
            g["entries"].append(uid)
            msg = "✅ تم تسجيل مشاركتك، بالتوفيق!"
        save_data()
        await interaction.response.send_message(f"{msg} ({len(g['entries'])} مشارك)", ephemeral=True)


async def end_giveaway(message_id: str, g: dict):
    g["ended"] = True
    save_data()
    channel = bot.get_channel(g["channel_id"])
    if not channel:
        return
    try:
        message = await channel.fetch_message(int(message_id))
    except discord.HTTPException:
        message = None
    entries = g["entries"]
    winners = random.sample(entries, min(g["winners"], len(entries)))
    text = " ".join(f"<@{w}>" for w in winners) if winners else "لا يوجد مشاركون 😢"
    embed = make_embed("🎁 انتهت الفعالية", f"## {g['prize']}\n{LINE}\n**الفائزون:** {text}", GOLD)
    if message:
        try:
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass
    if winners:
        await channel.send(f"🎊 مبروك {text}! ربحت **{g['prize']}**")


# ============================================================
#                      ألعاب صغيرة
# ============================================================
CHOICES = {"rock": "🪨 حجر", "paper": "📄 ورقة", "scissors": "✂️ مقص"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


class RPSView(discord.ui.View):
    def __init__(self, player: discord.abc.User):
        super().__init__(timeout=30)
        self.player = player

    async def play(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id != self.player.id:
            return await interaction.response.send_message("هذي مو لعبتك، اكتب /rps", ephemeral=True)
        bot_choice = random.choice(list(CHOICES))
        if choice == bot_choice:
            result, colour = "🤝 تعادل!", ORANGE
        elif BEATS[choice] == bot_choice:
            result, colour = "🎉 فزت!", GREEN
        else:
            result, colour = "😅 خسرت!", RED
        for c in self.children:
            c.disabled = True
        embed = make_embed("✊ حجر ورقة مقص",
                           f"أنت: {CHOICES[choice]}\nالبوت: {CHOICES[bot_choice]}\n{LINE}\n## {result}", colour)
        await interaction.response.edit_message(content=None, embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="🪨 حجر", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "rock")

    @discord.ui.button(label="📄 ورقة", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "paper")

    @discord.ui.button(label="✂️ مقص", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "scissors")


QUESTIONS = [
    {"q": "ما المادة اللازمة لصنع بوابة النذر (Nether) في ماينكرافت؟",
     "o": ["أوبسيديان", "حجر عادي", "ذهب", "ألماس"], "a": 0},
    {"q": "ما اسم المخلوق الأخضر الذي ينفجر في ماينكرافت؟",
     "o": ["كريبر", "زومبي", "سكيلتون", "إندرمان"], "a": 0},
    {"q": "كم عدد اللاعبين في فريق سكواد (Squad) في ببجي موبايل؟",
     "o": ["2", "4", "6", "8"], "a": 1},
    {"q": "من هي الشركة المطورة لسلسلة ماريو؟",
     "o": ["نينتندو", "سوني", "مايكروسوفت", "سيغا"], "a": 0},
    {"q": "كم عدد قلوب الصحة الكاملة للاعب في ماينكرافت؟",
     "o": ["5", "10", "20", "3"], "a": 1},
    {"q": "ما اسم التنين الموجود في بُعد الإند (End) في ماينكرافت؟",
     "o": ["إندر دراغون", "واردن", "غاست", "ويذر"], "a": 0},
]


class TriviaView(discord.ui.View):
    def __init__(self, player: discord.abc.User, question: dict):
        super().__init__(timeout=40)
        self.player = player
        self.question = question
        for i, opt in enumerate(question["o"]):
            button = discord.ui.Button(label=opt, style=discord.ButtonStyle.secondary)
            button.callback = self.make_callback(i)
            self.add_item(button)

    def make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.player.id:
                return await interaction.response.send_message(
                    "هذا السؤال مو لك، اكتب /trivia", ephemeral=True)
            for c in self.children:
                c.disabled = True
            q = self.question
            if index == q["a"]:
                old, new, _ = add_xp(interaction.guild.id, interaction.user.id, 30)
                add_coins(interaction.guild.id, interaction.user.id, 10)
                result = "## ✅ إجابة صحيحة!\n+30 نقطة و +10 عملات"
                colour = GREEN
                if new > old:
                    await check_level_roles(interaction.user, new)
                    result += f"\n🎉 وصلت للمستوى {new}!"
            else:
                result = f"## ❌ إجابة خاطئة\nالصحيح: **{q['o'][q['a']]}**"
                colour = RED
            embed = make_embed("🧠 سؤال", f"**{q['q']}**\n{LINE}\n{result}", colour)
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
        return callback


# ============================================================
#                        الأحداث
# ============================================================
# ============================================================
#                  بطاقة الترحيب بالصورة
# ============================================================
ARABIC_FONT_PATHS = [
    "font.ttf",  # ضع هنا أي خط عربي تحبه بجانب البوت
    "/system/fonts/NotoNaskhArabic-Regular.ttf",
    "/system/fonts/NotoSansArabic-Regular.ttf",
    "/system/fonts/NotoKufiArabic-Regular.ttf",
    "/system/fonts/DroidSansArabic.ttf",
]
LATIN_FONT_PATHS = ["/system/fonts/Roboto-Regular.ttf", "/system/fonts/DroidSans.ttf"]


def has_arabic(text: str) -> bool:
    return any("\u0600" <= c <= "\u06FF" for c in text)


def find_arabic_font() -> Optional[str]:
    for path in ARABIC_FONT_PATHS:
        if os.path.exists(path):
            return path
    found = glob.glob("/system/fonts/*Arabic*")
    return found[0] if found else None


ARABIC_FONT = find_arabic_font()
ARABIC_OK = bool(PIL_OK and RESHAPE_OK and ARABIC_FONT)


def load_font(size: int, arabic: bool = False):
    paths = [ARABIC_FONT] if (arabic and ARABIC_FONT) else LATIN_FONT_PATHS
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def shape(text: str) -> str:
    if ARABIC_OK and has_arabic(text):
        return get_display(arabic_reshaper.reshape(text))
    return text


def build_welcome_card(avatar_bytes: bytes, name: str, number: int) -> io.BytesIO:
    width, height = 900, 380
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    top, bottom = (24, 26, 51), (96, 62, 170)
    for y in range(height):
        t = y / height
        colour = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=colour)
    draw.rounded_rectangle([12, 12, width - 12, height - 12], radius=28,
                           outline=(255, 193, 7), width=3)

    size = 170
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    cx, cy = width // 2, 105
    ring = size + 16
    draw.ellipse((cx - ring // 2, cy - ring // 2, cx + ring // 2, cy + ring // 2),
                 fill=(255, 193, 7))
    img.paste(avatar, (cx - size // 2, cy - size // 2), mask)

    def centered(text: str, y: int, font, fill):
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (box[2] - box[0])) // 2 - box[0], y), text, font=font, fill=fill)

    shown = name[:22]
    if has_arabic(shown) and not ARABIC_OK:
        shown = "NEW MEMBER"
    title = "أهلاً بك في السيرفر" if ARABIC_OK else "WELCOME"
    sub = f"أنت العضو رقم {number}" if ARABIC_OK else f"Member #{number}"

    centered(shape(title), 205, load_font(48, ARABIC_OK), (255, 193, 7))
    centered(shape(shown), 262, load_font(40, ARABIC_OK and has_arabic(shown)), (255, 255, 255))
    centered(shape(sub), 322, load_font(26, ARABIC_OK), (200, 200, 235))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


async def build_welcome(member: discord.Member):
    guild = member.guild
    file = None
    if PIL_OK:
        try:
            avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
            buf = await asyncio.to_thread(build_welcome_card, avatar_bytes,
                                          member.display_name, guild.member_count)
            file = discord.File(buf, filename="welcome.png")
        except Exception as error:
            print(f"[WARN] welcome card failed: {error}")
    desc = (f"## أهلاً {member.mention} 👋\n"
            f"نورتنا في **{guild.name}**\n{LINE}\n"
            f"أنت العضو رقم **{guild.member_count}**\n"
            f"📜 اقرأ **#القوانين**\n🎮 اختر لعبتك من **#اختيار-الرتب**")
    embed = make_embed("✨ عضو جديد", desc, GOLD,
                       thumb=None if file else member.display_avatar.url)
    if file:
        embed.set_image(url="attachment://welcome.png")
    return embed, file


async def setup_hook():
    bot.add_view(RolesView())
    bot.add_view(TicketView())
    bot.add_view(CloseView())
    bot.add_view(GiveawayView())
    background_loop.start()

bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    print(f"[OK] Bot is online: {bot.user}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)


@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    role = discord.utils.get(guild.roles, name=MEMBER_ROLE)
    if role:
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            pass

    channel = discord.utils.get(guild.text_channels, name=cfg(guild, "welcome_channel"))
    if channel:
        embed, file = await build_welcome(member)
        await channel.send(content=member.mention, embed=embed, file=file)

    # تنبيه الحسابات الجديدة
    age_days = (discord.utils.utcnow() - member.created_at).days
    try:
        limit = int(cfg(guild, "new_account_days"))
    except ValueError:
        limit = 3
    if age_days < limit:
        embed = make_embed("⚠️ حساب جديد جداً",
                           f"{member.mention} عمر حسابه **{age_days}** يوم فقط.", ORANGE,
                           thumb=member.display_avatar.url)
        await send_log(guild, embed)


@bot.event
async def on_member_remove(member: discord.Member):
    embed = make_embed("👋 غادر السيرفر", f"{member} (`{member.id}`)", RED)
    await send_log(member.guild, embed)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState,
                                after: discord.VoiceState):
    # إنشاء غرفة مؤقتة
    if after.channel and after.channel.name == CREATOR_VC:
        overwrites = {member: discord.PermissionOverwrite(manage_channels=True, move_members=True)}
        try:
            vc = await member.guild.create_voice_channel(
                f"🎧 غرفة {member.display_name}", category=after.channel.category,
                overwrites=overwrites)
            DATA["tempvoice"].append(vc.id)
            save_data()
            await member.move_to(vc)
        except discord.HTTPException:
            pass
    # حذف الغرفة المؤقتة الفاضية
    if before.channel and before.channel.id in DATA["tempvoice"] and not before.channel.members:
        try:
            await before.channel.delete()
        except discord.HTTPException:
            pass
        DATA["tempvoice"].remove(before.channel.id)
        save_data()


# ============================================================
#                  الحماية التلقائية
# ============================================================
async def run_automod(message: discord.Message) -> bool:
    guild = message.guild
    content = message.content or ""
    now = time.time()
    key = (guild.id, message.author.id)
    times = [t for t in spam_tracker.get(key, []) if now - t < 6]
    times.append(now)
    spam_tracker[key] = times

    reason = None
    is_spam = False
    channel_name = getattr(message.channel, "name", "")
    if channel_name not in LINK_CHANNELS and URL_RE.search(content):
        reason = "الروابط غير مسموحة هنا"
    elif any(w in content.lower() for w in DATA["badwords"].get(str(guild.id), [])):
        reason = "كلمة ممنوعة"
    elif len(times) >= 6:
        reason = "سبام: رسائل كثيرة بسرعة"
        is_spam = True
    if not reason:
        return False

    try:
        await message.delete()
    except discord.HTTPException:
        pass
    await message.channel.send(f"🚫 {message.author.mention} {reason}", delete_after=6)
    if is_spam:
        spam_tracker[key] = []
        try:
            await message.author.timeout(datetime.timedelta(minutes=5), reason="سبام")
        except discord.HTTPException:
            pass
    embed = make_embed("🛡 حماية تلقائية",
                       f"**العضو:** {message.author.mention}\n**الروم:** {message.channel.mention}\n"
                       f"**السبب:** {reason}", ORANGE)
    await send_log(guild, embed)
    return True


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    perms = message.author.guild_permissions
    exempt = perms.administrator or perms.manage_messages
    if not exempt and cfg(message.guild, "automod") == "on":
        if await run_automod(message):
            return

    key = (message.guild.id, message.author.id)
    now = time.time()
    if now - last_xp.get(key, 0) < XP_COOLDOWN:
        return
    last_xp[key] = now
    old, new, _ = add_xp(message.guild.id, message.author.id, random.randint(15, 25))
    if new > old:
        add_coins(message.guild.id, message.author.id, 50)
        embed = make_embed("🎉 ترقية مستوى",
                           f"## {message.author.mention}\nوصلت للمستوى **{new}**\n+50 عملة 🪙", GOLD,
                           thumb=message.author.display_avatar.url)
        await message.channel.send(embed=embed)
        await check_level_roles(message.author, new)


# ============================================================
#        حلقة الخلفية: الرسائل التلقائية والهدايا
# ============================================================
@tasks.loop(minutes=1)
async def background_loop():
    now = time.time()
    changed = False
    for post in list(DATA["autoposts"]):
        if now >= post["next"]:
            channel = bot.get_channel(post["channel_id"])
            if channel:
                try:
                    await channel.send(post["text"])
                except discord.HTTPException:
                    pass
            if post.get("once"):
                DATA["autoposts"].remove(post)
            else:
                post["next"] = now + post["hours"] * 3600
            changed = True
    if changed:
        save_data()

    for message_id, g in list(DATA["giveaways"].items()):
        if not g["ended"] and now >= g["end"]:
            await end_giveaway(message_id, g)


@background_loop.before_loop
async def before_background():
    await bot.wait_until_ready()


# ============================================================
#                      أوامر الإعداد
# ============================================================
@bot.tree.command(name="setup_server", description="تجهيز السيرفر: الرتب والرومات")
@app_commands.default_permissions(administrator=True)
async def setup_server(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    for name, colour, perms, hoist in ROLES_TO_CREATE:
        if not discord.utils.get(guild.roles, name=name):
            await guild.create_role(name=name, colour=colour, permissions=perms, hoist=hoist)
    mod = discord.utils.get(guild.roles, name=MOD_ROLE)

    for cat_name, data in LAYOUT.items():
        category = discord.utils.get(guild.categories, name=cat_name)
        if category is None:
            cat_overwrites = {}
            if cat_name in PRIVATE_CATEGORIES:
                cat_overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
                if mod:
                    cat_overwrites[mod] = discord.PermissionOverwrite(view_channel=True)
            category = await guild.create_category(cat_name, overwrites=cat_overwrites)
        for ch in data.get("text", []):
            if discord.utils.get(category.text_channels, name=ch):
                continue
            overwrites = {}
            if ch in READ_ONLY:
                overwrites[guild.default_role] = discord.PermissionOverwrite(send_messages=False)
            await category.create_text_channel(ch, overwrites=overwrites)
        for ch in data.get("voice", []):
            if discord.utils.get(category.voice_channels, name=ch):
                continue
            await category.create_voice_channel(ch)

    await interaction.followup.send("✅ تم تجهيز السيرفر!", ephemeral=True)


@bot.tree.command(name="setup_roles", description="نشر أزرار اختيار الألعاب في هذا الروم")
@app_commands.default_permissions(administrator=True)
async def setup_roles(interaction: discord.Interaction):
    embed = make_embed("🎮 اختر ألعابك",
                       f"اضغط على الزر لتحصل على رتبة اللعبة.\nاضغط مرة ثانية لإزالتها.\n{LINE}", PRIMARY)
    await interaction.channel.send(embed=embed, view=RolesView())
    await interaction.response.send_message("✅ تم", ephemeral=True)


@bot.tree.command(name="setup_tickets", description="نشر قائمة فتح التذاكر في هذا الروم")
@app_commands.default_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    embed = make_embed("🎫 مركز المساعدة",
                       f"## تحتاج مساعدة؟\nاختر نوع التذكرة من القائمة وبيفتح لك روم خاص مع الإدارة.\n{LINE}",
                       GREEN)
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ تم", ephemeral=True)


@bot.tree.command(name="welcome_test", description="معاينة رسالة الترحيب بنفسك")
@app_commands.default_permissions(administrator=True)
async def welcome_test(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed, file = await build_welcome(interaction.user)
    kwargs = {"file": file} if file else {}
    await interaction.followup.send(embed=embed, ephemeral=True, **kwargs)


@bot.tree.command(name="config_set", description="تغيير إعداد من إعدادات البوت")
@app_commands.describe(key="الإعداد", value="القيمة الجديدة")
@app_commands.choices(key=[app_commands.Choice(name=k, value=k) for k in DEFAULT_CONFIG])
@app_commands.default_permissions(administrator=True)
async def config_set(interaction: discord.Interaction, key: app_commands.Choice[str], value: str):
    DATA["config"].setdefault(str(interaction.guild.id), {})[key.value] = value
    save_data()
    await interaction.response.send_message(f"✅ {key.value} = `{value}`", ephemeral=True)


@bot.tree.command(name="config_show", description="عرض إعدادات البوت")
@app_commands.default_permissions(administrator=True)
async def config_show(interaction: discord.Interaction):
    lines = [f"**{k}:** `{cfg(interaction.guild, k) or '—'}`" for k in DEFAULT_CONFIG]
    await interaction.response.send_message(
        embed=make_embed("⚙️ الإعدادات", "\n".join(lines), PRIMARY), ephemeral=True)


# ============================================================
#                   الإعلانات والفعاليات
# ============================================================
@bot.tree.command(name="announce", description="إرسال إعلان منسق في روم")
@app_commands.describe(channel="الروم", title="العنوان", text="نص الإعلان", ping_everyone="منشن للجميع؟")
@app_commands.default_permissions(administrator=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel,
                   title: str, text: str, ping_everyone: bool = False):
    embed = make_embed(f"📢 {title}", f"{text}\n{LINE}", GOLD)
    await channel.send(content="@everyone" if ping_everyone else None, embed=embed,
                       allowed_mentions=discord.AllowedMentions(everyone=ping_everyone))
    await interaction.response.send_message("✅ تم إرسال الإعلان", ephemeral=True)


@bot.tree.command(name="autopost_add", description="رسالة تلقائية تتكرر كل عدد ساعات")
@app_commands.describe(channel="الروم", hours="كل كم ساعة", text="نص الرسالة")
@app_commands.default_permissions(administrator=True)
async def autopost_add(interaction: discord.Interaction, channel: discord.TextChannel,
                       hours: app_commands.Range[int, 1, 168], text: str):
    DATA["autoposts"].append({"guild_id": interaction.guild.id, "channel_id": channel.id,
                              "text": text, "hours": hours, "next": time.time() + hours * 3600})
    save_data()
    await interaction.response.send_message(
        f"✅ بترسل الرسالة في {channel.mention} كل {hours} ساعة", ephemeral=True)


@bot.tree.command(name="autopost_list", description="عرض الرسائل التلقائية")
@app_commands.default_permissions(administrator=True)
async def autopost_list(interaction: discord.Interaction):
    posts = guild_posts(interaction.guild.id)
    if not posts:
        return await interaction.response.send_message("ما في رسائل تلقائية.", ephemeral=True)
    lines = []
    for i, p in enumerate(posts, 1):
        kind = "مرة وحدة" if p.get("once") else f"كل {p['hours']} ساعة"
        lines.append(f"**{i}.** <#{p['channel_id']}> {kind}: {p['text'][:50]}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="autopost_remove", description="حذف رسالة تلقائية بالرقم")
@app_commands.describe(number="الرقم من /autopost_list")
@app_commands.default_permissions(administrator=True)
async def autopost_remove(interaction: discord.Interaction, number: int):
    posts = guild_posts(interaction.guild.id)
    if number < 1 or number > len(posts):
        return await interaction.response.send_message("❌ رقم غير صحيح.", ephemeral=True)
    DATA["autoposts"].remove(posts[number - 1])
    save_data()
    await interaction.response.send_message("🗑 تم الحذف", ephemeral=True)


@bot.tree.command(name="remind", description="تذكير بفعالية: رسالة مرة وحدة بعد عدد دقائق")
@app_commands.describe(channel="الروم", minutes="بعد كم دقيقة", text="نص التذكير")
@app_commands.default_permissions(administrator=True)
async def remind(interaction: discord.Interaction, channel: discord.TextChannel,
                 minutes: app_commands.Range[int, 1, 10080], text: str):
    DATA["autoposts"].append({"guild_id": interaction.guild.id, "channel_id": channel.id,
                              "text": text, "hours": 0, "once": True,
                              "next": time.time() + minutes * 60})
    save_data()
    await interaction.response.send_message(
        f"⏰ بنذكّر في {channel.mention} بعد {minutes} دقيقة", ephemeral=True)


@bot.tree.command(name="giveaway", description="إطلاق فعالية هدايا")
@app_commands.describe(prize="الجائزة", minutes="المدة بالدقائق", winners="عدد الفائزين",
                       channel="الروم (الحالي إذا تركته فاضي)")
@app_commands.default_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction, prize: str,
                   minutes: app_commands.Range[int, 1, 10080],
                   winners: app_commands.Range[int, 1, 10] = 1,
                   channel: Optional[discord.TextChannel] = None):
    channel = channel or interaction.channel
    end = time.time() + minutes * 60
    embed = make_embed("🎁 فعالية هدايا",
                       f"## {prize}\n{LINE}\n**الفائزون:** {winners}\n"
                       f"**تنتهي:** <t:{int(end)}:R>\nاضغط الزر للمشاركة 👇", GOLD)
    msg = await channel.send(embed=embed, view=GiveawayView())
    DATA["giveaways"][str(msg.id)] = {"channel_id": channel.id, "prize": prize, "end": end,
                                      "winners": winners, "entries": [], "ended": False}
    save_data()
    await interaction.response.send_message("✅ انطلقت الفعالية", ephemeral=True)


@bot.tree.command(name="poll", description="استطلاع رأي (خيارات مفصولة بفاصلة، حتى 5)")
@app_commands.describe(question="السؤال", options="مثال: نعم, لا, ربما")
async def poll(interaction: discord.Interaction, question: str, options: str):
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    opts = [o.strip() for o in options.replace("،", ",").split(",") if o.strip()][:5]
    if len(opts) < 2:
        return await interaction.response.send_message("❌ لازم خيارين على الأقل.", ephemeral=True)
    desc = "\n".join(f"{nums[i]}  {o}" for i, o in enumerate(opts))
    embed = make_embed(f"📊 {question}", f"{desc}\n{LINE}\nصوّت بالضغط على الرقم 👇", PRIMARY)
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(opts)):
        await msg.add_reaction(nums[i])


# ============================================================
#                       اللاعبون والبطولات
# ============================================================
@bot.tree.command(name="lfg", description="ابحث عن فريق للعب")
@app_commands.describe(game="اللعبة", slots="عدد اللاعبين المطلوب (مع نفسك)", note="ملاحظة (اختياري)")
@app_commands.choices(game=[app_commands.Choice(name=g, value=g) for g in GAME_ROLES])
async def lfg(interaction: discord.Interaction, game: app_commands.Choice[str],
              slots: app_commands.Range[int, 2, 10] = 4, note: str = ""):
    role = discord.utils.get(interaction.guild.roles, name=game.value)
    mention = role.mention if role else ""
    view = SignupView(interaction.user, f"🎮 يبحث عن فريق: {game.value}",
                      note or "من يبي يلعب معي؟", slots)
    await interaction.response.send_message(
        content=mention, embed=view.render(), view=view,
        allowed_mentions=discord.AllowedMentions(roles=True))


@bot.tree.command(name="tournament", description="إنشاء بطولة وتسجيل المشاركين")
@app_commands.describe(name="اسم البطولة", slots="عدد المشاركين", info="التفاصيل (الموعد، الجوائز...)")
@app_commands.default_permissions(administrator=True)
async def tournament(interaction: discord.Interaction, name: str,
                     slots: app_commands.Range[int, 2, 64] = 16, info: str = ""):
    view = SignupView(interaction.user, f"🏆 بطولة: {name}", info or "سجّل مشاركتك بالزر!",
                      slots, owner_joins=False, colour=GOLD)
    await interaction.response.send_message(embed=view.render(), view=view)


@bot.tree.command(name="mcserver", description="حالة سيرفر ماينكرافت (Java)")
@app_commands.describe(ip="عنوان السيرفر (أو اتركه فاضي لاستخدام المحفوظ)")
async def mcserver(interaction: discord.Interaction, ip: Optional[str] = None):
    ip = ip or cfg(interaction.guild, "mc_ip")
    if not ip:
        return await interaction.response.send_message(
            "❌ اكتب العنوان، أو احفظه بـ /config_set mc_ip", ephemeral=True)
    await interaction.response.defer()
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"https://api.mcsrvstat.us/3/{ip}") as resp:
                data = await resp.json()
    except Exception:
        return await interaction.followup.send("❌ تعذر الاتصال بخدمة الفحص.")
    if not data.get("online"):
        return await interaction.followup.send(
            embed=make_embed("⛏ ماينكرافت", f"**{ip}**\n🔴 السيرفر أوفلاين", RED))
    players = data.get("players", {})
    motd = " ".join(data.get("motd", {}).get("clean", [])) or "—"
    desc = (f"**{ip}**\n🟢 أونلاين\n{LINE}\n"
            f"👥 اللاعبون: **{players.get('online', 0)}/{players.get('max', 0)}**\n"
            f"🧩 النسخة: **{data.get('version', '?')}**\n📝 {motd}")
    await interaction.followup.send(embed=make_embed("⛏ ماينكرافت", desc, GREEN))


# ============================================================
#                    المستويات والاقتصاد
# ============================================================
@bot.tree.command(name="rank", description="شوف مستواك ونقاطك")
@app_commands.describe(member="العضو (اختياري)")
async def rank(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    user = get_user(interaction.guild.id, member.id)
    xp = user["xp"]
    lvl = level_from_xp(xp)
    need = xp_for_level(lvl + 1)
    base = xp_for_level(lvl)
    filled = int(10 * (xp - base) / max(need - base, 1))
    bar = "🟩" * filled + "⬜" * (10 - filled)
    desc = (f"## المستوى {lvl}\n{bar}\n**النقاط:** {xp} / {need}\n"
            f"**العملات:** {user.get('coins', 0)} 🪙")
    await interaction.response.send_message(
        embed=make_embed(f"📊 {member.display_name}", desc, PRIMARY, thumb=member.display_avatar.url))


@bot.tree.command(name="leaderboard", description="أفضل 10 أعضاء بالنقاط")
async def leaderboard(interaction: discord.Interaction):
    prefix = f"{interaction.guild.id}:"
    items = [(k, v["xp"]) for k, v in DATA["users"].items() if k.startswith(prefix)]
    items.sort(key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (key, xp) in enumerate(items[:10]):
        uid = int(key.split(":")[1])
        m = interaction.guild.get_member(uid)
        name = m.display_name if m else f"مستخدم {uid}"
        mark = medals[i] if i < 3 else f"`{i + 1}.`"
        lines.append(f"{mark} **{name}** — مستوى {level_from_xp(xp)} ({xp} نقطة)")
    text = "\n".join(lines) if lines else "ما في أحد بعد، ابدأوا دردشة!"
    await interaction.response.send_message(embed=make_embed("🏆 المتصدرين", text, GOLD))


@bot.tree.command(name="daily", description="مكافأة يومية من العملات")
async def daily(interaction: discord.Interaction):
    user = get_user(interaction.guild.id, interaction.user.id)
    now = time.time()
    left = user.get("last_daily", 0) + 20 * 3600 - now
    if left > 0:
        hours = int(left // 3600)
        minutes = int((left % 3600) // 60)
        return await interaction.response.send_message(
            f"⏳ ارجع بعد {hours} ساعة و{minutes} دقيقة.", ephemeral=True)
    reward = random.randint(100, 200)
    user["last_daily"] = now
    total = add_coins(interaction.guild.id, interaction.user.id, reward)
    await interaction.response.send_message(
        embed=make_embed("🎁 المكافأة اليومية", f"## +{reward} 🪙\nرصيدك الآن: **{total}**", GREEN))


@bot.tree.command(name="balance", description="رصيدك من العملات")
async def balance(interaction: discord.Interaction):
    user = get_user(interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(
        embed=make_embed("🪙 رصيدك", f"## {user.get('coins', 0)} عملة", GOLD), ephemeral=True)


@bot.tree.command(name="shop", description="متجر الرتب")
async def shop(interaction: discord.Interaction):
    lines = [f"**{name}** — {price} 🪙" for name, price in SHOP.items()]
    desc = "\n".join(lines) + f"\n{LINE}\nللشراء: /buy"
    await interaction.response.send_message(embed=make_embed("🛒 المتجر", desc, PRIMARY))


@bot.tree.command(name="buy", description="شراء رتبة من المتجر")
@app_commands.choices(item=[app_commands.Choice(name=f"{n} - {p} عملة", value=n)
                            for n, p in SHOP.items()])
async def buy(interaction: discord.Interaction, item: app_commands.Choice[str]):
    guild = interaction.guild
    member = interaction.user
    price = SHOP[item.value]
    role = discord.utils.get(guild.roles, name=item.value)
    if role and role in member.roles:
        return await interaction.response.send_message("عندك هذي الرتبة أصلاً ✅", ephemeral=True)
    user = get_user(guild.id, member.id)
    if user.get("coins", 0) < price:
        return await interaction.response.send_message(
            f"❌ رصيدك ما يكفي. تحتاج {price} 🪙", ephemeral=True)
    try:
        if role is None:
            role = await guild.create_role(name=item.value, colour=SHOP_COLOURS[item.value])
        await member.add_roles(role)
    except discord.Forbidden:
        return await interaction.response.send_message("❌ ما عندي صلاحية أعطيك الرتبة.", ephemeral=True)
    user["coins"] -= price
    save_data()
    await interaction.response.send_message(
        embed=make_embed("🛍 تم الشراء", f"## {item.value}\nمبروك! تبقى لك **{user['coins']}** 🪙", GREEN))


# ============================================================
#                         الألعاب
# ============================================================
@bot.tree.command(name="rps", description="حجر ورقة مقص ضد البوت")
async def rps(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=make_embed("✊ حجر ورقة مقص", "اختر حركتك 👇"), view=RPSView(interaction.user))


@bot.tree.command(name="trivia", description="سؤال ألعاب، الصح يعطيك نقاط وعملات")
async def trivia(interaction: discord.Interaction):
    q = random.choice(QUESTIONS)
    await interaction.response.send_message(
        embed=make_embed("🧠 سؤال", f"**{q['q']}**"), view=TriviaView(interaction.user, q))


@bot.tree.command(name="dice", description="رمي نرد")
@app_commands.describe(sides="عدد الأوجه (الافتراضي 6)")
async def dice(interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6):
    await interaction.response.send_message(
        embed=make_embed("🎲 النرد", f"## {random.randint(1, sides)}\nمن {sides}", PRIMARY))


@bot.tree.command(name="coin", description="رمي عملة")
async def coin(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=make_embed("🪙 العملة", f"## {random.choice(['صورة', 'كتابة'])}", GOLD))


# ============================================================
#                  الإدارة والتحذيرات
# ============================================================
def mod_embed(title: str, member: discord.abc.User, by: discord.abc.User, reason: str,
              colour: discord.Colour) -> discord.Embed:
    return make_embed(title, f"**العضو:** {member.mention}\n**بواسطة:** {by.mention}\n"
                             f"**السبب:** {reason}", colour)


@bot.tree.command(name="kick", description="طرد عضو")
@app_commands.describe(member="العضو", reason="السبب")
@app_commands.default_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    try:
        await member.kick(reason=reason)
    except discord.Forbidden:
        return await interaction.response.send_message("❌ ما أقدر أطرده (رتبته أعلى مني).", ephemeral=True)
    embed = mod_embed("👢 تم الطرد", member, interaction.user, reason, ORANGE)
    await interaction.response.send_message(embed=embed)
    await send_log(interaction.guild, embed)


@bot.tree.command(name="ban", description="حظر عضو")
@app_commands.describe(member="العضو", reason="السبب")
@app_commands.default_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    try:
        await member.ban(reason=reason)
    except discord.Forbidden:
        return await interaction.response.send_message("❌ ما أقدر أحظره (رتبته أعلى مني).", ephemeral=True)
    embed = mod_embed("🔨 تم الحظر", member, interaction.user, reason, RED)
    await interaction.response.send_message(embed=embed)
    await send_log(interaction.guild, embed)


@bot.tree.command(name="mute", description="كتم عضو بمدة بالدقائق")
@app_commands.describe(member="العضو", minutes="المدة بالدقائق", reason="السبب")
@app_commands.default_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member,
               minutes: app_commands.Range[int, 1, 40320], reason: str = "بدون سبب"):
    try:
        await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    except discord.Forbidden:
        return await interaction.response.send_message("❌ ما أقدر أكتمه (رتبته أعلى مني).", ephemeral=True)
    embed = mod_embed(f"🔇 تم الكتم ({minutes} دقيقة)", member, interaction.user, reason, ORANGE)
    await interaction.response.send_message(embed=embed)
    await send_log(interaction.guild, embed)


@bot.tree.command(name="unmute", description="فك الكتم عن عضو")
@app_commands.default_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(
        embed=make_embed("🔊 فك الكتم", f"تم فك الكتم عن {member.mention}", GREEN))


@bot.tree.command(name="clear", description="مسح رسائل من الروم")
@app_commands.describe(amount="عدد الرسائل (1-100)")
@app_commands.default_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 تم مسح {len(deleted)} رسالة", ephemeral=True)
    embed = make_embed("🧹 مسح رسائل",
                       f"**بواسطة:** {interaction.user.mention}\n**الروم:** {interaction.channel.mention}\n"
                       f"**العدد:** {len(deleted)}", ORANGE)
    await send_log(interaction.guild, embed)


@bot.tree.command(name="warn", description="تحذير عضو (بعد عدد معين يُكتم تلقائياً)")
@app_commands.describe(member="العضو", reason="السبب")
@app_commands.default_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    user = get_user(interaction.guild.id, member.id)
    warns = user.setdefault("warns", [])
    warns.append({"reason": reason, "by": interaction.user.id, "time": int(time.time())})
    try:
        limit = int(cfg(interaction.guild, "max_warns"))
    except ValueError:
        limit = 3
    extra = ""
    if len(warns) >= limit:
        user["warns"] = []
        try:
            await member.timeout(datetime.timedelta(hours=1), reason="وصل الحد الأقصى للتحذيرات")
            extra = f"\n🔇 وصل {limit} تحذيرات وتم كتمه ساعة."
        except discord.HTTPException:
            extra = "\n⚠️ وصل الحد الأقصى، بس ما قدرت أكتمه."
    save_data()
    embed = mod_embed(f"⚠️ تحذير ({len(warns) if user['warns'] else limit}/{limit})",
                      member, interaction.user, reason + extra, ORANGE)
    await interaction.response.send_message(embed=embed)
    await send_log(interaction.guild, embed)


@bot.tree.command(name="warnings", description="عرض تحذيرات عضو")
@app_commands.default_permissions(moderate_members=True)
async def warnings(interaction: discord.Interaction, member: discord.Member):
    warns = get_user(interaction.guild.id, member.id).get("warns", [])
    if not warns:
        return await interaction.response.send_message("✅ ما عنده تحذيرات.", ephemeral=True)
    lines = [f"**{i}.** {w['reason']} — <t:{w['time']}:R>" for i, w in enumerate(warns, 1)]
    await interaction.response.send_message(
        embed=make_embed(f"⚠️ تحذيرات {member.display_name}", "\n".join(lines), ORANGE), ephemeral=True)


@bot.tree.command(name="clearwarns", description="مسح تحذيرات عضو")
@app_commands.default_permissions(moderate_members=True)
async def clearwarns(interaction: discord.Interaction, member: discord.Member):
    get_user(interaction.guild.id, member.id)["warns"] = []
    save_data()
    await interaction.response.send_message("🧽 تم مسح التحذيرات", ephemeral=True)


@bot.tree.command(name="badword_add", description="إضافة كلمة ممنوعة")
@app_commands.default_permissions(administrator=True)
async def badword_add(interaction: discord.Interaction, word: str):
    words = DATA["badwords"].setdefault(str(interaction.guild.id), [])
    if word.lower() not in words:
        words.append(word.lower())
        save_data()
    await interaction.response.send_message("✅ تمت الإضافة", ephemeral=True)


@bot.tree.command(name="badword_remove", description="حذف كلمة ممنوعة")
@app_commands.default_permissions(administrator=True)
async def badword_remove(interaction: discord.Interaction, word: str):
    words = DATA["badwords"].setdefault(str(interaction.guild.id), [])
    if word.lower() in words:
        words.remove(word.lower())
        save_data()
    await interaction.response.send_message("🗑 تم الحذف", ephemeral=True)


@bot.tree.command(name="badword_list", description="عرض الكلمات الممنوعة")
@app_commands.default_permissions(administrator=True)
async def badword_list(interaction: discord.Interaction):
    words = DATA["badwords"].get(str(interaction.guild.id), [])
    text = "، ".join(words) if words else "ما في كلمات ممنوعة."
    await interaction.response.send_message(text, ephemeral=True)


@bot.tree.command(name="help", description="قائمة أوامر البوت")
async def help_command(interaction: discord.Interaction):
    desc = (
        f"**🎮 ألعاب**\n/rps  /trivia  /dice  /coin\n{LINE}\n"
        "**👥 لاعبون**\n/lfg  /tournament  /mcserver\n"
        f"{LINE}\n**📊 مستويات واقتصاد**\n/rank  /leaderboard  /daily  /balance  /shop  /buy\n"
        f"{LINE}\n**🎉 تفاعل**\n/poll  /giveaway\n"
        f"{LINE}\n**🛡 إدارة**\n/kick  /ban  /mute  /unmute  /clear  /warn  /warnings  /clearwarns\n"
        f"{LINE}\n**⚙️ للأدمن**\n/setup_server  /setup_roles  /setup_tickets  /announce  /remind\n"
        "/autopost_add  /autopost_list  /autopost_remove  /badword_add  /badword_remove  "
        "/badword_list  /config_set  /config_show  /welcome_test"
    )
    await interaction.response.send_message(embed=make_embed("📖 قائمة الأوامر", desc, PRIMARY), ephemeral=True)


bot.run(TOKEN)
