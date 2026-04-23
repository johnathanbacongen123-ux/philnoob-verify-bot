import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import os
import random
import string
import asyncio
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN")
HCAPTCHA_SECRET   = os.getenv("HCAPTCHA_SECRET")
HCAPTCHA_SITE_KEY = "2eeeeb00-2ab2-4018-b01a-771206602b54"
ADMIN_ROLE_ID     = 1487090799853174975
CAT_VERIFY2       = 1496948054773993653

# ─────────────────────────────────────────────────────────────
#  BOT SETUP
# ─────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# pending verifications: {user_id: {"code": str, "role_id": int, "stage": int, "captcha_token": str}}
pending = {}

# ─────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────
def has_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    return any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles)

def gen_code(length=8) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ─────────────────────────────────────────────────────────────
#  STAGE 1 BUTTON
# ─────────────────────────────────────────────────────────────
class VerifyView(discord.ui.View):
    def __init__(self, role_id: int = 0):
        super().__init__(timeout=None)
        self.role_id = role_id

    @discord.ui.button(label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id="verify_start")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        code = gen_code()
        pending[user.id] = {"code": code, "role_id": self.role_id, "stage": 1}

        embed = discord.Embed(
            title="🔐 Step 1 — Code Verification",
            description=(
                f"Type the code below **exactly** as shown in this channel:\n\n"
                f"```{code}```\n"
                f"You have **5 minutes**. The code is case sensitive!"
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        def check(m):
            return m.author.id == user.id and m.guild == interaction.guild

        try:
            msg = await bot.wait_for("message", timeout=300, check=check)
            try:
                await msg.delete()
            except Exception:
                pass

            if msg.content.strip() == code:
                pending[user.id]["stage"] = 2
                await send_stage2(interaction, user)
            else:
                pending.pop(user.id, None)
                await interaction.followup.send("❌ Wrong code! Click **Verify** again to retry.", ephemeral=True)
        except asyncio.TimeoutError:
            pending.pop(user.id, None)
            await interaction.followup.send("⏰ Timed out! Click **Verify** again to retry.", ephemeral=True)

# ─────────────────────────────────────────────────────────────
#  STAGE 2 — CREATE CAPTCHA CHANNEL
# ─────────────────────────────────────────────────────────────
async def send_stage2(interaction: discord.Interaction, user: discord.Member):
    guild    = interaction.guild
    category = guild.get_channel(CAT_VERIFY2)

    if not category:
        await interaction.followup.send("❌ Verify category not found. Contact an admin.", ephemeral=True)
        return

    # check for existing channel
    for ch in category.channels:
        if ch.topic == str(user.id):
            await interaction.followup.send(f"✅ Step 1 passed! Continue in {ch.mention}", ephemeral=True)
            return

    role_id = pending[user.id]["role_id"]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user:               discord.PermissionOverwrite(read_messages=True, send_messages=False),
        guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }
    admin_role = guild.get_role(ADMIN_ROLE_ID)
    if admin_role:
        overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    ch = await guild.create_text_channel(
        name=f"verify-{user.name}",
        category=category,
        overwrites=overwrites,
        topic=str(user.id)
    )

    embed = discord.Embed(
        title="🤖 Step 2 — Captcha Verification",
        description=(
            f"Hey {user.mention}! Almost done!\n\n"
            f"**1.** Click **Complete Captcha** below\n"
            f"**2.** Solve the captcha on the page\n"
            f"**3.** Come back and click **Done**\n\n"
            f"You'll get your role instantly once verified!"
        ),
        color=discord.Color.green()
    )
    embed.set_footer(text="Proves you're not a bot!")

    view = CaptchaView(user_id=user.id, role_id=role_id, channel_id=ch.id)
    await ch.send(f"{user.mention}", embed=embed, view=view)
    await interaction.followup.send(f"✅ Step 1 passed! Complete step 2 in {ch.mention}", ephemeral=True)

# ─────────────────────────────────────────────────────────────
#  STAGE 2 BUTTON
# ─────────────────────────────────────────────────────────────
class CaptchaView(discord.ui.View):
    def __init__(self, user_id: int = 0, role_id: int = 0, channel_id: int = 0):
        super().__init__(timeout=None)
        self.user_id    = user_id
        self.role_id    = role_id
        self.channel_id = channel_id

        self.add_item(discord.ui.Button(
            label="Complete Captcha",
            emoji="🔗",
            style=discord.ButtonStyle.link,
            url=f"https://philnoob-verify.github.io/verify/?uid={user_id}&sitekey={HCAPTCHA_SITE_KEY}"
        ))

    @discord.ui.button(label="I'm Done", emoji="✅", style=discord.ButtonStyle.success, custom_id="captcha_done")
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This is not your verification!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        token = pending.get(self.user_id, {}).get("captcha_token")
        if not token:
            await interaction.followup.send(
                "❌ No captcha response found. Please click **Complete Captcha** first and solve it!",
                ephemeral=True
            )
            return

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.hcaptcha.com/siteverify",
                data={"secret": HCAPTCHA_SECRET, "response": token}
            ) as r:
                data = await r.json()

        if data.get("success"):
            guild = interaction.guild
            role  = guild.get_role(self.role_id)
            if role:
                await interaction.user.add_roles(role, reason="Passed verification")

            pending.pop(self.user_id, None)

            embed = discord.Embed(
                title="✅ Fully Verified!",
                description=f"Welcome {interaction.user.mention}! You now have access.",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            await asyncio.sleep(5)
            ch = bot.get_channel(self.channel_id)
            if ch:
                await ch.delete()
        else:
            await interaction.followup.send(
                "❌ Captcha failed. Please click **Complete Captcha** and try again!",
                ephemeral=True
            )

# ─────────────────────────────────────────────────────────────
#  /vsetup  ──  ADMIN ONLY
# ─────────────────────────────────────────────────────────────
@bot.tree.command(name="vsetup", description="[Admin] Create the verification panel.")
@app_commands.describe(
    channel="Channel to send the panel in",
    role="Role to give after full verification",
    title="Panel title",
    description="Panel description"
)
async def vsetup(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    role: discord.Role,
    title: str = "Verification",
    description: str = "Complete both steps to gain access to the server!"
):
    if not has_admin(interaction):
        await interaction.response.send_message("❌ No permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title=f"🔐 {title}",
        description=description,
        color=discord.Color.blue()
    )
    embed.add_field(name="Step 1", value="Type a verification code", inline=True)
    embed.add_field(name="Step 2", value="Complete a captcha", inline=True)
    embed.set_footer(text="Both steps required to gain access")

    await channel.send(embed=embed, view=VerifyView(role_id=role.id))
    await interaction.followup.send(f"✅ Panel sent to {channel.mention}!", ephemeral=True)

# ─────────────────────────────────────────────────────────────
#  ON READY
# ─────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    bot.add_view(VerifyView())
    bot.add_view(CaptchaView())
    await bot.tree.sync()
    print(f"Verify bot online as {bot.user}")
    print(f"    hCaptcha Site Key : {HCAPTCHA_SITE_KEY}")
    print(f"    Verify2 Cat ID    : {CAT_VERIFY2}")

bot.run(DISCORD_TOKEN)
