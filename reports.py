"""Member report system with mod action buttons."""
from __future__ import annotations
import discord, config, database as db, llm, moderation, logging
log = logging.getLogger("reports")

class ReportView(discord.ui.View):
    def __init__(self, report_id):
        super().__init__(timeout=None)
        self.report_id = report_id
    @discord.ui.button(label="Warn", style=discord.ButtonStyle.primary, custom_id="report_warn")
    async def warn_btn(self, interaction, button):
        report = db.get_report(self.report_id)
        if not report: await interaction.response.send_message("Not found.", ephemeral=True); return
        guild = interaction.guild; member = guild.get_member(report["target_id"])
        if member:
            total = db.add_warning(guild.id, member.id, str(interaction.user), report["reason"])
            db.log_action(guild.id, "WARN(report)", member.id, str(interaction.user), report["reason"])
            t = await llm.generate(f"Warn: {report['reason']}. Warning {total}. Calm.")
            try: await member.send(t)
            except: pass
        db.update_report_status(self.report_id, "warned")
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.yellow(); e.set_footer(text=f"Warned by {interaction.user}")
        await interaction.response.edit_message(embed=e, view=None)
    @discord.ui.button(label="Mute", style=discord.ButtonStyle.secondary, custom_id="report_mute")
    async def mute_btn(self, interaction, button):
        report = db.get_report(self.report_id)
        if not report: await interaction.response.send_message("Not found.", ephemeral=True); return
        guild = interaction.guild; member = guild.get_member(report["target_id"])
        if member: await moderation._mute(interaction.client, guild, member, report["reason"])
        db.update_report_status(self.report_id, "muted")
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.orange(); e.set_footer(text=f"Muted by {interaction.user}")
        await interaction.response.edit_message(embed=e, view=None)
    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, custom_id="report_ban")
    async def ban_btn(self, interaction, button):
        report = db.get_report(self.report_id)
        if not report: await interaction.response.send_message("Not found.", ephemeral=True); return
        guild = interaction.guild; member = guild.get_member(report["target_id"])
        if member: await moderation._ban(interaction.client, guild, member, report["reason"])
        db.update_report_status(self.report_id, "banned")
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.red(); e.set_footer(text=f"Banned by {interaction.user}")
        await interaction.response.edit_message(embed=e, view=None)
    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary, custom_id="report_dismiss")
    async def dismiss_btn(self, interaction, button):
        db.update_report_status(self.report_id, "dismissed")
        e = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        e.color = discord.Color.light_grey(); e.set_footer(text=f"Dismissed by {interaction.user}")
        await interaction.response.edit_message(embed=e, view=None)

async def _send_report(bot, guild, report_id, reporter, target, reason, msg_content="", msg_url=""):
    ch = guild.get_channel(config.LOG_CHANNEL_ID)
    if not ch: return
    e = discord.Embed(title=f"Report #{report_id}", color=discord.Color.orange())
    e.add_field(name="Reported User", value=f"{target} ({target.id})", inline=True)
    e.add_field(name="Reporter", value=f"{reporter}", inline=True)
    e.add_field(name="Reason", value=reason[:500], inline=False)
    if msg_content: e.add_field(name="Message", value=msg_content[:500], inline=False)
    if msg_url: e.add_field(name="Link", value=msg_url, inline=False)
    view = ReportView(report_id)
    msg = await ch.send(embed=e, view=view)
    db.update_report_message(report_id, msg.id)

async def submit_from_command(bot, interaction, member, reason):
    await interaction.response.defer(ephemeral=True)
    rid = db.add_report(interaction.guild.id, interaction.user.id, member.id, reason)
    await _send_report(bot, interaction.guild, rid, interaction.user, member, reason)
    await interaction.followup.send(f"Report #{rid} submitted.", ephemeral=True)

async def submit_from_context_menu(bot, interaction, message):
    await interaction.response.defer(ephemeral=True)
    rid = db.add_report(interaction.guild.id, interaction.user.id, message.author.id,
                        "Reported via context menu", message.content[:500], message.jump_url)
    await _send_report(bot, interaction.guild, rid, interaction.user, message.author,
                       "Context menu report", message.content[:500], message.jump_url)
    await interaction.followup.send(f"Report #{rid} submitted.", ephemeral=True)

async def restore_pending_views(bot):
    for r in db.get_pending_reports():
        bot.add_view(ReportView(r["id"]))
