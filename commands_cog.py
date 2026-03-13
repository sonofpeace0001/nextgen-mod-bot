"""All slash commands. Only immune roles can use mod commands. No ban commands."""
from __future__ import annotations
import asyncio, datetime, re
import discord
from discord import app_commands
from discord.ext import commands
import config, database as db, llm, moderation
import reports as reports_module


def _has_immune_role(member) -> bool:
    """Check if member has any immune role (Elite, Admin, Escalation)."""
    for role in member.roles:
        if role.id in config.IMMUNE_ROLE_IDS:
            return True
    return False


def is_immune_only():
    """Permission check: only immune role holders can use this command.
    Non-immune users get timed out for 10 minutes and warned in chat."""
    async def pred(interaction: discord.Interaction):
        if _has_immune_role(interaction.user):
            return True
        # Unauthorized: 10 min timeout (not 1 hour, more proportional)
        duration = datetime.timedelta(minutes=10)
        try:
            await interaction.user.timeout(duration, reason="Unauthorized use of mod commands")
        except Exception:
            pass
        await interaction.response.send_message(
            "You do not have permission to use this command. You have been timed out for 10 minutes.",
            ephemeral=True
        )
        try:
            await interaction.channel.send(
                f"@everyone {interaction.user.mention} attempted to use a moderator command "
                f"without authorization. They have been timed out for 10 minutes."
            )
        except Exception:
            pass
        db.log_action(
            interaction.guild.id, "TIMEOUT (unauthorized cmd)", interaction.user.id,
            "AutoMod", f"Tried to use /{interaction.command.name}"
        )
        return False
    return app_commands.check(pred)


class ModCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    # ── MOD-ONLY COMMANDS ──────────────────────────────────────────

    @app_commands.command(name="warn", description="Warn a member.")
    @app_commands.describe(member="Member", reason="Reason")
    @is_immune_only()
    async def warn(self, i, member: discord.Member, reason: str):
        await i.response.defer(ephemeral=True)
        if moderation._is_immune(member):
            await i.followup.send(f"{member} has an immune role and cannot be warned.", ephemeral=True); return
        total = db.add_warning(i.guild.id, member.id, str(i.user), reason)
        db.log_action(i.guild.id, "WARN", member.id, str(i.user), reason)
        t = await llm.generate(f"Warn member. Reason: {reason}. Warning {total}/{config.WARN_BEFORE_MUTE}. Calm and fair.")
        try: await member.send(t)
        except: pass
        try: await i.channel.send(f"{member.mention} {t}", delete_after=20)
        except: pass
        await i.followup.send(f"Warning #{total} issued to {member}.", ephemeral=True)
        if total >= config.WARN_BEFORE_MUTE:
            await moderation._mute(self.bot, i.guild, member, reason)

    @app_commands.command(name="mute", description="Timeout a member.")
    @app_commands.describe(member="Member", reason="Reason")
    @is_immune_only()
    async def mute(self, i, member: discord.Member, reason: str = "No reason"):
        await i.response.defer(ephemeral=True)
        if moderation._is_immune(member):
            await i.followup.send(f"{member} has an immune role and cannot be muted.", ephemeral=True); return
        await moderation._mute(self.bot, i.guild, member, reason)
        await i.followup.send(f"Timed out {member}.", ephemeral=True)

    @app_commands.command(name="unmute", description="Remove timeout from a member.")
    @is_immune_only()
    async def unmute(self, i, member: discord.Member):
        await i.response.defer(ephemeral=True)
        removed = False
        if member.is_timed_out():
            try:
                await member.timeout(None, reason=f"Untimeout by {i.user}")
                removed = True
            except: pass
        r = i.guild.get_role(config.MUTED_ROLE_ID) or discord.utils.get(i.guild.roles, name="Muted")
        if r and r in member.roles:
            await member.remove_roles(r); removed = True
        if removed:
            db.log_action(i.guild.id, "UNMUTE", member.id, str(i.user), "")
            await i.followup.send(f"Unmuted {member}.", ephemeral=True)
        else:
            await i.followup.send(f"{member} is not muted or timed out.", ephemeral=True)

    @app_commands.command(name="purge", description="Bulk-delete messages.")
    @is_immune_only()
    async def purge(self, i, amount: app_commands.Range[int,1,100], member: discord.Member = None):
        await i.response.defer(ephemeral=True)
        cutoff = discord.utils.utcnow() - datetime.timedelta(days=14)
        def chk(m): return m.created_at >= cutoff and (member is None or m.author.id == member.id)
        try: d = await i.channel.purge(limit=amount, check=chk, bulk=True)
        except: await i.followup.send("No permission.", ephemeral=True); return
        await i.followup.send(f"Deleted {len(d)} message(s).", ephemeral=True)

    @app_commands.command(name="warnings", description="View warnings.")
    @is_immune_only()
    async def warnings(self, i, member: discord.Member):
        rows = db.get_warnings(i.guild.id, member.id)
        if not rows: await i.response.send_message(f"No warnings for {member}.", ephemeral=True); return
        lines = [f"**{member}** ({len(rows)} warnings)"] + [f"{n}. [{r['timestamp'][:10]}] {r['reason']} -- {r['moderator']}" for n,r in enumerate(rows,1)]
        await i.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="clearwarnings", description="Clear warnings.")
    @is_immune_only()
    async def clearwarnings(self, i, member: discord.Member):
        db.clear_warnings(i.guild.id, member.id)
        await i.response.send_message(f"Cleared for {member}.", ephemeral=True)

    @app_commands.command(name="modlog", description="Recent mod actions.")
    @is_immune_only()
    async def modlog(self, i, limit: int = 10):
        rows = db.get_recent_log(i.guild.id, min(limit,20))
        if not rows: await i.response.send_message("No actions.", ephemeral=True); return
        lines = [f"[{r['timestamp'][:16]}] **{r['action']}** user `{r['target_id']}` by {r['moderator']}" for r in rows]
        await i.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="slowmode", description="Set channel slowmode.")
    @is_immune_only()
    async def slowmode(self, i, seconds: app_commands.Range[int,0,21600], channel: discord.TextChannel = None):
        await i.response.defer(ephemeral=True)
        ch = channel or i.channel
        try: await ch.edit(slowmode_delay=seconds)
        except: await i.followup.send("No permission.", ephemeral=True); return
        await i.followup.send(f"Slowmode {'off' if seconds==0 else f'{seconds}s'} in {ch.mention}.", ephemeral=True)

    @app_commands.command(name="lookup", description="Full mod profile.")
    @is_immune_only()
    async def lookup(self, i, member: discord.Member):
        await i.response.defer(ephemeral=True)
        w = db.get_warnings(i.guild.id, member.id)
        n = db.get_user_notes(i.guild.id, member.id)
        wc = len(w)
        if wc == 0: color, st = discord.Color.green(), "Clean"
        elif wc < config.WARN_BEFORE_MUTE: color, st = discord.Color.yellow(), "Cautioned"
        else: color, st = discord.Color.red(), "High Risk"
        e = discord.Embed(title=f"Profile: {member}", color=color)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Standing", value=st); e.add_field(name="Warnings", value=str(wc))
        e.add_field(name="Notes", value=str(len(n)))
        await i.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="guide", description="Channel overview.")
    @is_immune_only()
    async def guide(self, i):
        import welcome as wm
        t = await llm.generate("Give a concise, friendly overview of the server channels.", context=wm.CHANNEL_GUIDE, max_tokens=250)
        await i.response.send_message(t, ephemeral=True)

    # ── CHANNEL IGNORE MANAGEMENT ─────────────────────────────────

    @app_commands.command(name="ignore", description="Make the bot ignore a channel (no replies, no moderation).")
    @app_commands.describe(channel="Channel to ignore. Leave empty for current channel.")
    @is_immune_only()
    async def ignore(self, i, channel: discord.TextChannel = None):
        await i.response.defer(ephemeral=True)
        ch = channel or i.channel
        db.add_ignored_channel(i.guild.id, ch.id, str(i.user))
        await i.followup.send(f"Now ignoring {ch.mention}. I will not reply or moderate there.", ephemeral=True)

    @app_commands.command(name="unignore", description="Make the bot resume replying in a channel.")
    @app_commands.describe(channel="Channel to unignore. Leave empty for current channel.")
    @is_immune_only()
    async def unignore(self, i, channel: discord.TextChannel = None):
        await i.response.defer(ephemeral=True)
        ch = channel or i.channel
        removed = db.remove_ignored_channel(i.guild.id, ch.id)
        if removed:
            await i.followup.send(f"Resumed in {ch.mention}. I'm active there again.", ephemeral=True)
        else:
            await i.followup.send(f"{ch.mention} was not being ignored.", ephemeral=True)

    @app_commands.command(name="ignoredchannels", description="List all ignored channels.")
    @is_immune_only()
    async def ignoredchannels(self, i):
        rows = db.get_ignored_channels(i.guild.id)
        if not rows:
            await i.response.send_message("No channels are being ignored.", ephemeral=True)
            return
        lines = []
        for r in rows:
            ch = i.guild.get_channel(r["channel_id"])
            name = ch.mention if ch else f"Unknown ({r['channel_id']})"
            lines.append(f"{name} (added by {r['added_by']})")
        await i.response.send_message("**Ignored channels:**\n" + "\n".join(lines), ephemeral=True)

    # ── PUBLIC COMMAND: /report ────────────────────────────────────

    @app_commands.command(name="report", description="Report an issue to moderators.")
    @app_commands.describe(reason="What's happening? Describe the issue.")
    async def report(self, i, reason: str):
        await i.response.defer(ephemeral=True)
        role_pings = " ".join(f"<@&{rid}>" for rid in config.IMMUNE_ROLE_IDS)
        e = discord.Embed(
            title="Member Report",
            description=reason,
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        e.add_field(name="Reported by", value=f"{i.user.mention} ({i.user})", inline=True)
        e.add_field(name="Channel", value=i.channel.mention, inline=True)
        log_ch = i.guild.get_channel(config.LOG_CHANNEL_ID)
        if log_ch:
            try: await log_ch.send(f"{role_pings} New report from {i.user.mention}", embed=e)
            except: pass
        try:
            await i.channel.send(
                f"{role_pings} A member has flagged an issue in this channel. Please check.",
                embed=e
            )
        except: pass
        await i.followup.send("Your report has been sent to the moderators. Thank you.", ephemeral=True)
        db.log_action(i.guild.id, "REPORT", i.user.id, str(i.user), reason)

    # ── INTERNAL: note and reactionrole helpers ────────────────────

    async def _note_add(self, i, member, content):
        nid = db.add_note(i.guild.id, member.id, str(i.user), content[:1000])
        await i.response.send_message(f"Note #{nid} added.", ephemeral=True)
    async def _note_list(self, i, member):
        notes = db.get_user_notes(i.guild.id, member.id)
        if not notes: await i.response.send_message(f"No notes for {member}.", ephemeral=True); return
        e = discord.Embed(title=f"Notes for {member} ({len(notes)})", color=discord.Color.blurple())
        for r in notes: e.add_field(name=f"#{r['id']} {r['created_at'][:16]}", value=r["content"], inline=False)
        await i.response.send_message(embed=e, ephemeral=True)
    async def _note_delete(self, i, nid):
        db.delete_note(nid, i.guild.id); await i.response.send_message(f"Note #{nid} deleted.", ephemeral=True)
    async def _rr_setup(self, i, mid_str, emoji, role, desc=""):
        await i.response.defer(ephemeral=True)
        try: mid = int(mid_str)
        except: await i.followup.send("Invalid ID.", ephemeral=True); return
        tm = None
        for ch in i.guild.text_channels:
            try: tm = await ch.fetch_message(mid); break
            except: continue
        if not tm: await i.followup.send("Not found.", ephemeral=True); return
        db.add_reaction_role(i.guild.id, tm.channel.id, mid, emoji.strip(), role.id, desc, str(i.user))
        try: await tm.add_reaction(emoji.strip())
        except: pass
        await i.followup.send(f"Saved. {emoji} assigns **@{role.name}**.", ephemeral=True)
    async def _rr_post(self, i, channel, role, emoji="\u2705", title="Rules Agreement", body="React to agree."):
        await i.response.defer(ephemeral=True)
        e = discord.Embed(title=title, description=body, color=discord.Color.blue())
        e.set_footer(text=f"React {emoji} for @{role.name}")
        try: msg = await channel.send(embed=e)
        except: await i.followup.send("No permission.", ephemeral=True); return
        try: await msg.add_reaction(emoji.strip())
        except: pass
        db.add_reaction_role(i.guild.id, channel.id, msg.id, emoji.strip(), role.id, title, str(i.user))
        await i.followup.send(f"Posted in {channel.mention}. ID: `{msg.id}`", ephemeral=True)
    async def _rr_list(self, i):
        rows = db.get_all_reaction_roles(i.guild.id)
        if not rows: await i.response.send_message("None configured.", ephemeral=True); return
        e = discord.Embed(title=f"Reaction Roles ({len(rows)})", color=discord.Color.blurple())
        for r in rows:
            role = i.guild.get_role(r["role_id"]); ch = i.guild.get_channel(r["channel_id"])
            e.add_field(name=f"{r['emoji']} -> {'@'+role.name if role else '?'}", value=f"{ch.mention if ch else '?'} | `{r['message_id']}`", inline=False)
        await i.response.send_message(embed=e, ephemeral=True)
    async def _rr_remove(self, i, mid_str, emoji=None):
        try: mid = int(mid_str)
        except: await i.response.send_message("Invalid ID.", ephemeral=True); return
        if emoji:
            ok = db.remove_reaction_role(i.guild.id, mid, emoji.strip())
            await i.response.send_message("Removed." if ok else "Not found.", ephemeral=True)
        else:
            c = db.remove_reaction_roles_for_message(i.guild.id, mid)
            await i.response.send_message(f"Removed {c}." if c else "None.", ephemeral=True)


async def setup(bot):
    cog = ModCog(bot); await bot.add_cog(cog)

    # Note subcommands (immune only)
    ng = app_commands.Group(name="note", description="Moderator notes.")
    @ng.command(name="add", description="Add note.")
    @app_commands.describe(member="User", content="Text")
    @is_immune_only()
    async def na(i: discord.Interaction, member: discord.Member, content: str):
        await cog._note_add(i, member, content)
    @ng.command(name="list", description="List notes.")
    @app_commands.describe(member="User")
    @is_immune_only()
    async def nl(i: discord.Interaction, member: discord.Member):
        await cog._note_list(i, member)
    @ng.command(name="delete", description="Delete note.")
    @app_commands.describe(note_id="ID")
    @is_immune_only()
    async def nd(i: discord.Interaction, note_id: int):
        await cog._note_delete(i, note_id)
    bot.tree.add_command(ng)

    # Reaction role subcommands (immune only)
    rg = app_commands.Group(name="reactionrole", description="Reaction roles.")
    @rg.command(name="setup", description="Bind emoji to role on a message.")
    @app_commands.describe(message_id="Message ID", emoji="Emoji", role="Role", description="Note")
    @is_immune_only()
    async def rs(i: discord.Interaction, message_id: str, emoji: str, role: discord.Role, description: str = ""):
        await cog._rr_setup(i, message_id, emoji, role, description)
    @rg.command(name="post", description="Post rules-agreement embed.")
    @is_immune_only()
    async def rp(i: discord.Interaction, channel: discord.TextChannel, role: discord.Role, emoji: str = "\u2705", title: str = "Rules Agreement", body: str = "React to agree."):
        await cog._rr_post(i, channel, role, emoji, title, body)
    @rg.command(name="list", description="List mappings.")
    @is_immune_only()
    async def rl(i: discord.Interaction):
        await cog._rr_list(i)
    @rg.command(name="remove", description="Remove mapping.")
    @is_immune_only()
    async def rr(i: discord.Interaction, message_id: str, emoji: str = None):
        await cog._rr_remove(i, message_id, emoji)
    bot.tree.add_command(rg)

    # Context menu: Report Message (available to everyone)
    @bot.tree.context_menu(name="Report Message")
    async def rmc(i: discord.Interaction, message: discord.Message):
        if message.author.id == i.user.id:
            await i.response.send_message("Can't report yourself.", ephemeral=True); return
        await reports_module.submit_from_context_menu(bot, i, message)

    # Sync commands
    go = discord.Object(id=config.GUILD_ID) if config.GUILD_ID else None
    if go: bot.tree.copy_global_to(guild=go); await bot.tree.sync(guild=go)
    else: await bot.tree.sync()
