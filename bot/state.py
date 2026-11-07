"""Process-wide mutable runtime state shared between the Telegram loop and Flask.

Always access these as attributes (``state.tg_client``) so reassignment is visible everywhere."""

# Globals for async bridge
telethon_loop = None
tg_client = None
current_api_id = None
current_api_hash = None
reset_requested = False
handlers_registered = False

background_job_semaphore = None
