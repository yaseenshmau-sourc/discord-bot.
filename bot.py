import os
import datetime
import discord
from discord import app_commands
from discord.ext import commands

# ============ الإعدادات ============
TOKEN = os.environ["DISCORD_TOKEN"]  # التوكن من متغيرات البيئة، لا تكتبه بالكود

WELCOME_CHANNEL = "الترحيب"
MEMBER_ROLE = "عضو"
MOD_ROLE = "🛡 مشرف"
SUPPORT_CATEGORY = "🛠 الدعم"
GAME_ROLES = ["🎮 ماينكرافت", "🔫 ببجي", "🕹 ألعاب أخرى"]

# الرتب: (الاسم، اللون، الصلاحيات، إظهار منفصل)
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

# هيكل السيرفر
LAYOUT = {
    "📢 معلومات": {"text": ["القوانين", "الإعلانات", "الترحيب", "اختيار-الرتب"]},
    "💬 عام": {"text": ["الدردشة", "الميمز", "الصور-والكليبات"]},
    "🎮 الألعاب": {"text": ["ماينكرافت", "ببجي", "ألعاب-أخرى", "البحث-عن-فريق"]},
    "🔊 صوتي": {"voice": ["غرفة عامة", "غرفة ماينكرافت", "غرفة ببجي"]},
    SUPPORT_CATEGORY: {"text": ["فتح-تذكرة"]},
}
READ_ONLY = {"القوانين", "الإعلانات", "اختيار-الرتب", "فتح-تذكرة"}

intents = discord.Intents.default()
intents.members = True  # فعّل Server Members Intent من بوابة المطورين
bot = commands.Bot(command_prefix="!", intents=intents)


# ============ أزرار اختيار الرتب ============
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


# ============ نظام التذاكر ============
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
        await interaction.response.send_message("🔒 سيتم حذف التذكرة بعد 5 ثوانٍ...")
        await discord.utils.sleep_until(discord.utils.utcnow() + datetime.timedelta(seconds=5))
        await channel.delete()


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 فتح تذكرة", style=discord.ButtonStyle.success,
                       custom_id="ticket:open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
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
            f"تذكرة-{user.name}", overwrites=overwrites, category=category,
            topic=f"ticket:{user.id}")
        await channel.send(f"{user.mention} أهلاً! اشرح مشكلتك وراح يرد عليك المشرفون.",
                           view=CloseView())
        await interaction.response.send_message(f"✅ تم فتح تذكرتك: {channel.mention}", ephemeral=True)


# ============ الأحداث ============
async def setup_hook():
    bot.add_view(RolesView())
    bot.add_view(TicketView())
    bot.add_view(CloseView())

bot.setup_hook = setup_hook


@bot.event
async def on_ready():
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    print(f"✅ البوت شغال: {bot.user}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)


@bot.event
async def on_member_join(member: discord.Member):
    role = discord.utils.get(member.guild.roles, name=MEMBER_ROLE)
    if role:
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            pass
    channel = discord.utils.get(member.guild.text_channels, name=WELCOME_CHANNEL)
    if channel:
        await channel.send(f"👋 أهلاً وسهلاً {member.mention} في **{member.guild.name}**! "
                           f"اختر لعبتك من روم #اختيار-الرتب 🎮")


# ============ أوامر الإعداد ============
@bot.tree.command(name="setup_server", description="تجهيز السيرفر: الرتب والرومات")
@app_commands.default_permissions(administrator=True)
async def setup_server(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    for name, colour, perms, hoist in ROLES_TO_CREATE:
        if not discord.utils.get(guild.roles, name=name):
            await guild.create_role(name=name, colour=colour, permissions=perms, hoist=hoist)

    for cat_name, data in LAYOUT.items():
        category = discord.utils.get(guild.categories, name=cat_name) \
            or await guild.create_category(cat_name)
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

    await interaction.followup.send(
        "✅ تم تجهيز السيرفر!\nالخطوة الجاية: روح #اختيار-الرتب واكتب /setup_roles "
        "وبعدها #فتح-تذكرة واكتب /setup_tickets", ephemeral=True)


@bot.tree.command(name="setup_roles", description="نشر أزرار اختيار الألعاب في هذا الروم")
@app_commands.default_permissions(administrator=True)
async def setup_roles(interaction: discord.Interaction):
    await interaction.channel.send("🎮 **اختر ألعابك** (اضغط مرة ثانية لإزالة الرتبة):", view=RolesView())
    await interaction.response.send_message("✅ تم", ephemeral=True)


@bot.tree.command(name="setup_tickets", description="نشر زر فتح التذاكر في هذا الروم")
@app_commands.default_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    await interaction.channel.send("🎫 **تحتاج مساعدة؟** اضغط الزر لفتح تذكرة خاصة:", view=TicketView())
    await interaction.response.send_message("✅ تم", ephemeral=True)


# ============ أوامر الإدارة ============
@bot.tree.command(name="kick", description="طرد عضو")
@app_commands.describe(member="العضو", reason="السبب")
@app_commands.default_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"👢 تم طرد {member.mention} | السبب: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ ما أقدر أطرده (رتبته أعلى مني).", ephemeral=True)


@bot.tree.command(name="ban", description="حظر عضو")
@app_commands.describe(member="العضو", reason="السبب")
@app_commands.default_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "بدون سبب"):
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"🔨 تم حظر {member.mention} | السبب: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ ما أقدر أحظره (رتبته أعلى مني).", ephemeral=True)


@bot.tree.command(name="mute", description="كتم عضو بمدة بالدقائق")
@app_commands.describe(member="العضو", minutes="المدة بالدقائق", reason="السبب")
@app_commands.default_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member,
               minutes: app_commands.Range[int, 1, 40320], reason: str = "بدون سبب"):
    try:
        await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
        await interaction.response.send_message(f"🔇 تم كتم {member.mention} لمدة {minutes} دقيقة")
    except discord.Forbidden:
        await interaction.response.send_message("❌ ما أقدر أكتمه (رتبته أعلى مني).", ephemeral=True)


@bot.tree.command(name="unmute", description="فك الكتم عن عضو")
@app_commands.default_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(f"🔊 تم فك الكتم عن {member.mention}")


@bot.tree.command(name="clear", description="مسح رسائل من الروم")
@app_commands.describe(amount="عدد الرسائل (1-100)")
@app_commands.default_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 تم مسح {len(deleted)} رسالة", ephemeral=True)


bot.run(TOKEN)
