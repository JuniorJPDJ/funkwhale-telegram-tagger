import os
import requests

FUNKWHALE_BASE_URL = os.getenv("FUNKWHALE_BASE_URL")
FUNKWHALE_CHAT_PATH_PREFIX = os.getenv("FUNKWHALE_CHAT_PATH_PREFIX")
FUNKWHALE_APP_TOKEN = os.getenv("FUNKWHALE_APP_TOKEN")

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

sess = requests.Session()
sess.headers.update({"Authorization": f'Bearer {FUNKWHALE_APP_TOKEN}'})

start_page = 40
next = FUNKWHALE_BASE_URL + f"/api/v1/uploads?page_size=100&ordering=creation_date&page={start_page}"
while next:
    print(next)
    r = sess.get(next).json()

    next = r['next']
    for x in r['results']:
        # tgmount filename format
        if not x['source'].startswith(FUNKWHALE_CHAT_PATH_PREFIX):
            print("skipping: file out of tg chat:", x['source'])
            continue
        src = x['source']

        if not x['track'] or 'id' not in x['track'] or not x['track']['id']:
            print("skipping: missing track info:", src)
            continue
        track_id = x['track']['id']

        # tgmount filename format
        msgid = x['source'].split(FUNKWHALE_CHAT_PATH_PREFIX, 1)[1].split(" ", 1)[0]
        print("tagging: track:", track_id, "msg:", msgid, src)

        r = sess.get(f"http://127.0.0.1:9999/update_tags_track/{track_id}/{TELEGRAM_CHAT_ID}/{msgid}")
        if r.status_code == 404:
            print("failed: telegram message deleted")
            continue
        r.raise_for_status()
        print(r.text)
