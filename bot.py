"""NEXTGEN MOD -- Discord Moderation Agent with conversational chat and ticket support."""
from __future__ import annotations
import asyncio, logging, traceback, sys, re
import discord
from discord.ext import commands
import config, database as db, moderation, welcome, appeals, reports, roles, chat, tickets
import tutor, prompthelper, prompts, retention, social, xp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger("mod-agent")

# Narrowed intents: only what the bot actually uses. Presence is intentionally OFF.
# members + message_content are privileged and must be enabled in the Developer Portal.
# dm_messages is required for ban appeals and welcome-DM replies; reactions for reaction roles.
intents = discord.Intents.none()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.guild_messages = True
intents.dm_messages = True
intents.reactions = True

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
            await xp.restore_pending_views(self)
        except Exception as e: log.error(f"Restore error: {e}")
    async def on_ready(self):
        log.info(f"=== ONLINE as {self.user} (id={self.user.id}) ===")
        log.info(f"Guilds: {[g.name for g in self.guilds]}")
        log.info(f"Ignored channels: {config.IGNORED_CHANNEL_IDS}")
        for g in self.guilds:
            me = g.me
            log.info(f"Guild '{g.name}': administrator={me.guild_permissions.administrator}")
        prompts.start(self)  # daily prompt scheduler (no-op if PROMPT_CHANNEL_ID unset)
        social.start(self)   # daily social-media reminder (no-op if unset)
        await retention.seed_and_start(self)  # auto-kick scheduler (seeds activity first)

    async def on_message(self, message):
        log.info(f"MSG: #{getattr(message.channel,'name','DM')} | {message.author} | {message.content[:100]!r}")

        # 1. Skip bot messages always
        if message.author.bot:
            return

        # 2. DMs: welcome-DM replies first, otherwise the appeal handler (unchanged).
        if message.guild is None:
            if await welcome.handle_welcome_reply(self, message):
                return
            await appeals.handle_dm(self, message)
            return

        # 2b. Activity tracking for the retention/auto-kick check (any channel counts).
        try:
            db.record_message(message.guild.id, message.author.id)
        except Exception as e:
            log.error(f"activity tracking failed: {e}")

        # 3. FOUNDER COMMANDS: always process first, even in ignored channels
        if moderation._is_founder(message.author):
            handled = await self._handle_founder_command(message)
            if handled:
                return

        # 4. Ignored channels: do absolutely nothing
        if moderation._is_ignored_channel(message.channel.id):
            return

        # 5. Cancel any pending delayed replies (someone is talking)
        await chat.cancel_for_channel(message.channel.id)

        # 6. Process prefix commands
        await self.process_commands(message)

        # 7. TICKET CHANNELS: light moderation always; auto-reply only if not disabled.
        if tickets.is_ticket_channel(message.channel):
            if not config.DISABLE_TICKET_REPLIES:
                await tickets.handle_ticket_message(self, message)
            # Light moderation (airdrop/phishing/spam) still runs even when replies are off.
            await moderation.handle_message_light(self, message)
            return

        # 8. TUTOR / PROMPT-HELPER: fire when mentioned, in the help channel, OR when the
        #    member is mid prompt-drafting (so their answers continue without re-tagging).
        #    If either handles it, skip chat.py (and the delayed reply) to avoid double-replying.
        mentioned = self.user.mentioned_in(message) and not message.mention_everyone
        in_help = message.channel.id == config.CHANNEL_MAP.get("help", 0)
        drafting = prompthelper.has_active_session(message.channel.id, message.author.id)
        if mentioned or in_help or drafting:
            # Mid-session: go straight to the prompt-helper (don't let the tutor hijack answers).
            handled = False
            if not drafting:
                handled = await tutor.maybe_handle(self, message)
            if not handled:
                handled = await prompthelper.maybe_handle(self, message)
            if handled:
                await moderation.handle_message(self, message)
                return

        # 9. BOT MENTIONED anywhere: teach / answer as a senior prompt engineer.
        if mentioned:
            log.info(f"BOT MENTIONED by {message.author}")
            await chat.handle_teach(self, message)
            await moderation.handle_message(self, message)
            return

        # 10. Question in help channel: welcome handler
        if await welcome.answer_question(self, message):
            await moderation.handle_message(self, message)
            return

        # 11. Normal message: moderation + delayed chat reply
        await moderation.handle_message(self, message)
        await chat.schedule_delayed_reply(self, message)

    async def _handle_founder_command(self, message):
        """Process natural language commands from the founder (SON OF PEACE)."""
        content = message.content

        # "Don't reply in this channel" / "ignore this channel"
        if _IGNORE_CHANNEL_RE.search(content):
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
        # Give them a fresh, empty activity cycle so the retention/auto-kick check never
        # counts anything from before they even joined.
        try:
            db.seed_cycle(member.guild.id, member.id)
        except Exception as e:
            log.error(f"activity seed on join failed: {e}")
        # Welcome DM with one onboarding question; fall back to public greet if DMs are closed.
        sent = await welcome.send_welcome_dm(self, member)
        if not sent:
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
