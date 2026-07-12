import os

TOKEN = os.getenv("BOT_TOKEN")

_default_admins = "5508686165,6586028596"
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_ID", _default_admins).split(",") if x.strip()]

# Backward-compat: if other code still imports ADMIN_ID expecting a single int,
# keep it pointing at the first admin.
ADMIN_ID = ADMIN_IDS[0]
