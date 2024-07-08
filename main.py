import asyncio
import json
import logging
import os
import random
from collections import OrderedDict
import pprint

from aiohttp import web, ClientSession
from aiohttp.web_runner import GracefulExit

from telethon import TelegramClient, events

# TYPE_CHECKING
from typing import Tuple, Optional
from telethon.tl.custom.message import Message
import telethon.tl.types
ChatId, MsgId = int, int
ImportRef = str


TG_APP_ID = os.getenv("TG_APP_ID")
TG_APP_HASH = os.getenv("TG_APP_HASH")

FUNKWHALE_APP_TOKEN = os.getenv("FUNKWHALE_APP_TOKEN")
FUNKWHALE_BASE_URL = os.getenv("FUNKWHALE_BASE_URL")

TGMOUNT_CHATS = os.getenv("TGMOUNT_CHATS")

if any(not x for x in (TG_APP_ID, TG_APP_HASH, FUNKWHALE_APP_TOKEN, FUNKWHALE_BASE_URL, TGMOUNT_CHATS)):
    raise Exception("Missing/empty env vars")

TGMOUNT_CHATS = json.loads(TGMOUNT_CHATS)
for chat, chat_cfg in TGMOUNT_CHATS.items():
    if any(key not in chat_cfg for key in ("funkwhale_import_path", "funkwhale_library")):
        raise Exception("Invalid TGMOUNT_CHATS env var")

_ref_cache: OrderedDict[Tuple[ChatId, MsgId], ImportRef] = OrderedDict()


def cache_ref(import_ref: ImportRef, chat_id: ChatId, msg_id: MsgId, cache_size=200):
    _ref_cache[(chat_id, msg_id)] = import_ref
    logging.debug(f"caching import_ref {import_ref} for {(chat_id, msg_id)}")
    while len(_ref_cache) > cache_size:
        _ref_cache.popitem(False)


async def get_ref(chat_id: ChatId, msg_id: MsgId, timeout=120) -> Optional[ImportRef]:
    d = (chat_id, msg_id)

    if d in _ref_cache:
        return _ref_cache[d]

    for _ in range(0, timeout * 10):
        if d in _ref_cache:
            return _ref_cache[d]
        await asyncio.sleep(0.1)

    logging.debug(f"import_ref for {d} not found in cache, timing out")
    return None


