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

# Latest-bill fetching. Bills are issued monthly: from a chosen day of the
# month (clamped to its last day) the bill is checked daily until a bill dated
# no more than NEW_BILL_GRACE before that day is in, then not until next month.
CONF_BILL_ENABLED = "bill_enabled"
CONF_BILL_DAY = "bill_day"
CONF_BILL_TIME = "bill_time"
DEFAULT_BILL_ENABLED = True
DEFAULT_BILL_DAY = 1
NEW_BILL_GRACE = timedelta(days=7)
DEFAULT_BILL_TIME = "09:00:00"
