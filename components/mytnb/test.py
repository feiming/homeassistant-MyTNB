import asyncio
import os

from dotenv import load_dotenv

from homeassistant.components.mytnb.tnb import TNBSmartMeter

load_dotenv()

username = os.environ["USERNAME"]
password = os.environ["PASSWORD"]


async def main():
    smartmeter = TNBSmartMeter()
    if await smartmeter.login(username, password):
        test = await smartmeter.login_smart_meter()
        sdpudcid = await smartmeter.get_sdpudcid()
        print(sdpudcid)
        # data = await smartmeter.get_data(
        #     {
        #         "metric": "usage",
        #         "view": "BILL",
        #         "granularity": "MIN30",
        #         "start": "2024-10-22+00:00",
        #         "end": "2024-10-23+00:00",
        #     }
        # )
        # print(data)
        await smartmeter.close()


if __name__ == "__main__":
    asyncio.run(main())
