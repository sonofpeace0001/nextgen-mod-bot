"""NEXTGEN MOD -- Discord Moderation Agent with conversational chat and ticket support."""
from __future__ import annotations
import asyncio, logging, traceback, sys, re
import discord
from discord.ext import commands
import config, database as db, moderation, welcome, appeals, reports, roles, chat, tickets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger("mod-agent")
intents = discord.Intents.all()

# Patterns for founder commands (natural language)
_IGNORE_CHANNEL_RE = re.compile(
    r"(don.?t reply|stop replying|ignore|stay out of|leave|be quiet|shut up|no replies?)\s*(in\s*)?(this\s*channel|here|<#\d+>)",
    re.IGNORECASE
)
_UNIGNORE_CHANNEL_RE = re.compile(
    r"(reply|start replying|unignore|come back|resume|you can reply)\s*(in\s*)?(this\s*channel|here|<#\d+>)",
    re.IGNORECASE
)

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
        log.info(f"Ignored channels: {config.IGNORED_CHANNEL_IDS}")
        for g in self.guilds:
            me = g.me
            log.info(f"Guild '{g.name}': administrator={me.guild_permissions.administrator}")

    async def on_message(self, message):
        log.info(f"MSG: #{getattr(message.channel,'name','DM')} | {message.author} | {message.content[:100]!r}")
        if message.author.bot: return
        if message.guild is None:
            await appeals.handle_dm(self, message); return

        # FOUNDER COMMANDS: always process, even in ignored channels
        if moderation._is_founder(message.author):
            handled = await self._handle_founder_command(message)
            if handled:
                return

        # Completely ignore messages in ignored channels
        if moderation._is_ignored_channel(message.channel.id):
            return

        await chat.cancel_for_channel(message.channel.id)
        await self.process_commands(message)

        # Airdrop scam check runs everywhere (except ignored channels, handled above)
        # moderation.handle_message already checks for airdrop patterns

        # Ticket channels
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

    async def _handle_founder_command(self, message):
        """Process natural language commands from the founder (SON OF PEACE)."""
        content = message.content

        # "Don't reply in this channel" / "ignore this channel"
        if _IGNORE_CHANNEL_RE.search(content):
            # Check if a specific channel is mentioned
            channel_mentions = message.channel_mentions
            if channel_mentions:
                for ch in channel_mentions:
                    db.add_ignored_channel(message.guild.id, ch.id, str(message.author))
                    log.info(f"Founder ignored channel: #{ch.name} ({ch.id})")
                names = ", ".join(f"#{ch.name}" for ch in channel_mentions)
                await message.reply(f"Got it. I will no longer reply in {names}.", mention_author=False)
            else:
                db.add_ignored_channel(message.guild.id, message.channel.id, str(message.author))
                log.info(f"Founder ignored channel: #{message.channel.name} ({message.channel.id})")
                await message.reply("Got it. I will no longer reply in this channel.", mention_author=False)
            return True

        # "Reply in this channel" / "unignore this channel"
        if _UNIGNORE_CHANNEL_RE.search(content):
            channel_mentions = message.channel_mentions
            if channel_mentions:
                for ch in channel_mentions:
                    db.remove_ignored_channel(message.guild.id, ch.id)
                    log.info(f"Founder unignored channel: #{ch.name} ({ch.id})")
                names = ", ".join(f"#{ch.name}" for ch in channel_mentions)
                await message.reply(f"Got it. I'm back in {names}.", mention_author=False)
            else:
                db.remove_ignored_channel(message.guild.id, message.channel.id)
                log.info(f"Founder unignored channel: #{message.channel.name} ({message.channel.id})")
                await message.reply("Got it. I'm back in this channel.", mention_author=False)
            return True

        return False

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
