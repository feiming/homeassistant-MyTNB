import requests
import re
import argparse
import pprint

SESSION = requests.Session()


def main():
    parser = argparse.ArgumentParser(
        description="Process username and password.")

    parser.add_argument(
        "--username", type=str, required=True, help="The username for authentication."
    )
    parser.add_argument(
        "--password", type=str, required=True, help="The password for authentication."
    )
    args = parser.parse_args()

    login(args.username, args.password)
    smartmeter_url = get_smartmeter_url()
    smart_meter(smartmeter_url)
    sdpudcid = get_sdpudcid()

    get_data(
        sdpudcid,
        {
            "metric": "usage",
            "view": "BILL",
            "granularity": "MIN30",
            "start": "2024-10-22+00:00",
            "end": "2024-10-23+00:00",
        },
    )
    # get_data(sdpudcid, {"metric": "usage", "view": "BILL"})
    # get_data(sdpudcid, {"metric": "cost", "view": "BILL"})


def get_login_details(username, password):
    url = "https://www.mytnb.com.my/api/sitecore/Account/Login"
    payload = {"Email": username, "Password": password}
    response = SESSION.request(
        "POST",
        url,
        data=payload,
    )

    matches = re.findall(r'name="([^"]+)" value="([^"]*)"', response.text)
    data = {name: value for name, value in matches}
    return data


def login(username, password):
    url = "https://myaccount.mytnb.com.my/SSO/SSOHandler"
    payload = get_login_details(username, password)
    response = SESSION.request(
        "POST",
        url,
        data=payload,
    )
    print(response.status_code)
    # print(SESSION.cookies.get_dict())
    # print(response.text)
    if response.status_code == 200:
        return True


def get_smartmeter_url():
    url = "https://myaccount.mytnb.com.my/AccountManagement/IndividualDashboard"
    headers = {
        "Host": "myaccount.mytnb.com.my",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": "https://myaccount.mytnb.com.my/SSO/SSOHandler",
        "DNT": "1",
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8",
    }
    # print(SESSION.cookies.get_dict())
    response = SESSION.request("GET", url, headers=headers)
    pattern = r'href="(/AccountManagement/SmartMeter/Index/TRIL\?caNo=[^"]+)"'
    match = re.search(pattern, response.text)
    return match.group(1)

    # # Regular expression to match the specific class and capture the desired text
    # pattern = r'class="list-group-item-title">\s*(.*?)\s*(\d+)\s*</div>'
    # matches = re.findall(pattern, response.text)

    # # Format the results
    # results = [f"{match[0].strip()} {match[1]}" for match in matches]
    # for result in results:
    #     print(result)
    # if response.status_code == 200:
    #     return True
    # print(response.text)


def smart_meter(smartmeter):
    url = f"https://myaccount.mytnb.com.my{smartmeter}"
    print(url)
    # print(SESSION.cookies.get_dict())
    response = SESSION.request("GET", url)
    # print(response.status_code)
    # print(response.text)
    if response.status_code == 200:
        return True


# def smart_meter_dashboard():
def get_sdpudcid():
    url = "https://smartliving.myaccount.mytnb.com.my/dashboard"
    headers = {
        "Host": "smartliving.myaccount.mytnb.com.my",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "DNT": "1",
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8",
    }
    response = SESSION.request("GET", url, headers=headers)
    match = re.search(r'"sdpudcid":"(\d+)"', response.text)
    if match:
        sdpudcid = match.group(1)
        return sdpudcid


# def get_cost(zoom=""):
#     url = f"https://smartliving.myaccount.mytnb.com.my/my_energy_request/timeseries?metric=cost&view=BILL&sdpudcid=xxxxxxxxxxxxxx{zoom}"
#     payload = {}
#     headers = {
#         'Referer': 'https://smartliving.myaccount.mytnb.com.my/commodity/electric/cost',
#         'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0',
#         'Accept': 'application/json',
#         'Accept-Language': 'en-US,en;q=0.5',
#         'X-Requested-With': 'XMLHttpRequest',
#         'X-Request': 'JSON',
#         'DNT': '1',
#         'Accept-Encoding': 'gzip, deflate, br, zstd',
#         'Host': 'smartliving.myaccount.mytnb.com.my',
#         'Sec-Fetch-Dest': 'empty',
#         'Sec-Fetch-Mode': 'cors',
#         'Sec-Fetch-Site': 'same-origin',
#     }
#     query_params = {
#         'metric': 'cost',
#         'view': 'BILL',
#         'sdpudcid': 'xxxxxxxxxxxxxxxxx'
#     }
#     # print(SESSION.cookies.get_dict())
#     response = SESSION.request(
#         "GET", url, headers=headers, data=payload, params=query_params)
#     pprint.pprint(response.json())

# def get_usage(zoom=""):
#     url = f"https://smartliving.myaccount.mytnb.com.my/my_energy_request/timeseries?metric=usage&view=BILL&sdpudcid=xxxxxxxxxxxxx{zoom}"
#     payload = {}
#     headers = {
#         'Referer': 'https://smartliving.myaccount.mytnb.com.my/commodity/electric/usage',
#         'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0',
#         'Accept': 'application/json',
#         'Accept-Language': 'en-US,en;q=0.5',
#         'X-Requested-With': 'XMLHttpRequest',
#         'X-Request': 'JSON',
#         'DNT': '1',
#         'Accept-Encoding': 'gzip, deflate, br, zstd',
#         'Host': 'smartliving.myaccount.mytnb.com.my',
#         'Sec-Fetch-Dest': 'empty',
#         'Sec-Fetch-Mode': 'cors',
#         'Sec-Fetch-Site': 'same-origin',
#     }
#     query_params = {
#         'metric': 'usage',
#         'view': 'BILL',
#         'sdpudcid': 'xxxxxxxxxxxxx'
#     }
#     # print(SESSION.cookies.get_dict())
#     response = SESSION.request(
#         "GET", url, headers=headers, data=payload, params=query_params)
#     pprint.pprint(response.json())


def get_data(sdpudcid, query_params):
    url = f"https://smartliving.myaccount.mytnb.com.my/my_energy_request/timeseries"
    headers = {
        "Referer": f'https://smartliving.myaccount.mytnb.com.my/commodity/electric/{query_params["metric"]}',
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5",
        "X-Requested-With": "XMLHttpRequest",
        "X-Request": "JSON",
        "DNT": "1",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Host": "smartliving.myaccount.mytnb.com.my",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    query_params["sdpudcid"] = sdpudcid
    response = SESSION.request(
        "GET", url, headers=headers, params=query_params)
    pprint.pprint(response.json())


if __name__ == "__main__":
    main()
