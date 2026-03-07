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
)

load_dotenv()


def test_api_client():
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
    
    api = MyTNBAPI(username, password, smartmeter_url)
    
    # Test 1: Authentication
    print("\n[1/5] Testing authentication...")
    try:
        if api.authenticate():
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
        sdpudcid = api.get_sdpudcid()
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
        
        usage_data = api.get_data(
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
        cost_data = api.get_data(
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


def test_config_flow_validation():
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
        api = MyTNBAPI(
            data["username"],
            data["password"],
            data["smartmeter_url"],
        )
        
        # Test authentication (this is synchronous, but in HA it would be async)
        if api.authenticate():
            print("✅ API authentication successful")
        else:
            print("❌ API authentication failed")
            return False
        
        # Test getting sdpudcid
        sdpudcid = api.get_sdpudcid()
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
    
    # Simulate Home Assistant's async_add_executor_job
    print("\n[1/3] Testing async authentication...")
    
    def sync_authenticate():
        api = MyTNBAPI(username, password, smartmeter_url)
        return api.authenticate()
    
    try:
        # Simulate: await hass.async_add_executor_job(api.authenticate)
        result = await asyncio.get_event_loop().run_in_executor(None, sync_authenticate)
        if result:
            print("✅ Async authentication successful")
        else:
            print("❌ Async authentication failed")
            return False
    except Exception as e:
        print(f"❌ Async authentication error: {e}")
        return False
    
    # Test async data fetching
    print("\n[2/3] Testing async data fetching...")
    
    def sync_get_data():
        api = MyTNBAPI(username, password, smartmeter_url)
        api.authenticate()  # Ensure authenticated
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        start_str = start_date.strftime("%Y-%m-%d+00:00")
        end_str = end_date.strftime("%Y-%m-%d+00:00")
        return api.get_data("usage", start=start_str, end=end_str)
    
    try:
        # Simulate: await hass.async_add_executor_job(api.get_data, ...)
        data = await asyncio.get_event_loop().run_in_executor(None, sync_get_data)
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
        api = MyTNBAPI(username, password, smartmeter_url)
        
        # Authenticate
        await asyncio.get_event_loop().run_in_executor(None, api.authenticate)
        
        # Get data
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        start_str = start_date.strftime("%Y-%m-%d+00:00")
        end_str = end_date.strftime("%Y-%m-%d+00:00")
        
        data = {}
        for metric in ["usage", "cost"]:
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda m=metric: api.get_data(m, start=start_str, end=end_str)
                )
                data[metric] = result
            except Exception as e:
                print(f"   Error fetching {metric}: {e}")
                data[metric] = None
        
        # Get sdpudcid
        try:
            sdpudcid = await asyncio.get_event_loop().run_in_executor(None, api.get_sdpudcid)
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


def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("MyTNB Integration Test Suite")
    print("Testing outside of Home Assistant")
    print("=" * 70)
    
    results = []
    
    # Test 1: API Client
    results.append(("API Client", test_api_client()))
    
    # Test 2: Sensor Logic
    results.append(("Sensor Logic", test_sensor_logic()))
    
    # Test 3: Config Flow Validation
    results.append(("Config Flow Validation", test_config_flow_validation()))
    
    # Test 4: Async Operations
    try:
        async_result = asyncio.run(test_async_operations())
        results.append(("Async Operations", async_result))
    except Exception as e:
        print(f"\n❌ Async test failed: {e}")
        results.append(("Async Operations", False))
    
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
    success = main()
    sys.exit(0 if success else 1)
