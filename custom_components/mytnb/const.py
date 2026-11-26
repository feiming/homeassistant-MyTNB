"""Constants for the Tenaga Nasional integration."""

from datetime import timedelta

DOMAIN = "mytnb"

CONF_SMARTMETER_URL = "smartmeter_url"
CONF_UPDATE_INTERVAL_HOURS = "update_interval_hours"

# TNB publishes data roughly two days late, so frequent polling gains
# nothing; four fetches a day picks up each newly published batch while
# keeping login traffic low. Configurable via the integration's options.
DEFAULT_UPDATE_INTERVAL_HOURS = 6

# How far back each refresh looks; statistics imports are incremental, so
# this only needs to comfortably cover TNB's ~2-day data publication lag.
FETCH_WINDOW = timedelta(days=30)

CURRENCY_MYR = "MYR"

# Latest-bill fetching. Bills are issued monthly, so a daily check picks up a
# new one within a day; users can check less often or turn it off.
CONF_BILL_ENABLED = "bill_enabled"
CONF_BILL_INTERVAL_DAYS = "bill_interval_days"
CONF_BILL_TIME = "bill_time"
DEFAULT_BILL_ENABLED = True
DEFAULT_BILL_INTERVAL_DAYS = 1
DEFAULT_BILL_TIME = "09:00:00"
