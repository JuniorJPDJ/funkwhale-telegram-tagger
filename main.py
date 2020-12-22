import asyncio
import os
from collections import OrderedDict as ordered_dict

from aiohttp import web, ClientSession
from aiohttp.web_runner import GracefulExit

from telethon import TelegramClient, events

# TYPE_CHECKING
from typing import Tuple, OrderedDict, Optional
from telethon.tl.custom.message import Message
import telethon.tl.types
ChatId, MsgId = int, int
ImportRef = str


TG_APP_ID = os.getenv("TG_APP_ID")
TG_APP_HASH = os.getenv("TG_APP_HASH")

FUNKWHALE_APP_TOKEN = os.getenv("FUNKWHALE_APP_TOKEN")
FUNKWHALE_BASE_URL = os.getenv("FUNKWHALE_BASE_URL")

if any(not x for x in (TG_APP_ID, TG_APP_HASH, FUNKWHALE_APP_TOKEN, FUNKWHALE_BASE_URL)):
    raise Exception("Missing/empty env vars")


_ref_cache: OrderedDict[Tuple[ChatId, MsgId], ImportRef] = ordered_dict()


def cache_ref(import_ref: ImportRef, chat_id: ChatId, msg_id: MsgId, cache_size=200):
    _ref_cache[(chat_id, msg_id)] = import_ref
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

    return None


async def main():
    app = web.Application()
    routes = web.RouteTableDef()

    async with ClientSession(
        headers={"Authorization": 'Bearer ' + FUNKWHALE_APP_TOKEN}, raise_for_status=True
    ) as funk_http, TelegramClient(
        'data/tg.session', TG_APP_ID, TG_APP_HASH
    ) as tg:
        await tg.start()
        print("Started")

        async def add_tags(track_id, *new_tags, edit_summary=""):
            track = await funk_http.get(FUNKWHALE_BASE_URL + f"/api/v1/tracks/{track_id}", params={
                'refresh': 'true'
            })
            track = await track.json()
            current_tags = set(track['tags'])

            tags = current_tags | set(new_tags)

            if tags != current_tags:
                await funk_http.post(FUNKWHALE_BASE_URL + f"/api/v1/tracks/{track['id']}/mutations/", json={
                # print(FUNKWHALE_BASE_URL + f"/api/v1/tracks/{track['id']}/mutations/", {
                    "type": "update",
                    "payload": {"tags": list(tags)},
                    "summary": edit_summary,
                    "is_approved": True
                })

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

            if reply is not None:
                ref = await get_ref(reply.chat.id, reply.id)
                if ref is not None and tags:
                    async for imp in import_get_tracks(ref):
                        await add_tags(
                            imp['track']['id'],
                            *tags,
                            edit_summary=f"Tagged in Telegram message: https://t.me/c/{msg.chat.id}/{msg.id}"
                        )

        @routes.get('/funkwhale_import_info/{import_ref}')
        async def import_info(req):
            ref = req.match_info['import_ref']

            return web.json_response([x async for x in import_get_tracks(ref)])

        @routes.get('/update_tags_track/{track_id}/{chat_id}/{msg_id}')
        async def import_info(req):
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

        @routes.get('/update_tags/{import_ref}/{chat_id}/{msg_id}')
        async def update_tags(req):
            out = ""
            ref = req.match_info['import_ref']

            chat = await tg.get_entity(int(req.match_info['chat_id']))
            if chat is None:
                raise web.HTTPNotFound()

            msg: Message = await tg.get_messages(chat, ids=int(req.match_info['msg_id']))
            if msg is None:
                raise web.HTTPNotFound()

            tags = await tg_msg_to_tags(msg)

            tmp = f"gen tags: {tags}"
            out += tmp + "\n"

            async for imp in import_get_tracks(ref):
                id_ = imp['track']['id']

                await add_tags(id_, *tags, edit_summary=f"Sent in Telegram message: https://t.me/c/{chat.id}/{msg.id}")

                tmp = f"found track: {id_} with tags {tags}"
                out += tmp + "\n"

            cache_ref(ref, chat.id, msg.id)

            return web.Response(text=out)

        app.add_routes(routes)
        try:
            await asyncio.gather(tg.run_until_disconnected(), web._run_app(app, port=9999))
        except (GracefulExit, KeyboardInterrupt):
            pass


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
