"""Constants for the Tenaga Nasional integration."""

from datetime import timedelta

DOMAIN = "mytnb"

CONF_SMARTMETER_URL = "smartmeter_url"

# TNB publishes data roughly two days late, so frequent polling gains
# nothing; four fetches a day picks up each newly published batch while
# keeping login traffic low.
UPDATE_INTERVAL = timedelta(hours=6)

# How far back each refresh looks; statistics imports are incremental, so
# this only needs to comfortably cover TNB's ~2-day data publication lag.
FETCH_WINDOW = timedelta(days=30)

CURRENCY_MYR = "MYR"
