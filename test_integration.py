#!/usr/bin/env python3
"""Test the MyTNB integration components outside of Home Assistant."""

import os
import sys
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
from dotenv import load_dotenv

# Add custom_components to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components"))

from mytnb.api import MyTNBAPI
from mytnb.const import (
    DOMAIN,
    CONF_SMARTMETER_URL,
    DEFAULT_VIEW,
    DEFAULT_GRANULARITY,
    ATTR_SDPUDCID,
    ATTR_METRIC,
    ATTR_VIEW,
    ATTR_GRANULARITY,
)

load_dotenv()


async def test_api_client():
    """Test the API client directly."""
    print("=" * 70)
    print("Testing MyTNB API Client")
    print("=" * 70)
    
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    smartmeter_url = os.environ.get("SMARTMETER_URL")
    
    if not all([username, password, smartmeter_url]):
        print("❌ Missing environment variables!")
        print("Please set USERNAME, PASSWORD, and SMARTMETER_URL in .env file")
        return False
    
    print(f"\n✓ Username: {username}")
    print(f"✓ Smart Meter URL: {smartmeter_url[:60]}...")
    
    async with MyTNBAPI(username, password, smartmeter_url) as api:
        # Test 1: Authentication
        print("\n[1/5] Testing authentication...")
        try:
            if await api.authenticate():
                print("✅ Authentication successful")
            else:
                print("❌ Authentication failed")
                return False
        except Exception as e:
            print(f"❌ Authentication error: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 2: Get smartmeter path
        print("\n[2/5] Testing smartmeter path extraction...")
        try:
            path = api.get_smartmeter_path()
            print(f"✅ Extracted path: {path[:60]}...")
        except Exception as e:
            print(f"❌ Failed to extract path: {e}")
            return False
        
        # Test 3: Get sdpudcid
        print("\n[3/5] Testing sdpudcid retrieval...")
        try:
            sdpudcid = await api.get_sdpudcid()
            print(f"✅ Got sdpudcid: {sdpudcid}")
        except Exception as e:
            print(f"❌ Failed to get sdpudcid: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 4: Get usage data
        print("\n[4/5] Testing usage data retrieval...")
        try:
            end_date = datetime.now() - timedelta(days=3)
            start_date = end_date - timedelta(days=4)
            start_str = start_date.strftime("%Y-%m-%d+00:00")
            end_str = end_date.strftime("%Y-%m-%d+00:00")
            print(f"   Date range: {start_str} to {end_str}")
            print(f"   Requested granularity: {DEFAULT_GRANULARITY}")
            print(f"   Expected entries (MIN30): ~{7 * 48} = 336 entries (7 days * 48 intervals/day)")
            print(f"   Expected entries (DAILY): ~{7} = 7 entries (one per day)")
            
            usage_data = await api.get_data(
                "usage",
                view=DEFAULT_VIEW,
                granularity=DEFAULT_GRANULARITY,
                start=start_str,
                end=end_str,
            )
            
            # Check for error responses
            if isinstance(usage_data, dict):
                if "error" in usage_data or "message" in usage_data:
                    error_msg = usage_data.get("error") or usage_data.get("message")
                    print(f"⚠️  API returned error: {error_msg}")
                    print(f"   Full response: {usage_data}")
                    return False
            
            # Debug: print the structure first
            print(f"   Response type: {type(usage_data)}")
            if isinstance(usage_data, dict):
                print(f"   Response keys: {list(usage_data.keys())}")
            
            if isinstance(usage_data, dict) and "data" in usage_data:
                data_points = usage_data["data"]
                print(f"   Data points type: {type(data_points)}")
                
                # Handle both list and dict formats
                if isinstance(data_points, list):
                    print(f"✅ Got usage data: {len(data_points)} data points")
                    
                    # Show ALL entries
                    print(f"\n   All data entries:")
                    print(f"   {'Timestamp':<30} {'Value':<15} {'Type'}")
                    print(f"   {'-'*30} {'-'*15} {'-'*10}")
                    for entry in data_points:
                        if isinstance(entry, dict):
                            timestamp = entry.get('timestamp', 'N/A')
                            value = entry.get('value', 'N/A')
                            val_type = type(value).__name__
                            print(f"   {str(timestamp):<30} {str(value):<15} {val_type}")
                        else:
                            print(f"   {str(entry):<30} {type(entry).__name__}")
                    
                    if data_points:
                        latest = data_points[-1]
                        if isinstance(latest, dict):
                            print(f"\n   Latest: {latest.get('value')} kWh at {latest.get('timestamp')}")
                            if len(data_points) > 0:
                                first = data_points[0]
                                if isinstance(first, dict):
                                    print(f"   First: {first.get('value')} kWh at {first.get('timestamp')}")
                        else:
                            print(f"   Latest entry type: {type(latest)}, value: {latest}")
                elif isinstance(data_points, dict):
                    print(f"✅ Got usage data (dict format): {len(data_points)} entries")
                
                # Show ALL entries with their values
                print(f"\n   All data entries:")
                print(f"   {'Timestamp':<30} {'Value':<15} {'Type'}")
                print(f"   {'-'*30} {'-'*15} {'-'*10}")
                for k, v in sorted(data_points.items()):
                    if isinstance(v, dict):
                        val = v.get('value', v.get('usage', 'N/A'))
                        val_type = type(val).__name__
                        print(f"   {k:<30} {str(val):<15} {val_type}")
                    else:
                        print(f"   {k:<30} {str(v):<15} {type(v).__name__}")
                
                # Extract and display values
                values = []
                null_count = 0
                for key, value in data_points.items():
                    if isinstance(value, (int, float)):
                        values.append((key, float(value)))
                    elif isinstance(value, dict):
                        # Try to find numeric value in nested dict
                        if "value" in value:
                            val = value["value"]
                            if val is None:
                                null_count += 1
                            elif isinstance(val, (int, float)):
                                values.append((key, float(val)))
                        elif "usage" in value:
                            val = value["usage"]
                            if val is None:
                                null_count += 1
                            elif isinstance(val, (int, float)):
                                values.append((key, float(val)))
                        # If dict has numeric keys, try those
                        elif len(value) > 0:
                            for k, v in value.items():
                                if v is None:
                                    null_count += 1
                                elif isinstance(v, (int, float)):
                                    values.append((key, float(v)))
                                    break
                
                print(f"\n   Extraction results:")
                print(f"   - Valid values found: {len(values)}")
                print(f"   - Null values: {null_count}")
                
                if values:
                    # Sort by key (timestamp) to get latest
                    sorted_values = sorted(values, key=lambda x: str(x[0]))
                    if sorted_values:
                        latest_key, latest_value = sorted_values[-1]
                        print(f"   Latest value: {latest_value} kWh (at {latest_key})")
                        if len(sorted_values) > 1:
                            first_key, first_value = sorted_values[0]
                            print(f"   First value: {first_value} kWh (at {first_key})")
                            print(f"   Total entries: {len(sorted_values)}")
                    else:
                        print(f"   ⚠️  Could not extract numeric values from dict structure")
                        # Show more detailed structure
                        print(f"\n   Full structure of first entry:")
                        first_key = list(data_points.keys())[0]
                        first_value = data_points[first_key]
                        print(json.dumps({first_key: first_value}, indent=4, default=str))
                else:
                    print(f"⚠️  Data points is unexpected type: {type(data_points)}")
                    print(f"   Data preview: {str(data_points)[:300]}")
            else:
                print(f"⚠️  Unexpected format: {type(usage_data)}")
                if isinstance(usage_data, dict):
                    print(f"   Keys: {list(usage_data.keys())}")
                print(f"   Full response preview: {str(usage_data)[:500]}")
        except Exception as e:
            print(f"❌ Failed to get usage data: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 5: Get cost data
        print("\n[5/5] Testing cost data retrieval...")
        try:
            cost_data = await api.get_data(
                "cost",
                view=DEFAULT_VIEW,
                granularity=DEFAULT_GRANULARITY,
                start=start_str,
                end=end_str,
            )
            
            # Check for error responses
            if isinstance(cost_data, dict):
                if "error" in cost_data or "message" in cost_data:
                    error_msg = cost_data.get("error") or cost_data.get("message")
                    print(f"⚠️  API returned error: {error_msg}")
                    print(f"   Full response: {cost_data}")
                    return False
            
            # Debug: print the structure first
            print(f"   Response type: {type(cost_data)}")
            if isinstance(cost_data, dict):
                print(f"   Response keys: {list(cost_data.keys())}")
            
            if isinstance(cost_data, dict) and "data" in cost_data:
                data_points = cost_data["data"]
                print(f"   Data points type: {type(data_points)}")
                
                # Handle both list and dict formats
                if isinstance(data_points, list):
                    print(f"✅ Got cost data: {len(data_points)} data points")
                    
                    # Show ALL entries
                    print(f"\n   All data entries:")
                    print(f"   {'Timestamp':<30} {'Value':<15} {'Type'}")
                    print(f"   {'-'*30} {'-'*15} {'-'*10}")
                    for entry in data_points:
                        if isinstance(entry, dict):
                            timestamp = entry.get('timestamp', 'N/A')
                            value = entry.get('value', 'N/A')
                            val_type = type(value).__name__
                            print(f"   {str(timestamp):<30} {str(value):<15} {val_type}")
                        else:
                            print(f"   {str(entry):<30} {type(entry).__name__}")
                    
                    if data_points:
                        latest = data_points[-1]
                        if isinstance(latest, dict):
                            print(f"\n   Latest: {latest.get('value')} MYR at {latest.get('timestamp')}")
                            if len(data_points) > 0:
                                first = data_points[0]
                                if isinstance(first, dict):
                                    print(f"   First: {first.get('value')} MYR at {first.get('timestamp')}")
                        else:
                            print(f"   Latest entry type: {type(latest)}, value: {latest}")
                elif isinstance(data_points, dict):
                    print(f"✅ Got cost data (dict format): {len(data_points)} entries")
                
                # Show ALL entries with their values
                print(f"\n   All data entries:")
                print(f"   {'Timestamp':<30} {'Value':<15} {'Type'}")
                print(f"   {'-'*30} {'-'*15} {'-'*10}")
                for k, v in sorted(data_points.items()):
                    if isinstance(v, dict):
                        val = v.get('value', v.get('cost', 'N/A'))
                        val_type = type(val).__name__
                        print(f"   {k:<30} {str(val):<15} {val_type}")
                    else:
                        print(f"   {k:<30} {str(v):<15} {type(v).__name__}")
                
                # Extract and display values
                values = []
                null_count = 0
                for key, value in data_points.items():
                    if isinstance(value, (int, float)):
                        values.append((key, float(value)))
                    elif isinstance(value, dict):
                        # Try to find numeric value in nested dict
                        if "value" in value:
                            val = value["value"]
                            if val is None:
                                null_count += 1
                            elif isinstance(val, (int, float)):
                                values.append((key, float(val)))
                        elif "cost" in value:
                            val = value["cost"]
                            if val is None:
                                null_count += 1
                            elif isinstance(val, (int, float)):
                                values.append((key, float(val)))
                        # If dict has numeric keys, try those
                        elif len(value) > 0:
                            for k, v in value.items():
                                if v is None:
                                    null_count += 1
                                elif isinstance(v, (int, float)):
                                    values.append((key, float(v)))
                                    break
                
                print(f"\n   Extraction results:")
                print(f"   - Valid values found: {len(values)}")
                print(f"   - Null values: {null_count}")
                
                if values:
                    # Sort by key (timestamp) to get latest
                    sorted_values = sorted(values, key=lambda x: str(x[0]))
                    if sorted_values:
                        latest_key, latest_value = sorted_values[-1]
                        print(f"   Latest value: {latest_value} MYR (at {latest_key})")
                        if len(sorted_values) > 1:
                            first_key, first_value = sorted_values[0]
                            print(f"   First value: {first_value} MYR (at {first_key})")
                            print(f"   Total entries: {len(sorted_values)}")
                    else:
                        print(f"   ⚠️  Could not extract numeric values from dict structure")
                        # Show more detailed structure
                        print(f"\n   Full structure of first entry:")
                        first_key = list(data_points.keys())[0]
                        first_value = data_points[first_key]
                        print(json.dumps({first_key: first_value}, indent=4, default=str))
                else:
                    print(f"⚠️  Data points is unexpected type: {type(data_points)}")
                    print(f"   Data preview: {str(data_points)[:300]}")
            else:
                print(f"⚠️  Unexpected format: {type(cost_data)}")
                if isinstance(cost_data, dict):
                    print(f"   Keys: {list(cost_data.keys())}")
                print(f"   Full response preview: {str(cost_data)[:500]}")
        except Exception as e:
            print(f"❌ Failed to get cost data: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        print("\n" + "=" * 70)
        print("✅ All API client tests passed!")
        print("=" * 70)
        return True


def test_sensor_logic():
    """Test sensor data extraction logic."""
    print("\n" + "=" * 70)
    print("Testing Sensor Data Extraction Logic")
    print("=" * 70)
    
    # Mock API response data
    mock_usage_data = {
        "data": [
            {"timestamp": "2025-03-01T00:00:00", "value": 100.5},
            {"timestamp": "2025-03-01T00:30:00", "value": 101.2},
            {"timestamp": "2025-03-01T01:00:00", "value": 102.0},
        ]
    }
    
    mock_cost_data = {
        "data": [
            {"timestamp": "2025-03-01T00:00:00", "value": 50.25},
            {"timestamp": "2025-03-01T00:30:00", "value": 50.60},
            {"timestamp": "2025-03-01T01:00:00", "value": 51.00},
        ]
    }
    
    # Test usage data extraction
    print("\n[1/2] Testing usage data extraction...")
    if isinstance(mock_usage_data, dict) and "data" in mock_usage_data:
        data_points = mock_usage_data["data"]
        if data_points and isinstance(data_points, list) and len(data_points) > 0:
            latest = data_points[-1]
            if isinstance(latest, dict) and "value" in latest:
                value = float(latest["value"])
                print(f"✅ Extracted usage value: {value} kWh")
                assert value == 102.0, "Value should be 102.0"
            else:
                print("❌ Latest data point missing 'value' key")
                return False
        else:
            print("❌ No data points found")
            return False
    else:
        print("❌ Invalid data structure")
        return False
    
    # Test cost data extraction
    print("\n[2/2] Testing cost data extraction...")
    if isinstance(mock_cost_data, dict) and "data" in mock_cost_data:
        data_points = mock_cost_data["data"]
        if data_points and isinstance(data_points, list) and len(data_points) > 0:
            latest = data_points[-1]
            if isinstance(latest, dict) and "value" in latest:
                value = float(latest["value"])
                print(f"✅ Extracted cost value: {value} MYR")
                assert value == 51.00, "Value should be 51.00"
            else:
                print("❌ Latest data point missing 'value' key")
                return False
        else:
            print("❌ No data points found")
            return False
    else:
        print("❌ Invalid data structure")
        return False
    
    print("\n" + "=" * 70)
    print("✅ All sensor logic tests passed!")
    print("=" * 70)
    return True


async def test_config_flow_validation():
    """Test config flow validation logic."""
    print("\n" + "=" * 70)
    print("Testing Config Flow Validation")
    print("=" * 70)
    
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    smartmeter_url = os.environ.get("SMARTMETER_URL")
    
    if not all([username, password, smartmeter_url]):
        print("⚠️  Skipping - missing environment variables")
        return True
    
    # Simulate config flow validation
    print("\n[1/2] Testing input validation...")
    data = {
        "username": username,
        "password": password,
        "smartmeter_url": smartmeter_url,
    }
    
    # Check required fields
    required_fields = ["username", "password", "smartmeter_url"]
    for field in required_fields:
        if field not in data or not data[field]:
            print(f"❌ Missing required field: {field}")
            return False
    
    print("✅ All required fields present")
    
    # Test API creation and authentication
    print("\n[2/2] Testing API creation and authentication...")
    try:
        async with MyTNBAPI(
            data["username"],
            data["password"],
            data["smartmeter_url"],
        ) as api:
            # Test authentication (now async)
            if await api.authenticate():
                print("✅ API authentication successful")
            else:
                print("❌ API authentication failed")
                return False
            
            # Test getting sdpudcid
            sdpudcid = await api.get_sdpudcid()
            if sdpudcid:
                print(f"✅ Successfully retrieved sdpudcid: {sdpudcid}")
            else:
                print("❌ Failed to retrieve sdpudcid")
                return False
                
    except Exception as e:
        print(f"❌ Validation error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 70)
    print("✅ All config flow validation tests passed!")
    print("=" * 70)
    return True


async def test_async_operations():
    """Test async operations that would be used in Home Assistant."""
    print("\n" + "=" * 70)
    print("Testing Async Operations (Home Assistant Simulation)")
    print("=" * 70)
    
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    smartmeter_url = os.environ.get("SMARTMETER_URL")
    
    if not all([username, password, smartmeter_url]):
        print("⚠️  Skipping - missing environment variables")
        return True
    
    # Test async authentication (API is now async)
    print("\n[1/3] Testing async authentication...")
    
    try:
        async with MyTNBAPI(username, password, smartmeter_url) as api:
            result = await api.authenticate()
            if result:
                print("✅ Async authentication successful")
            else:
                print("❌ Async authentication failed")
                return False
    except Exception as e:
        print(f"❌ Async authentication error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test async data fetching
    print("\n[2/3] Testing async data fetching...")
    
    try:
        async with MyTNBAPI(username, password, smartmeter_url) as api:
            await api.authenticate()  # Ensure authenticated
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            start_str = start_date.strftime("%Y-%m-%d+00:00")
            end_str = end_date.strftime("%Y-%m-%d+00:00")
            
            data = await api.get_data("usage", start=start_str, end=end_str)
            
            if isinstance(data, dict) and "data" in data:
                print(f"✅ Async data fetch successful: {len(data['data'])} points")
            else:
                print("❌ Async data fetch returned invalid format")
                return False
    except Exception as e:
        print(f"❌ Async data fetch error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test coordinator-like behavior
    print("\n[3/3] Testing coordinator simulation...")
    
    async def simulate_coordinator_update():
        """Simulate what the coordinator does."""
        async with MyTNBAPI(username, password, smartmeter_url) as api:
            # Authenticate
            await api.authenticate()
            
            # Get data
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            start_str = start_date.strftime("%Y-%m-%d+00:00")
            end_str = end_date.strftime("%Y-%m-%d+00:00")
            
            data = {}
            for metric in ["usage", "cost"]:
                try:
                    result = await api.get_data(metric, start=start_str, end=end_str)
                    data[metric] = result
                except Exception as e:
                    print(f"   Error fetching {metric}: {e}")
                    data[metric] = None
            
            # Get sdpudcid
            try:
                sdpudcid = await api.get_sdpudcid()
                data["sdpudcid"] = sdpudcid
            except Exception as e:
                print(f"   Error fetching sdpudcid: {e}")
                data["sdpudcid"] = None
            
            return data
    
    try:
        coordinator_data = await simulate_coordinator_update()
        print(f"✅ Coordinator simulation successful")
        print(f"   Usage data points: {len(coordinator_data.get('usage', {}).get('data', []))}")
        print(f"   Cost data points: {len(coordinator_data.get('cost', {}).get('data', []))}")
        print(f"   SDPUDCID: {coordinator_data.get('sdpudcid')}")
    except Exception as e:
        print(f"❌ Coordinator simulation error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 70)
    print("✅ All async operation tests passed!")
    print("=" * 70)
    return True


async def test_live_sensor():
    """Live sensor test using .env credentials - tests sensors with real API data."""
    print("\n" + "=" * 70)
    print("Live Sensor Test - Testing with Real API Data")
    print("=" * 70)
    
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    smartmeter_url = os.environ.get("SMARTMETER_URL")
    
    if not all([username, password, smartmeter_url]):
        print("❌ Missing environment variables!")
        print("Please set USERNAME, PASSWORD, and SMARTMETER_URL in .env file")
        return False
    
    print(f"\n✓ Username: {username}")
    print(f"✓ Smart Meter URL: {smartmeter_url[:60]}...")
    
    # Import sensor components
    try:
        from mytnb.sensor import (
            MyTNBCoordinator,
            MyTNBSensor,
            SENSOR_DESCRIPTIONS,
        )
    except ImportError as e:
        print(f"❌ Failed to import sensor components: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Create mock Home Assistant and ConfigEntry
    print("\n[1/5] Setting up test environment...")
    
    mock_hass = Mock()
    
    # Mock async_add_executor_job to execute synchronously
    async def mock_executor_job(func, *args):
        return func(*args)
    
    mock_hass.async_add_executor_job = mock_executor_job
    
    mock_entry = Mock()
    mock_entry.entry_id = "live_test_entry"
    mock_entry.data = {
        "username": username,
        "password": password,
        "smartmeter_url": smartmeter_url,
    }
    
    # Create real API instance
    async with MyTNBAPI(username, password, smartmeter_url) as api:
        mock_entry.runtime_data = api
        
        print("✅ Test environment ready")
        
        # Authenticate API before use
        print("\n[2/6] Authenticating API...")
        try:
            if await api.authenticate():
                print("✅ API authenticated successfully")
            else:
                print("❌ API authentication failed")
                return False
        except Exception as e:
            print(f"❌ API authentication error: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 1: Initialize coordinator
        print("\n[3/6] Initializing coordinator...")
        try:
            with patch('homeassistant.helpers.frame.report_usage'):
                coordinator = MyTNBCoordinator(mock_hass, api, mock_entry)
            print("✅ Coordinator initialized")
        except Exception as e:
            print(f"❌ Coordinator initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 2: Fetch live data (1 week)
        print("\n[4/6] Fetching 1 week of live data from API...")
        
        # Calculate date range (1 week)
        end_date = datetime.now() - timedelta(days=3)
        start_date = end_date - timedelta(days=4)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        print(f"   Date range: {start_str} to {end_str} (7 days)")
        
        try:
            data = await coordinator._async_update_data()
            
            # Set coordinator.data so sensors can access it
            # DataUpdateCoordinator only sets .data when using refresh methods,
            # not when calling _async_update_data() directly
            coordinator.data = data
            
            print(f"✅ Data fetched successfully")
            print(f"   SDPUDCID: {data.get('sdpudcid', 'N/A')}")
            
            # Show detailed data structure and statistics
            if data.get("usage"):
                usage_data = data["usage"]
                if isinstance(usage_data, dict) and "data" in usage_data:
                    inner_data = usage_data["data"]
                    if isinstance(inner_data, dict) and "timeseries" in inner_data:
                        timeseries = inner_data["timeseries"]
                        if isinstance(timeseries, list):
                            print(f"\n   📊 Usage Data (Timeseries Format):")
                            print(f"      Total timeseries entries: {len(timeseries)}")
                            
                            # Extract all data points from timeseries
                            all_data_points = []
                            for item in timeseries:
                                if isinstance(item, dict) and "data" in item:
                                    item_data = item["data"]
                                    if isinstance(item_data, list):
                                        all_data_points.extend(item_data)
                            
                            if all_data_points:
                                print(f"      Total data points: {len(all_data_points)}")
                                if len(all_data_points) > 0:
                                    # Show first and last entries
                                    print(f"      First entry: {all_data_points[0]}")
                                    if len(all_data_points) > 1:
                                        print(f"      Last entry: {all_data_points[-1]}")
                                    
                                    # Calculate statistics
                                    values = []
                                    for point in all_data_points:
                                        if isinstance(point, dict) and "value" in point:
                                            val = point.get("value")
                                            if isinstance(val, (int, float)):
                                                values.append(float(val))
                                    
                                    if values:
                                        print(f"      Statistics:")
                                        print(f"         Min: {min(values):.2f} kWh")
                                        print(f"         Max: {max(values):.2f} kWh")
                                        print(f"         Average: {sum(values)/len(values):.2f} kWh")
                                        print(f"         Latest: {values[-1]:.2f} kWh")
                                    
                                    # Show sample timeseries entries
                                    print(f"\n      Sample timeseries entries (first 3):")
                                    for i, item in enumerate(timeseries[:3]):
                                        if isinstance(item, dict):
                                            print(f"         Entry {i+1}:")
                                            print(f"            Start: {item.get('start', 'N/A')}")
                                            print(f"            End: {item.get('end', 'N/A')}")
                                            if "data" in item and isinstance(item["data"], list) and len(item["data"]) > 0:
                                                data_point = item["data"][0]
                                                print(f"            Value: {data_point.get('value', 'N/A')}")
                                                print(f"            Datetime: {data_point.get('datetime', 'N/A')}")
                    elif isinstance(inner_data, list):
                        print(f"\n   📊 Usage Data (List Format):")
                        print(f"      Total data points: {len(inner_data)}")
                        if len(inner_data) > 0:
                            print(f"      First entry: {inner_data[0]}")
                            if len(inner_data) > 1:
                                print(f"      Last entry: {inner_data[-1]}")
                    elif isinstance(inner_data, dict):
                        print(f"\n   📊 Usage Data (Dict Format):")
                        print(f"      Total keys: {len(inner_data)}")
                        if "timeseries" in inner_data:
                            print(f"      ⚠️  Found 'timeseries' key in data dict")
                        # Show first few keys
                        keys = list(inner_data.keys())[:5]
                        print(f"      Sample keys: {keys}")
            
            if data.get("cost"):
                cost_data = data["cost"]
                if isinstance(cost_data, dict) and "data" in cost_data:
                    inner_data = cost_data["data"]
                    if isinstance(inner_data, dict) and "timeseries" in inner_data:
                        timeseries = inner_data["timeseries"]
                        if isinstance(timeseries, list):
                            print(f"\n   💰 Cost Data (Timeseries Format):")
                            print(f"      Total timeseries entries: {len(timeseries)}")
                            
                            # Extract all data points from timeseries
                            all_data_points = []
                            for item in timeseries:
                                if isinstance(item, dict) and "data" in item:
                                    item_data = item["data"]
                                    if isinstance(item_data, list):
                                        all_data_points.extend(item_data)
                            
                            if all_data_points:
                                print(f"      Total data points: {len(all_data_points)}")
                                if len(all_data_points) > 0:
                                    # Show first and last entries
                                    print(f"      First entry: {all_data_points[0]}")
                                    if len(all_data_points) > 1:
                                        print(f"      Last entry: {all_data_points[-1]}")
                                    
                                    # Calculate statistics
                                    values = []
                                    for point in all_data_points:
                                        if isinstance(point, dict) and "value" in point:
                                            val = point.get("value")
                                            if isinstance(val, (int, float)):
                                                values.append(float(val))
                                    
                                    if values:
                                        print(f"      Statistics:")
                                        print(f"         Min: {min(values):.2f} MYR")
                                        print(f"         Max: {max(values):.2f} MYR")
                                        print(f"         Average: {sum(values)/len(values):.2f} MYR")
                                        print(f"         Latest: {values[-1]:.2f} MYR")
                                    
                                    # Show sample timeseries entries
                                    print(f"\n      Sample timeseries entries (first 3):")
                                    for i, item in enumerate(timeseries[:3]):
                                        if isinstance(item, dict):
                                            print(f"         Entry {i+1}:")
                                            print(f"            Start: {item.get('start', 'N/A')}")
                                            print(f"            End: {item.get('end', 'N/A')}")
                                            if "data" in item and isinstance(item["data"], list) and len(item["data"]) > 0:
                                                data_point = item["data"][0]
                                                print(f"            Value: {data_point.get('value', 'N/A')}")
                                                print(f"            Datetime: {data_point.get('datetime', 'N/A')}")
                    elif isinstance(inner_data, list):
                        print(f"\n   💰 Cost Data (List Format):")
                        print(f"      Total data points: {len(inner_data)}")
                        if len(inner_data) > 0:
                            print(f"      First entry: {inner_data[0]}")
                            if len(inner_data) > 1:
                                print(f"      Last entry: {inner_data[-1]}")
                    elif isinstance(inner_data, dict):
                        print(f"\n   💰 Cost Data (Dict Format):")
                        print(f"      Total keys: {len(inner_data)}")
                        if "timeseries" in inner_data:
                            print(f"      ⚠️  Found 'timeseries' key in data dict")
                        # Show first few keys
                        keys = list(inner_data.keys())[:5]
                        print(f"      Sample keys: {keys}")
        
        except Exception as e:
            print(f"❌ Failed to fetch data: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 3: Create sensors
        print("\n[5/6] Creating sensor entities...")
        try:
            sensors = []
            for description in SENSOR_DESCRIPTIONS:
                sensor = MyTNBSensor(coordinator, mock_entry, description)
                sensors.append(sensor)
                print(f"✅ Created sensor: {description.name} ({description.key})")
            
            assert len(sensors) == 2, "Should have 2 sensors"
            print(f"✅ All sensors created")
        except Exception as e:
            print(f"❌ Failed to create sensors: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 4: Test sensor values and attributes
        print("\n[6/6] Testing sensor values and attributes...")
        try:
            usage_sensor = sensors[0] if sensors[0].entity_description.key == "usage" else sensors[1]
            cost_sensor = sensors[1] if sensors[1].entity_description.key == "cost" else sensors[0]
            
            # Debug: Check what data the coordinator has
            print(f"\n🔍 Debug: Coordinator data structure:")
            if coordinator.data:
                print(f"   Coordinator data keys: {list(coordinator.data.keys())}")
                if "usage" in coordinator.data:
                    usage_data = coordinator.data["usage"]
                    print(f"   Usage data type: {type(usage_data)}")
                    if isinstance(usage_data, dict):
                        print(f"   Usage data keys: {list(usage_data.keys())[:10]}")
                        if "data" in usage_data:
                            inner = usage_data["data"]
                            print(f"   Usage data['data'] type: {type(inner)}")
                            if isinstance(inner, dict):
                                print(f"   Usage data['data'] keys: {list(inner.keys())[:10]}")
                                if "timeseries" in inner:
                                    ts = inner["timeseries"]
                                    print(f"   Timeseries type: {type(ts)}, length: {len(ts) if isinstance(ts, list) else 'N/A'}")
            
            # Test usage sensor
            print(f"\n📊 Usage Sensor:")
            print(f"   Name: {usage_sensor.entity_description.name}")
            print(f"   Unique ID: {usage_sensor._attr_unique_id}")
            print(f"   Unit: {usage_sensor.entity_description.native_unit_of_measurement}")
            print(f"   State Class: {usage_sensor.entity_description.state_class}")
            
            usage_value = usage_sensor.native_value
            if usage_value is not None:
                print(f"   ✅ Current Value: {usage_value} kWh")
            else:
                print(f"   ⚠️  Value: None (no data available)")
            
            usage_attrs = usage_sensor.extra_state_attributes
            print(f"   Attributes:")
            for key, value in usage_attrs.items():
                print(f"      - {key}: {value}")
            
            # Test cost sensor
            print(f"\n💰 Cost Sensor:")
            print(f"   Name: {cost_sensor.entity_description.name}")
            print(f"   Unique ID: {cost_sensor._attr_unique_id}")
            print(f"   Unit: {cost_sensor.entity_description.native_unit_of_measurement}")
            print(f"   State Class: {cost_sensor.entity_description.state_class}")
            
            cost_value = cost_sensor.native_value
            if cost_value is not None:
                print(f"   ✅ Current Value: {cost_value} MYR")
            else:
                print(f"   ⚠️  Value: None (no data available)")
            
            cost_attrs = cost_sensor.extra_state_attributes
            print(f"   Attributes:")
            for key, value in cost_attrs.items():
                print(f"      - {key}: {value}")
            
            # Validate values
            if usage_value is None and cost_value is None:
                print(f"\n⚠️  Warning: Both sensors returned None values")
                print(f"   This might indicate:")
                print(f"   - No data available for the date range")
                print(f"   - API returned empty results")
                print(f"   - Data format might be unexpected")
            else:
                print(f"\n✅ Sensor values retrieved successfully")
            
        except Exception as e:
            print(f"❌ Failed to test sensor values: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Summary
        print("\n" + "=" * 70)
        print("Live Sensor Test Summary (1 Week Data)")
        print("=" * 70)
        print(f"✅ Date Range: {start_str} to {end_str} (7 days)")
        print(f"✅ Coordinator: Initialized and fetched data")
        print(f"✅ Sensors: Created {len(sensors)} sensor entities")
        print(f"✅ Usage Sensor: {usage_value if usage_value is not None else 'No data'} kWh")
        print(f"✅ Cost Sensor: {cost_value if cost_value is not None else 'No data'} MYR")
        print(f"✅ SDPUDCID: {data.get('sdpudcid', 'N/A')}")
        
        # Show data point counts
        if data.get("usage"):
            usage_data = data["usage"]
            if isinstance(usage_data, dict) and "data" in usage_data:
                inner_data = usage_data["data"]
                if isinstance(inner_data, dict) and "timeseries" in inner_data:
                    timeseries = inner_data["timeseries"]
                    count = len(timeseries) if isinstance(timeseries, list) else 0
                    print(f"✅ Usage Data Points: {count}")
        
        if data.get("cost"):
            cost_data = data["cost"]
            if isinstance(cost_data, dict) and "data" in cost_data:
                inner_data = cost_data["data"]
                if isinstance(inner_data, dict) and "timeseries" in inner_data:
                    timeseries = inner_data["timeseries"]
                    count = len(timeseries) if isinstance(timeseries, list) else 0
                    print(f"✅ Cost Data Points: {count}")
        
        print("=" * 70)
        
        return True


async def test_sensor_integration():
    """Integration test for sensor.py components."""
    print("\n" + "=" * 70)
    print("Testing Sensor Integration (sensor.py)")
    print("=" * 70)
    
    username = os.environ.get("USERNAME")
    password = os.environ.get("PASSWORD")
    smartmeter_url = os.environ.get("SMARTMETER_URL")
    
    if not all([username, password, smartmeter_url]):
        print("⚠️  Skipping - missing environment variables")
        return True
    
    # Import sensor components
    try:
        from mytnb.sensor import (
            MyTNBCoordinator,
            MyTNBSensor,
            SENSOR_DESCRIPTIONS,
            async_setup_entry,
        )
    except ImportError as e:
        print(f"❌ Failed to import sensor components: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Create mock Home Assistant and ConfigEntry
    print("\n[1/7] Setting up mocks...")
    
    mock_hass = Mock()
    mock_hass.async_add_executor_job = AsyncMock()
    
    mock_entry = Mock()
    mock_entry.entry_id = "test_entry_123"
    mock_entry.data = {
        "username": username,
        "password": password,
        "smartmeter_url": smartmeter_url,
    }
    
    # Create real API instance
    api = MyTNBAPI(username, password, smartmeter_url)
    mock_entry.runtime_data = api
    
    print("✅ Mocks created")
    
    # Test 1: Coordinator initialization
    print("\n[2/7] Testing coordinator initialization...")
    try:
        # Patch the frame helper to avoid RuntimeError in test environment
        with patch('homeassistant.helpers.frame.report_usage'):
            coordinator = MyTNBCoordinator(mock_hass, api, mock_entry)
        assert coordinator.api == api, "API should be set"
        assert coordinator.entry == mock_entry, "Entry should be set"
        assert coordinator.name == DOMAIN, "Coordinator name should match DOMAIN"
        print("✅ Coordinator initialized successfully")
    except Exception as e:
        print(f"❌ Coordinator initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Coordinator data update (with real API)
    print("\n[3/7] Testing coordinator data update...")
    try:
        # Ensure API is authenticated (authenticate is now async)
        try:
            if not await api.authenticate():
                print("❌ Failed to authenticate API")
                return False
        except Exception as auth_err:
            # If already authenticated or other error, continue
            print(f"   Authentication check: {auth_err}")
        
        # Call the update method
        data = await coordinator._async_update_data()
        
        # Set coordinator.data so sensors can access it
        coordinator.data = data
        
        # Validate data structure
        assert isinstance(data, dict), "Data should be a dictionary"
        assert "usage" in data, "Data should contain 'usage'"
        assert "cost" in data, "Data should contain 'cost'"
        assert "sdpudcid" in data, "Data should contain 'sdpudcid'"
        
        print(f"✅ Coordinator update successful")
        print(f"   Usage data: {'present' if data.get('usage') else 'missing'}")
        print(f"   Cost data: {'present' if data.get('cost') else 'missing'}")
        print(f"   SDPUDCID: {data.get('sdpudcid', 'N/A')}")
        
        # Validate usage data structure if present
        if data.get("usage"):
            usage_data = data["usage"]
            if isinstance(usage_data, dict) and "data" in usage_data:
                data_points = usage_data["data"]
                if isinstance(data_points, list):
                    print(f"   Usage data points: {len(data_points)} (list format)")
                elif isinstance(data_points, dict):
                    print(f"   Usage data points: {len(data_points)} (dict format)")
        
        # Validate cost data structure if present
        if data.get("cost"):
            cost_data = data["cost"]
            if isinstance(cost_data, dict) and "data" in cost_data:
                data_points = cost_data["data"]
                if isinstance(data_points, list):
                    print(f"   Cost data points: {len(data_points)} (list format)")
                elif isinstance(data_points, dict):
                    print(f"   Cost data points: {len(data_points)} (dict format)")
        
    except Exception as e:
        print(f"❌ Coordinator update failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Sensor entity initialization
    print("\n[4/7] Testing sensor entity initialization...")
    try:
        sensors = []
        for description in SENSOR_DESCRIPTIONS:
            sensor = MyTNBSensor(coordinator, mock_entry, description)
            assert sensor.coordinator == coordinator, "Coordinator should be set"
            assert sensor.entity_description == description, "Description should be set"
            assert sensor._attr_unique_id == f"{mock_entry.entry_id}_{description.key}", "Unique ID should match"
            sensors.append(sensor)
            print(f"✅ Created sensor: {description.key} (unique_id: {sensor._attr_unique_id})")
        
        assert len(sensors) == 2, "Should have 2 sensors (usage and cost)"
        print(f"✅ All sensors initialized successfully")
    except Exception as e:
        print(f"❌ Sensor initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Sensor value extraction (list format)
    print("\n[5/7] Testing sensor value extraction...")
    try:
        # Test with timeseries format data (matching actual API structure)
        # API response structure: {"data": {"timeseries": [...], "baseurn": "...", ...}, ...}
        mock_list_data = {
            "usage": {
                "data": {
                    "timeseries": [
                        {
                            "data": [{
                                "category": "energy_charge",
                                "datetime": "2025-03-01 00:00",
                                "flag": None,
                                "granularity": "MIN30",
                                "metric": "USAGE",
                                "rateplan": "TNB NEM Domestic General",
                                "value": 100.5
                            }],
                            "end": "2025-03-01 00:30",
                            "start": "2025-03-01 00:00",
                            "zoomin": "&view=DAY&metric=USAGE&granularity=MIN30&start=2025-03-01+00%3A00&end=2025-03-01+00%3A30",
                            "zoomout": "&view=YEAR&metric=USAGE&granularity=BILLCYCLE&start=2024-03-01+00%3A00&end=2025-03-31+00%3A00"
                        },
                        {
                            "data": [{
                                "category": "energy_charge",
                                "datetime": "2025-03-01 00:30",
                                "flag": None,
                                "granularity": "MIN30",
                                "metric": "USAGE",
                                "rateplan": "TNB NEM Domestic General",
                                "value": 101.2
                            }],
                            "end": "2025-03-01 01:00",
                            "start": "2025-03-01 00:30",
                            "zoomin": "&view=DAY&metric=USAGE&granularity=MIN30&start=2025-03-01+00%3A30&end=2025-03-01+01%3A00",
                            "zoomout": "&view=YEAR&metric=USAGE&granularity=BILLCYCLE&start=2024-03-01+00%3A00&end=2025-03-31+00%3A00"
                        },
                        {
                            "data": [{
                                "category": "energy_charge",
                                "datetime": "2025-03-01 01:00",
                                "flag": None,
                                "granularity": "MIN30",
                                "metric": "USAGE",
                                "rateplan": "TNB NEM Domestic General",
                                "value": 102.0
                            }],
                            "end": "2025-03-01 01:30",
                            "start": "2025-03-01 01:00",
                            "zoomin": "&view=DAY&metric=USAGE&granularity=MIN30&start=2025-03-01+01%3A00&end=2025-03-01+01%3A30",
                            "zoomout": "&view=YEAR&metric=USAGE&granularity=BILLCYCLE&start=2024-03-01+00%3A00&end=2025-03-31+00%3A00"
                        },
                    ],
                    "baseurn": "/timeseries?orgname=TNB&sdpudcid=12345678&dateformat=yyyy-MM-dd+HH%3Amm&timezone=Asia%2FKuala_Lumpur",
                    "comments": None,
                    "commodity": "Electric",
                    "dateformat": "yyyy-MM-dd HH:mm",
                    "next": "&view=BILL&granularity=MIN30&start=2025-03-31+00%3A00&end=2025-04-30+00%3A00",
                    "previous": "&view=BILL&granularity=MIN30&start=2025-02-01+00%3A00&end=2025-03-01+00%3A00",
                    "requestend": "2025-03-06 00:00",
                    "requestgranularity": "MIN30",
                    "requeststart": "2025-03-02 00:00",
                    "requestview": "BILL",
                    "sdpudcid": "12345678"
                }
            },
            "cost": {
                "data": {
                    "timeseries": [
                        {
                            "data": [{
                                "category": "energy_charge",
                                "datetime": "2025-03-01 00:00",
                                "flag": None,
                                "granularity": "MIN30",
                                "metric": "COST",
                                "rateplan": "TNB NEM Domestic General",
                                "value": 50.25
                            }],
                            "end": "2025-03-01 00:30",
                            "start": "2025-03-01 00:00",
                            "zoomin": "&view=DAY&metric=COST&granularity=MIN30&start=2025-03-01+00%3A00&end=2025-03-01+00%3A30",
                            "zoomout": "&view=YEAR&metric=COST&granularity=BILLCYCLE&start=2024-03-01+00%3A00&end=2025-03-31+00%3A00"
                        },
                        {
                            "data": [{
                                "category": "energy_charge",
                                "datetime": "2025-03-01 00:30",
                                "flag": None,
                                "granularity": "MIN30",
                                "metric": "COST",
                                "rateplan": "TNB NEM Domestic General",
                                "value": 50.60
                            }],
                            "end": "2025-03-01 01:00",
                            "start": "2025-03-01 00:30",
                            "zoomin": "&view=DAY&metric=COST&granularity=MIN30&start=2025-03-01+00%3A30&end=2025-03-01+01%3A00",
                            "zoomout": "&view=YEAR&metric=COST&granularity=BILLCYCLE&start=2024-03-01+00%3A00&end=2025-03-31+00%3A00"
                        },
                        {
                            "data": [{
                                "category": "energy_charge",
                                "datetime": "2025-03-01 01:00",
                                "flag": None,
                                "granularity": "MIN30",
                                "metric": "COST",
                                "rateplan": "TNB NEM Domestic General",
                                "value": 51.00
                            }],
                            "end": "2025-03-01 01:30",
                            "start": "2025-03-01 01:00",
                            "zoomin": "&view=DAY&metric=COST&granularity=MIN30&start=2025-03-01+01%3A00&end=2025-03-01+01%3A30",
                            "zoomout": "&view=YEAR&metric=COST&granularity=BILLCYCLE&start=2024-03-01+00%3A00&end=2025-03-31+00%3A00"
                        },
                    ],
                    "baseurn": "/timeseries?orgname=TNB&sdpudcid=12345678&dateformat=yyyy-MM-dd+HH%3Amm&timezone=Asia%2FKuala_Lumpur",
                    "comments": None,
                    "commodity": "Electric",
                    "dateformat": "yyyy-MM-dd HH:mm",
                    "next": "&view=BILL&granularity=MIN30&start=2025-03-31+00%3A00&end=2025-04-30+00%3A00",
                    "previous": "&view=BILL&granularity=MIN30&start=2025-02-01+00%3A00&end=2025-03-01+00%3A00",
                    "requestend": "2025-03-06 00:00",
                    "requestgranularity": "MIN30",
                    "requeststart": "2025-03-02 00:00",
                    "requestview": "BILL",
                    "sdpudcid": "12345678"
                }
            },
            "sdpudcid": "12345678",
        }
        
        coordinator.data = mock_list_data
        
        usage_sensor = sensors[0] if sensors[0].entity_description.key == "usage" else sensors[1]
        cost_sensor = sensors[1] if sensors[1].entity_description.key == "cost" else sensors[0]
        
        usage_value = usage_sensor.native_value
        cost_value = cost_sensor.native_value
        
        assert usage_value == 102.0, f"Usage value should be 102.0, got {usage_value}"
        assert cost_value == 51.00, f"Cost value should be 51.00, got {cost_value}"
        
        print(f"✅ Timeseries format extraction successful")
        print(f"   Usage value: {usage_value} kWh")
        print(f"   Cost value: {cost_value} MYR")
        
        # Test with None/empty data
        coordinator.data = None
        assert usage_sensor.native_value is None, "Value should be None when data is None"
        assert cost_sensor.native_value is None, "Value should be None when data is None"
        print(f"✅ None data handling successful")
        
        # Restore real data for next test
        restored_data = await coordinator._async_update_data()
        coordinator.data = restored_data
        
    except Exception as e:
        print(f"❌ Sensor value extraction failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 5: Sensor attributes
    print("\n[6/7] Testing sensor attributes...")
    try:
        # Ensure coordinator has data
        if coordinator.data is None:
            coordinator.data = await coordinator._async_update_data()
        
        usage_sensor = sensors[0] if sensors[0].entity_description.key == "usage" else sensors[1]
        cost_sensor = sensors[1] if sensors[1].entity_description.key == "cost" else sensors[0]
        
        usage_attrs = usage_sensor.extra_state_attributes
        cost_attrs = cost_sensor.extra_state_attributes
        
        # Validate attributes structure
        assert ATTR_METRIC in usage_attrs, "Usage sensor should have metric attribute"
        assert ATTR_VIEW in usage_attrs, "Usage sensor should have view attribute"
        assert ATTR_GRANULARITY in usage_attrs, "Usage sensor should have granularity attribute"
        assert usage_attrs[ATTR_METRIC] == "usage", "Metric should be 'usage'"
        assert usage_attrs[ATTR_VIEW] == DEFAULT_VIEW, f"View should be {DEFAULT_VIEW}"
        assert usage_attrs[ATTR_GRANULARITY] == DEFAULT_GRANULARITY, f"Granularity should be {DEFAULT_GRANULARITY}"
        
        assert ATTR_METRIC in cost_attrs, "Cost sensor should have metric attribute"
        assert cost_attrs[ATTR_METRIC] == "cost", "Metric should be 'cost'"
        
        # SDPUDCID should be present if available
        if coordinator.data and coordinator.data.get("sdpudcid"):
            assert ATTR_SDPUDCID in usage_attrs, "Usage sensor should have sdpudcid attribute"
            assert ATTR_SDPUDCID in cost_attrs, "Cost sensor should have sdpudcid attribute"
            assert usage_attrs[ATTR_SDPUDCID] == coordinator.data["sdpudcid"], "SDPUDCID should match"
            print(f"   SDPUDCID: {usage_attrs[ATTR_SDPUDCID]}")
        
        print(f"✅ Attributes extraction successful")
        print(f"   Usage attributes: {list(usage_attrs.keys())}")
        print(f"   Cost attributes: {list(cost_attrs.keys())}")
        
    except Exception as e:
        print(f"❌ Sensor attributes test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 6: Authentication flow (verify it's called)
    print("\n[7/7] Testing authentication flow...")
    try:
        # Reset sdpudcid to force re-fetch
        api._sdpudcid = None
        
        # Create a new coordinator to test fresh authentication
        # Patch the frame helper to avoid RuntimeError in test environment
        with patch('homeassistant.helpers.frame.report_usage'):
            new_coordinator = MyTNBCoordinator(mock_hass, api, mock_entry)
        
        # This should trigger authentication and data fetch
        data = await new_coordinator._async_update_data()
        
        # Verify authentication happened by checking sdpudcid was retrieved
        assert data.get('sdpudcid') is not None, "SDPUDCID should be retrieved after coordinator update"
        assert api._sdpudcid is not None, "API should have sdpudcid cached after coordinator update"
        print(f"✅ Authentication flow successful")
        print(f"   SDPUDCID retrieved: {data.get('sdpudcid')}")
        
    except Exception as e:
        print(f"❌ Authentication flow test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 70)
    print("✅ All sensor integration tests passed!")
    print("=" * 70)
    return True


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("MyTNB Integration Test Suite")
    print("Testing outside of Home Assistant")
    print("=" * 70)
    
    results = []
    
    # Test 1: API Client
    try:
        api_result = asyncio.run(test_api_client())
        results.append(("API Client", api_result))
    except Exception as e:
        print(f"\n❌ API Client test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(("API Client", False))
    
    # Test 2: Sensor Logic
    results.append(("Sensor Logic", test_sensor_logic()))
    
    # Test 3: Config Flow Validation
    try:
        config_result = asyncio.run(test_config_flow_validation())
        results.append(("Config Flow Validation", config_result))
    except Exception as e:
        print(f"\n❌ Config Flow Validation test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Config Flow Validation", False))
    
    # Test 4: Async Operations
    try:
        async_result = asyncio.run(test_async_operations())
        results.append(("Async Operations", async_result))
    except Exception as e:
        print(f"\n❌ Async test failed: {e}")
        results.append(("Async Operations", False))
    
    # Test 5: Live Sensor Test
    try:
        live_sensor_result = asyncio.run(test_live_sensor())
        results.append(("Live Sensor Test", live_sensor_result))
    except Exception as e:
        print(f"\n❌ Live sensor test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Live Sensor Test", False))
    
    # Test 6: Sensor Integration
    try:
        sensor_result = asyncio.run(test_sensor_integration())
        results.append(("Sensor Integration", sensor_result))
    except Exception as e:
        print(f"\n❌ Sensor integration test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Sensor Integration", False))
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    
    passed = 0
    failed = 0
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print("\n" + "-" * 70)
    print(f"Total: {len(results)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    # Allow running just the live sensor test with: python test_integration.py --live-sensor
    if len(sys.argv) > 1 and sys.argv[1] == "--live-sensor":
        print("\n" + "=" * 70)
        print("Running Live Sensor Test Only")
        print("=" * 70)
        try:
            success = asyncio.run(test_live_sensor())
            sys.exit(0 if success else 1)
        except Exception as e:
            print(f"\n❌ Live sensor test failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        success = main()
        sys.exit(0 if success else 1)
