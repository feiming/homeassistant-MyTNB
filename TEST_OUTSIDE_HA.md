# Testing the Plugin Outside Home Assistant

You can test the MyTNB integration components without installing them in Home Assistant. This is useful for development and debugging.

## Quick Start

### 1. Test API Client Only (Fastest)

Tests just the API connectivity and data retrieval:

```bash
python test_api.py
```

**What it tests:**
- ✅ Authentication with MyTNB
- ✅ Retrieving sdpudcid
- ✅ Fetching usage data
- ✅ Fetching cost data

### 2. Test Full Integration Logic (Comprehensive)

Tests all components including async operations that simulate Home Assistant behavior:

```bash
python test_integration.py
```

**What it tests:**
- ✅ API client functionality
- ✅ Sensor data extraction logic
- ✅ Config flow validation
- ✅ Async operations (simulating Home Assistant's async executor)

## Test Scripts

### `test_api.py` - Simple API Test

**Purpose:** Quick verification that the API works with your credentials.

**Usage:**
```bash
# Make sure .env file exists with:
# USERNAME=your-email@example.com
# PASSWORD=your-password
# SMARTMETER_URL=https://myaccount.mytnb.com.my/...

python test_api.py
```

**Output:**
- Shows authentication status
- Displays sdpudcid
- Shows latest usage and cost values
- Reports any errors

### `test_integration.py` - Full Integration Test

**Purpose:** Comprehensive test of all integration components, simulating Home Assistant behavior.

**Usage:**
```bash
python test_integration.py
```

**What it does:**
1. Tests API client directly
2. Tests sensor data extraction logic with mock data
3. Tests config flow validation
4. Tests async operations (simulating Home Assistant's `async_add_executor_job`)

**Output:**
- Detailed test results for each component
- Summary of passed/failed tests
- Error details if any tests fail

## Manual Testing

You can also test individual components manually:

### Test API Client

```python
from components.mytnb.api import MyTNBAPI
import os
from dotenv import load_dotenv

load_dotenv()

api = MyTNBAPI(
    os.environ["USERNAME"],
    os.environ["PASSWORD"],
    os.environ["SMARTMETER_URL"],
)

# Authenticate
if api.authenticate():
    print("✅ Authenticated")
    
    # Get sdpudcid
    sdpudcid = api.get_sdpudcid()
    print(f"SDPUDCID: {sdpudcid}")
    
    # Get usage data
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=7)
    
    usage = api.get_data(
        "usage",
        start=start.strftime("%Y-%m-%d+00:00"),
        end=end.strftime("%Y-%m-%d+00:00"),
    )
    print(f"Usage data points: {len(usage.get('data', []))}")
```

### Test Sensor Logic

```python
# Mock data structure
mock_data = {
    "data": [
        {"timestamp": "2025-03-01T00:00:00", "value": 100.5},
        {"timestamp": "2025-03-01T00:30:00", "value": 101.2},
    ]
}

# Extract latest value (same logic as sensor.py)
if isinstance(mock_data, dict) and "data" in mock_data:
    data_points = mock_data["data"]
    if data_points:
        latest = data_points[-1]
        value = float(latest["value"])
        print(f"Latest value: {value}")
```

## Expected Results

### Successful Test Output

```
======================================================================
MyTNB API Test
======================================================================

Username: your-email@example.com
Smart Meter URL: https://myaccount.mytnb.com.my/AccountManagement/SmartMeter/Index/TRIL?caNo=...

[1/4] Testing authentication...
✅ Authentication successful

[2/4] Testing sdpudcid retrieval...
✅ Got sdpudcid: 123456789

[3/4] Testing usage data retrieval...
✅ Got usage data: 336 data points
   Latest value: 1234.56 kWh at 2025-03-07T23:30:00

[4/4] Testing cost data retrieval...
✅ Got cost data: 336 data points
   Latest value: 617.28 MYR at 2025-03-07T23:30:00

======================================================================
✅ All tests passed!
======================================================================
```

## Troubleshooting

### Missing Environment Variables

**Error:** `Missing environment variables!`

**Solution:** Create a `.env` file in the project root:
```bash
USERNAME=your-email@example.com
PASSWORD=your-password
SMARTMETER_URL=https://myaccount.mytnb.com.my/AccountManagement/SmartMeter/Index/TRIL?caNo=...
```

### Authentication Failed

**Error:** `❌ Authentication failed`

**Solutions:**
- Verify credentials work on MyTNB website
- Check username/password in `.env` file
- Ensure Smart Meter URL is complete (includes `?caNo=...`)

### Import Errors

**Error:** `ModuleNotFoundError: No module named 'mytnb'`

**Solution:** The test scripts automatically add `components` to the Python path. If you're running manually, make sure you're in the project root directory.

### No Data Points

**Error:** `Got usage data: 0 data points`

**Possible causes:**
- Date range might be too recent (no data yet)
- Smart meter might not be reporting data
- API might be returning empty results

**Solution:** Try a wider date range or check the MyTNB website for data availability.

## Next Steps

After successful testing:

1. ✅ API works correctly
2. ✅ Data extraction logic works
3. ✅ Ready to install in Home Assistant
4. ✅ Integration should work as expected

If tests pass, you can confidently install the integration in Home Assistant!
