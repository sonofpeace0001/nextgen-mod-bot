"""Ban appeal system via DM."""
from __future__ import annotations
import discord, config, database as db, llm, logging
log = logging.getLogger("appeals")

class AppealView(discord.ui.View):
    def __init__(self, appeal_id):
        super().__init__(timeout=None)
        self.appeal_id = appeal_id
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="appeal_accept")
    async def accept(self, interaction, button):
        appeal = db.get_appeal(self.appeal_id)
        if not appeal: await interaction.response.send_message("Not found.", ephemeral=True); return
        db.update_appeal_status(self.appeal_id, "accepted")
        guild = interaction.guild
        try:
            await guild.unban(discord.Object(id=appeal["user_id"]), reason="Appeal accepted")
            user = await interaction.client.fetch_user(appeal["user_id"])
            t = await llm.generate("Tell this person their ban appeal was accepted. They can rejoin. Be warm but brief.")
            try: await user.send(t)
            except: pass
        except: pass
        db.log_action(guild.id, "APPEAL ACCEPTED", appeal["user_id"], str(interaction.user), "")
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.green(); e.set_footer(text=f"Accepted by {interaction.user}")
        await interaction.response.edit_message(embed=e, view=None)
    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="appeal_deny")
    async def deny(self, interaction, button):
        appeal = db.get_appeal(self.appeal_id)
        if not appeal: await interaction.response.send_message("Not found.", ephemeral=True); return
        db.update_appeal_status(self.appeal_id, "denied")
        try:
            user = await interaction.client.fetch_user(appeal["user_id"])
            t = await llm.generate("Tell this person their ban appeal was denied. Be respectful but firm.")
            try: await user.send(t)
            except: pass
        except: pass
        db.log_action(interaction.guild.id, "APPEAL DENIED", appeal["user_id"], str(interaction.user), "")
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.red(); e.set_footer(text=f"Denied by {interaction.user}")
        await interaction.response.edit_message(embed=e, view=None)

async def handle_dm(bot, message):
    if "appeal" not in message.content.lower(): return
    guild = bot.get_guild(config.GUILD_ID)
    if not guild: return
    try: await guild.fetch_ban(discord.Object(id=message.author.id))
    except discord.NotFound: await message.reply("You are not banned from this server."); return
    except: return
    aid = db.add_appeal(guild.id, message.author.id, message.content)
    ch = guild.get_channel(config.LOG_CHANNEL_ID)
    if ch:
        e = discord.Embed(title=f"Ban Appeal #{aid}", color=discord.Color.gold())
        e.add_field(name="User", value=f"{message.author} ({message.author.id})", inline=True)
        e.add_field(name="Reason", value=message.content[:500], inline=False)
        view = AppealView(aid)
        msg = await ch.send(embed=e, view=view)
        db.update_appeal_message(aid, msg.id)
    await message.reply("Your appeal has been submitted. A moderator will review it.")

async def restore_pending_views(bot):
    for a in db.get_pending_appeals():
        bot.add_view(AppealView(a["id"]))
