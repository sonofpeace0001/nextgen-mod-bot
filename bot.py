"""NEXTGEN MOD -- Discord Moderation Agent with conversational chat and ticket support."""
from __future__ import annotations
import asyncio, logging, traceback, sys
import discord
from discord.ext import commands
import config, database as db, moderation, welcome, appeals, reports, roles, chat, tickets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger("mod-agent")
intents = discord.Intents.all()

class ModerationBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!mod ", intents=intents, help_command=None)
    async def setup_hook(self):
        db.init_db(); log.info("Database initialised.")
        await self.load_extension("commands_cog"); log.info("Commands cog loaded.")
        self.loop.create_task(self._restore())
    async def _restore(self):
        await self.wait_until_ready()
        try:
            await appeals.restore_pending_views(self)
            await reports.restore_pending_views(self)
        except Exception as e: log.error(f"Restore error: {e}")
    async def on_ready(self):
        log.info(f"=== ONLINE as {self.user} (id={self.user.id}) ===")
        log.info(f"Guilds: {[g.name for g in self.guilds]}")
        for g in self.guilds:
            me = g.me
            log.info(f"Guild '{g.name}': administrator={me.guild_permissions.administrator}")
    async def on_message(self, message):
        log.info(f"MSG: #{getattr(message.channel,'name','DM')} | {message.author} | {message.content[:100]!r}")
        if message.author.bot: return
        if message.guild is None:
            await appeals.handle_dm(self, message); return

        # Completely ignore messages in ignored channels (no replies, no moderation, nothing)
        if moderation._is_ignored_channel(message.channel.id):
            return

        await chat.cancel_for_channel(message.channel.id)
        await self.process_commands(message)

        # Check if this is a ticket channel first
        if tickets.is_ticket_channel(message.channel):
            await tickets.handle_ticket_message(self, message)
            await moderation.handle_message(self, message)
            return

        if self.user.mentioned_in(message) and not message.mention_everyone:
            log.info(f"BOT MENTIONED by {message.author}")
            handled = await welcome.answer_question(self, message)
            if not handled: await chat.handle_mention(self, message)
            await moderation.handle_message(self, message)
            return
        handled = await welcome.answer_question(self, message)
        if handled: return
        await moderation.handle_message(self, message)
        await chat.schedule_delayed_reply(self, message)
    async def on_message_edit(self, before, after):
        if after.author.bot or after.guild is None: return
        if moderation._is_ignored_channel(after.channel.id): return
        await moderation.handle_message(self, after)
    async def on_member_join(self, member):
        log.info(f"MEMBER JOIN: {member} ({member.id})")
        await roles.assign_default_role(self, member)
        await welcome.greet_member(self, member)
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.user.id: return
        await roles.handle_reaction_add(self, payload)
    async def on_raw_reaction_remove(self, payload):
        if payload.user_id == self.user.id: return
        await roles.handle_reaction_remove(self, payload)
    async def on_error(self, event_method, *args, **kwargs):
        log.error(f"Error in {event_method}:\n{traceback.format_exc()}")

def main():
    bot = ModerationBot()
    if not config.BOT_TOKEN: raise RuntimeError("DISCORD_BOT_TOKEN not set")
    log.info("Starting bot...")
    bot.run(config.BOT_TOKEN, log_handler=None)

if __name__ == "__main__":
    main()
