import database

PREMIUM_STATUSES = ("active", "trialing")


async def require_premium(interaction) -> bool:
    """Returns True if guild has premium. Sends ephemeral error if not."""
    status = await database.get_subscription_status(str(interaction.guild_id))
    if status not in PREMIUM_STATUSES:
        await interaction.response.send_message(
            "⚠️ **TravOps Pro erforderlich**\n"
            "Diese Funktion ist nur mit einem aktiven Abonnement verfügbar.\n"
            "➡️ Upgrade unter: https://travops.online/dashboard",
            ephemeral=True,
        )
        return False
    return True
