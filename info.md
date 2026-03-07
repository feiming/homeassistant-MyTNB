# MyTNB Integration for Home Assistant

This integration allows you to monitor your Tenaga Nasional Berhad (TNB) smart meter energy usage and costs in Home Assistant.

## Features

- Monitor energy usage (kWh)
- Monitor energy costs (MYR)
- Automatic updates every 30 minutes
- Configurable via UI

## Installation

### HACS (Recommended)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. Go to HACS → Integrations
3. Click the three dots menu → Custom repositories
4. Add this repository URL
5. Search for "MyTNB" and install it
6. Restart Home Assistant

### Manual Installation

1. Copy the `mytnb` folder from `custom_components` to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Add the integration via Settings → Devices & Services → Add Integration

## Configuration

1. Go to Settings → Devices & Services → Add Integration
2. Search for "Tenaga Nasional" or "MyTNB"
3. Enter your MyTNB account credentials:
   - **Username**: Your MyTNB email address
   - **Password**: Your MyTNB password
   - **Smart Meter URL**: The full URL to your smart meter page (found in your MyTNB account dashboard)

## Finding Your Smart Meter URL

1. Log in to [MyTNB](https://myaccount.mytnb.com.my/)
2. Navigate to Account Management → Smart Meter
3. Copy the full URL from your browser's address bar
4. Paste it into the Smart Meter URL field during setup

## Sensors

The integration creates the following sensors:

- **Energy Usage**: Total energy consumption in kWh
- **Energy Cost**: Total energy cost in MYR

Both sensors update every 30 minutes and show data from the last 7 days.

## Troubleshooting

If you encounter authentication errors:
- Verify your username and password are correct
- Ensure your Smart Meter URL is the complete URL including the query parameters
- Check that you can log in to the MyTNB website manually

## Support

For issues and feature requests, please visit the [GitHub repository](https://github.com/feiming/homeassistant-MyTNB).
