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


# ── TravOps Branding ──────────────────────────────────────────────────────────

TRAVOPS_FOOTER_ICON = "https://travops.online/static/img/logo32.png"
TRAVOPS_FOOTER_SUFFIX = " · travops.online"


def travops_footer(text: str = "") -> dict:
    """Return kwargs for embed.set_footer() with TravOps branding appended."""
    combined = (text + TRAVOPS_FOOTER_SUFFIX).strip(" ·")
    # Ensure suffix is always at end, clean up double ·
    if not combined.endswith("travops.online"):
        combined = combined + TRAVOPS_FOOTER_SUFFIX
    return {"text": combined, "icon_url": TRAVOPS_FOOTER_ICON}
