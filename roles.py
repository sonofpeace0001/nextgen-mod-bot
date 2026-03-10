"""Auto-role on join + reaction-role system."""
from __future__ import annotations
import discord, config, database as db, logging

log = logging.getLogger("roles")

async def assign_default_role(bot, member):
    if not config.MEMBER_ROLE_ID: return
    role = member.guild.get_role(config.MEMBER_ROLE_ID)
    if role:
        try: await member.add_roles(role, reason="Auto-role on join")
        except Exception as e: log.error(f"Auto-role error: {e}")

async def handle_reaction_add(bot, payload):
    rr = db.get_reaction_role(payload.message_id, str(payload.emoji))
    if not rr: return
    guild = bot.get_guild(payload.guild_id)
    if not guild: return
    member = guild.get_member(payload.user_id)
    role = guild.get_role(rr["role_id"])
    if member and role:
        try: await member.add_roles(role, reason="Reaction role")
        except Exception as e: log.error(f"Reaction role add error: {e}")

async def handle_reaction_remove(bot, payload):
    rr = db.get_reaction_role(payload.message_id, str(payload.emoji))
    if not rr: return
    guild = bot.get_guild(payload.guild_id)
    if not guild: return
    member = guild.get_member(payload.user_id)
    role = guild.get_role(rr["role_id"])
    if member and role:
        try: await member.remove_roles(role, reason="Reaction role removed")
        except Exception as e: log.error(f"Reaction role remove error: {e}")
