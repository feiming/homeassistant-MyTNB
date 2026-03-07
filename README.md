# MyTNB Integration for Home Assistant

A Home Assistant custom integration to monitor your Tenaga Nasional Berhad (TNB) smart meter energy usage and costs.

## Features

- Monitor energy usage (kWh) from your TNB smart meter
- Monitor energy costs (MYR) 
- Automatic updates every 30 minutes
- Configurable via Home Assistant UI
- Supports HACS installation

## Installation

### HACS (Recommended)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. Go to HACS → Integrations
3. Click the three dots menu (⋮) → Custom repositories
4. Add this repository URL: `https://github.com/feiming/homeassistant-MyTNB`
5. Select category: Integration
6. Click Add
7. Search for "MyTNB" in HACS and install it
8. Restart Home Assistant

### Manual Installation

1. Copy the `mytnb` folder from `custom_components/mytnb` to your Home Assistant `custom_components` directory:
   ```
   <config>/custom_components/mytnb/
   ```
2. Restart Home Assistant
3. Add the integration via Settings → Devices & Services → Add Integration

## Configuration

1. Go to Settings → Devices & Services → Add Integration
2. Search for "Tenaga Nasional" or "MyTNB"
3. Enter your MyTNB account credentials:
   - **Username**: Your MyTNB email address
   - **Password**: Your MyTNB password
   - **Smart Meter URL**: The full URL to your smart meter page (found in your MyTNB account dashboard)
   - **SDPUDCID**: Your smart meter device ID (see instructions below)

## Finding Your Smart Meter URL

1. Log in to [MyTNB](https://myaccount.mytnb.com.my/)
2. Navigate to Account Management → Smart Meter
3. Copy the full URL from your browser's address bar (it should look like: `https://myaccount.mytnb.com.my/AccountManagement/SmartMeter/Index/TRIL?caNo=...`)
4. Paste it into the Smart Meter URL field during setup

## Finding Your SDPUDCID

The SDPUDCID (Smart Device Point Utility Device Customer ID) is a unique identifier for your smart meter. To find it:

### Method 1: Using Browser Developer Tools (Recommended)

1. Log in to [MyTNB](https://myaccount.mytnb.com.my/)
2. Navigate to the Smart Living dashboard: [https://smartliving.myaccount.mytnb.com.my/dashboard](https://smartliving.myaccount.mytnb.com.my/dashboard)
3. Open your browser's Developer Tools:
   - **Chrome/Edge**: Press `F12` or `Ctrl+Shift+I` (Windows/Linux) / `Cmd+Option+I` (Mac)
   - **Firefox**: Press `F12` or `Ctrl+Shift+I` (Windows/Linux) / `Cmd+Option+I` (Mac)
   - **Safari**: Enable Developer menu first, then press `Cmd+Option+I`
4. Go to the **Network** tab
5. Refresh the page (`F5` or `Ctrl+R` / `Cmd+R`)
6. Look for the `dashboard` request in the network list
7. Click on it and go to the **Response** or **Preview** tab
8. Search for `"sdpudcid"` (without quotes) in the response
9. You should see something like: `"sdpudcid":"1234567890123456"`
10. Copy the numeric value (the digits between the quotes) - this is your SDPUDCID

### Method 2: View Page Source

1. Log in to [MyTNB](https://myaccount.mytnb.com.my/)
2. Navigate to the Smart Living dashboard: [https://smartliving.myaccount.mytnb.com.my/dashboard](https://smartliving.myaccount.mytnb.com.my/dashboard)
3. Right-click on the page and select **View Page Source** (or press `Ctrl+U` / `Cmd+Option+U`)
4. Press `Ctrl+F` / `Cmd+F` to open the search box
5. Search for `sdpudcid` (without quotes)
6. You should find a line like: `"sdpudcid":"1234567890123456"`
7. Copy the numeric value (the digits between the quotes) - this is your SDPUDCID

**Note**: The SDPUDCID is typically a long numeric string (12-16 digits). Make sure to copy the complete value.

## Sensors

The integration creates the following sensors:

- **`sensor.energy_usage`**: Total energy consumption in kWh (state class: total_increasing)
- **`sensor.energy_cost`**: Total energy cost in MYR (state class: total_increasing)

Both sensors:
- Update every 30 minutes
- Show data from the last 7 days
- Include attributes: `sdpudcid`, `metric`, `view`, `granularity`

## Troubleshooting

### Authentication Errors

If you encounter authentication errors:
- Verify your username and password are correct
- Ensure your Smart Meter URL is the complete URL including all query parameters
- Verify your SDPUDCID is correct (should be a numeric string, typically 12-16 digits)
- Check that you can log in to the MyTNB website manually
- Try logging out and back in to MyTNB website to refresh your session

### Connection Errors

If you see connection errors:
- Check your internet connection
- Verify the Smart Meter URL is correct and accessible
- Check Home Assistant logs for more details

### No Data

If sensors show "unknown" or no data:
- Wait a few minutes for the first update (up to 30 minutes)
- Check Home Assistant logs for API errors
- Verify your smart meter is active and reporting data on the MyTNB website

## Development

This integration was converted from a standalone Python script (`get_smartmeter_data.py`) into a Home Assistant custom integration.

### Project Structure

```
homeassistant-MyTNB/
├── custom_components/
│   └── mytnb/
│       ├── __init__.py          # Integration setup
│       ├── manifest.json         # Integration metadata
│       ├── config_flow.py       # UI configuration flow
│       ├── sensor.py            # Sensor platform
│       ├── api.py               # MyTNB API client
│       ├── const.py             # Constants
│       ├── strings.json         # UI strings
│       └── translations/        # Translation files
├── hacs.json                    # HACS metadata
├── info.md                      # HACS info page
└── README.md                    # This file
```

## Support

For issues and feature requests, please visit the [GitHub repository](https://github.com/feiming/homeassistant-MyTNB).

## License

See LICENSE file for details.
