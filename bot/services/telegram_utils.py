"""Telegram send helpers."""

async def safe_send_message(client, target, message):
    """Sends a Telegram message to target entity, automatically handling cache misses
    by fetching dialogs if the entity isn't cached yet.
    """
    if not client or not target:
        return

    target_entity = int(target) if str(target).lstrip('-').isdigit() else target

    try:
        # Try to send message directly using current entity cache
        await client.send_message(target_entity, message)
    except Exception as e:
        err_str = str(e).lower()
        if "not registered" in err_str or "could not find" in err_str or "invalid" in err_str or "chat_id_invalid" in err_str:
            print(f"[TELEGRAM SEND] Target {target} not found in entity cache. Fetching dialogs to reload cache...", flush=True)
            try:
                # Force loading dialogs to populate Telethon's internal cache
                await client.get_dialogs(limit=100)
                # Retry sending
                await client.send_message(target_entity, message)
                print(f"[TELEGRAM SEND] Successfully sent message to {target} after reloading cache.", flush=True)
            except Exception as retry_err:
                print(f"[TELEGRAM SEND] Retry sending failed: {retry_err}", flush=True)
                raise retry_err
        else:
            raise e


def normalize_channel_id(raw_id):
    """Strip the Telegram -100 prefix to get the bare channel ID for comparison.
    Telethon's chat.id returns bare IDs, but the dashboard may store the full -100... form.
    We normalize both sides so comparison always works."""
    s = str(raw_id).strip()
    # Remove leading minus
    if s.startswith('-'):
        s = s[1:]
    # Remove the '100' prefix that Telegram uses for channels/supergroups
    if s.startswith('100') and len(s) > 10:
        s = s[3:]
    return s