async def main():
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('telethon').setLevel(level=logging.INFO)

    app = web.Application()
    routes = web.RouteTableDef()

    fs_import_lock = asyncio.Lock()

    async with ClientSession(
        headers={"Authorization": 'Bearer ' + FUNKWHALE_APP_TOKEN}, raise_for_status=True
    ) as funk_http, TelegramClient(
        'data/tg.session', TG_APP_ID, TG_APP_HASH
    ) as tg:
        await tg.start()
        print("Started")

        async def add_tags(track_id, *new_tags, edit_summary=""):
            logging.info(f'adding tags: {new_tags} to track: {track_id} with edit summary: "{edit_summary}"')
            track = await (await funk_http.get(FUNKWHALE_BASE_URL + f"/api/v1/tracks/{track_id}?refresh=true")).json()
            logging.debug(f"downloading track info: {track_id}")
            # workaround for Funkwhale bug with spaces in tags
            current_tags = set(t.replace(' ', '') for t in track['tags'])
            logging.debug(f"got tags for track {track_id}: {current_tags}")

            tags = current_tags | set(t.replace('-', '_') for t in new_tags)

            if tags != current_tags:
                logging.info(f"setting tags for track {track_id} to {tags}")
                await funk_http.post(FUNKWHALE_BASE_URL + f"/api/v1/tracks/{track['id']}/mutations/", json={
                    "type": "update",
                    "payload": {"tags": list(tags)},
                    "summary": edit_summary,
                    "is_approved": True
                })
            else:
                logging.info(f"all tags already present on track {track_id}")

        async def import_get_tracks(ref):
            imp = await (await funk_http.get(FUNKWHALE_BASE_URL + '/api/v1/uploads/', params={
                'import_reference': ref,
                'page_size': 25,
            })).json()

            for x in imp['results']:
                if x['import_status'] in ("skipped", "finished"):
                    yield x

            while imp['next'] is not None:
                imp = await (await funk_http.get(imp['next'])).json()
                for x in imp['results']:
                    if x['import_status'] in ("skipped", "finished"):
                        yield x

        async def tg_msg_to_tags(msg: Message):
            tags = [f'tg_by_{msg.sender_id}']

            if msg.forward is not None and msg.forward.sender_id:
                tags.append(f"tg_fwd_from_{msg.forward.sender_id}")

            reply: Message = await msg.get_reply_message()
            if reply is not None:
                tags.append(f"tg_reply_to_by_{reply.sender_id}")

                if reply.forward is not None and reply.forward.sender_id:
                    tags.append(f"tg_reply_to_fwd_from_{reply.forward.sender_id}")

            return tags

        @tg.on(events.NewMessage(incoming=True))
        async def handle_funkwhale_tags(event: events.NewMessage.Event):
            msg: Message = event.message
            reply: Message = await msg.get_reply_message()
            tags = set()

            for e, txt in msg.get_entities_text():
                if isinstance(e, telethon.tl.types.MessageEntityHashtag) and txt.startswith("#funkwhale_"):
                    tag = txt[11:]
                    if tag:
                        tags.add("tgtag_" + tag)

            if reply is not None and tags:
                logging.info(f"tg message {msg.id} suitable for tagging")
                ref = await get_ref(reply.chat.id, reply.id)
                if ref is not None:
                    async for imp in import_get_tracks(ref):
                        await add_tags(
                            imp['track']['id'],
                            *tags,
                            edit_summary=f"Tagged in Telegram message: https://t.me/c/{msg.chat.id}/{msg.id}"
                        )

        @routes.get('/funkwhale_import_info/{import_ref}')
        async def import_info(req: web.Request):
            ref = req.match_info['import_ref']

            return web.json_response([x async for x in import_get_tracks(ref)])

        @routes.get('/update_tags_track/{track_id}/{chat_id}/{msg_id}')
        async def update_tags_track(req: web.Request):
            id_ = req.match_info['track_id']

            chat = await tg.get_entity(int(req.match_info['chat_id']))
            if chat is None:
                raise web.HTTPNotFound()

            msg: Message = await tg.get_messages(chat, ids=int(req.match_info['msg_id']))
            if msg is None:
                raise web.HTTPNotFound()

            tags = await tg_msg_to_tags(msg)

            await add_tags(id_, *tags, edit_summary=f"Sent in Telegram message: https://t.me/c/{chat.id}/{msg.id}")

            return web.Response(text=f"gen tags: {tags}")

        async def update_tags_import(ref: str, chat_id: int, msg_id: int) -> str:
            out = ""

            chat = await tg.get_entity(chat_id)
            if chat is None:
                raise web.HTTPNotFound()

            msg: Message = await tg.get_messages(chat, ids=msg_id)
            if msg is None:
                raise web.HTTPNotFound()

            tags = await tg_msg_to_tags(msg)

            tmp = f"gen tags: {tags}"
            logging.info(tmp)
            out += tmp + "\n"

            async for imp in import_get_tracks(ref):
                id_ = imp['track']['id']

                tmp = f"found track: {id_}"
                logging.info(tmp)
                out += tmp + "\n"

                await add_tags(id_, *tags, edit_summary=f"Sent in Telegram message: https://t.me/c/{chat.id}/{msg.id}")

            cache_ref(ref, chat.id, msg.id)

            return out

        @routes.get('/update_tags_import/{import_ref}/{chat_id}/{msg_id}')
        async def update_tags_import_route(req: web.Request):
            ref = req.match_info['import_ref']
            chat_id = int(req.match_info['chat_id'])
            msg_id = int(req.match_info['msg_id'])

            out = await update_tags_import(ref, chat_id, msg_id)

            return web.Response(text=out)

        @routes.post('/tgmount-webhook')
        async def tgmount_webhook(req: web.Request):
            data = await req.json()
            logging.info(f"tgmount new file: {pprint.pformat(data)}")

            if data.get('voice', False) or data.get('video', False):
                logging.info(f"skipping voice or video message")
                raise web.HTTPUnprocessableEntity(reason="skipping voice or video message")

            mimetype: str = data.get('mimetype', "")
            if not (mimetype.startswith("audio/") or mimetype == "application/zip"):
                logging.info(f"mime-type not suitable for import: {mimetype}")
                raise web.HTTPUnprocessableEntity(reason=f"mime-type not suitable for import: {mimetype}")

            if str(data['chat_id']) not in TGMOUNT_CHATS:
                logging.info(f"chat not configured in TGMOUNT_CHATS: {data['chat_id']}")
                raise web.HTTPUnprocessableEntity(reason=f"chat not configured in TGMOUNT_CHATS: {data['chat_id']}")

            tgmount_chat = TGMOUNT_CHATS[str(data['chat_id'])]
            funkwhale_library = tgmount_chat["funkwhale_library"]
            funkwhale_import_reference = f"telegram-{data['chat_id']}-{data['msg_id']}"
            funkwhale_import_path = f"{tgmount_chat["funkwhale_import_path"]}/{data['fname']}"

            import_data = {
                "library": funkwhale_library,
                "import_reference": funkwhale_import_reference,
                "path": funkwhale_import_path,
                "prune": False,
                "outbox": True,
                "broadcast": True,
                "replace": True,
                "batch_size": 100,
                "verbosity": 3
            }

            # Funkwhale has fs-import race condition and I've hope this will help
            async with fs_import_lock:
                while True:
                    import_resp = await funk_http.post(
                        f"{FUNKWHALE_BASE_URL}/api/v1/libraries/fs-import/",
                        json=import_data,
                        raise_for_status=False,
                    )

                    logging.info(f"start import status: {import_resp.status}")

                    match import_resp.status:
                        case 201:
                            # This is ok!
                            break
                        case 400 if (await import_resp.json() == {"detail": "An import is already running"}):
                            logging.info("import already running, retrying")
                            await asyncio.sleep(0.2 + (random.randrange(0, 80, 5)/100))
                            continue
                        case _:
                            logging.error(f"import returned wrong status ({import_resp.status}) with body: {await import_resp.text()}")
                            raise web.HTTPUnprocessableEntity(reason=f"import returned wrong status: {import_resp.status}")

                while True:
                    import_resp = await funk_http.get(f"{FUNKWHALE_BASE_URL}/api/v1/libraries/fs-import/")
                    import_data = (await import_resp.json())["import"]

                    if import_data["reference"] != funkwhale_import_reference:
                        logging.error(f"import reference not equal - telegram import: {funkwhale_import_reference}, import status: {import_data['reference']}")
                        return web.HTTPUnprocessableEntity(reason=f"import reference not equal - telegram import: {funkwhale_import_reference}, import status: {import_data['reference']}")

                    logging.info(f"import status: {import_data['status']}")

                    match import_data["status"]:
                        case "canceled":
                            logging.error(f"import canceled")
                            return web.HTTPUnprocessableEntity(reason="import canceled")
                        case "finished":
                            break
                        case "pending" | "started":
                            await asyncio.sleep(0.2 + (random.randrange(0, 30, 5)/100))
                            continue
                        case _:
                            logging.error(f"unknown import status: {import_data['status']}")
                            return web.HTTPServerError(reason=f"unknown import status: {import_data['status']}")

                logging.info("import finished")

            logging.info("started updating import tags")
            await update_tags_import(funkwhale_import_reference, data["chat_id"], data["msg_id"])
            logging.info("finished updating import tags")

            return web.Response()

        app.add_routes(routes)

        try:
            await asyncio.gather(tg.run_until_disconnected(), web._run_app(app, port=9999))
        except (GracefulExit, KeyboardInterrupt):
            pass


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
