import asyncio
import logging
import websockets
import subprocess
import json
import os

WS_PASSWORD = os.getenv("WS_PASSWORD")
NOTIFICATION_WEBSOCKET_ENDPOINT = os.getenv("NOTIFICATION_WEBSOCKET_ENDPOINT")

async def main():

    if not WS_PASSWORD or not NOTIFICATION_WEBSOCKET_ENDPOINT:
        raise ValueError("ENV variables missing")

    extra_headers = {"X-API-Key": WS_PASSWORD}
    async with websockets.connect(NOTIFICATION_WEBSOCKET_ENDPOINT,
                                  additional_headers=extra_headers) as ws:
        async for msg in ws:
            try:
                data = json.loads(msg)
                response_data = data.get("data")
                data_type = data.get("type")
                status = data.get("status")

                if status == "success" and data_type == "new_jobs":
                    new_jobs = response_data.get("jobs")
                    job_ids = [job["id"] for job in new_jobs]
                    payload = {"type":"jobs_shown","job_ids": job_ids}
                    await ws.send(json.dumps(payload))
                    subprocess.run(["dunstify", f"{len(new_jobs)} new jobs found"])
                    logging.info(f"{len(new_jobs)} new jobs found")

                if status == "success" and data_type == "no_new_jobs":
                    logging.info("no new jobs")
                    subprocess.run(["dunstify", "no new jobs"])

            except json.JSONDecodeError:
                subprocess.run(["dunstify", "server error"])
                logging.info("server error")
asyncio.run(main())
