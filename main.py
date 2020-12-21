import asyncio
import os
import typing

from aiohttp import web, ClientSession
from aiohttp.web_runner import GracefulExit

from telethon import TelegramClient

if typing.TYPE_CHECKING:
    from telethon.tl.custom.message import Message

TG_APP_ID = os.getenv("TG_APP_ID")
TG_APP_HASH = os.getenv("TG_APP_HASH")

FUNKWHALE_APP_TOKEN = os.getenv("FUNKWHALE_APP_TOKEN")
FUNKWHALE_BASE_URL = os.getenv("FUNKWHALE_BASE_URL")

if any(not x for x in (TG_APP_ID, TG_APP_HASH, FUNKWHALE_APP_TOKEN, FUNKWHALE_BASE_URL)):
    raise Exception("Missing/empty env vars")


async def main():
    app = web.Application()
    routes = web.RouteTableDef()

    async with ClientSession(
        headers={"Authorization": 'Bearer ' + FUNKWHALE_APP_TOKEN}, raise_for_status=True
    ) as funkwhale_sess, TelegramClient(
        'data/tg.session', TG_APP_ID, TG_APP_HASH
    ) as client:
        await client.start()
        print("Started")

        async def track_gen(ref):
            imp = await funkwhale_sess.get(FUNKWHALE_BASE_URL + '/api/v1/uploads/', params={'import_reference': ref, 'page_size': 25})
            imp = await imp.json()
            for x in imp['results']:
                yield x

            while imp['next'] is not None:
                imp = await funkwhale_sess.get(imp['next'])
                imp = await imp.json()
                for x in imp['results']:
                    yield x

        @routes.get('/tginfo/{chat_id}/{msg_id}')
        async def msg_info(req):
            chat = await client.get_entity(int(req.match_info['chat_id']))
            if chat is None:
                raise web.HTTPNotFound()

            msg: Message = await client.get_messages(chat, ids=int(req.match_info['msg_id']))
            if msg is None:
                raise web.HTTPNotFound()

            reply: Message = await msg.get_reply_message()

            resp = f"sender:\n{msg.sender_id}\n\nmsg:\n{msg.to_dict()}"
            if reply is not None:
                resp += f"\n\nreply_sender:\n{reply.sender_id}\n\nreply:\n{reply.to_dict()}"

            return web.Response(text=resp)

        @routes.get('/funkwhale_import_info/{import_ref}')
        async def import_info(req):
            ref = req.match_info['import_ref']

            return web.json_response([x async for x in track_gen(ref)])

        @routes.get('/update_tags/{import_ref}/{chat_id}/{msg_id}')
        async def update_tags(req):
            out = ""
            ref = req.match_info['import_ref']

            chat = await client.get_entity(int(req.match_info['chat_id']))
            if chat is None:
                raise web.HTTPNotFound()

            msg: Message = await client.get_messages(chat, ids=int(req.match_info['msg_id']))
            if msg is None:
                raise web.HTTPNotFound()

            new_tags = [f'tg_by_{msg.sender_id}']

            if msg.forward is not None and msg.forward.sender_id:
                new_tags.append(f"tg_fwd_from_{msg.forward.sender_id}")

            reply: Message = await msg.get_reply_message()
            if reply is not None:
                new_tags.append(f"tg_reply_to_by_{reply.sender_id}")

                if reply.forward is not None and reply.forward.sender_id:
                    new_tags.append(f"tg_reply_to_fwd_from_{reply.forward.sender_id}")

            tmp = f"gen tags: {new_tags}"
            out += tmp + "\n"

            async for track_i in track_gen(ref):
                if track_i['import_status'] in ("skipped", "finished"):
                    id_ = track_i['track']['id']

                    # workaround, as track object from import doesn't contain tags (wtf? yes. always empty array)
                    track = await funkwhale_sess.get(FUNKWHALE_BASE_URL + f"/api/v1/tracks/{id_}", params={
                        'refresh': 'true'
                    })
                    track = await track.json()
                    tags = track['tags']

                    # testing if workaround still needed
                    print('track:', id_, 'import tags:', track_i['track']['tags'], 'track tags:', tags)

                    ntags = set(tags + new_tags)

                    if ntags != set(tags):
                        await funkwhale_sess.post(FUNKWHALE_BASE_URL + f"/api/v1/tracks/{track['id']}/mutations/", json={
                            "type": "update",
                            "payload": {"tags": list(ntags)},
                            "summary": f"Sent in Telegram message: https://t.me/c/{chat.id}/{msg.id}",
                            "is_approved": True
                        })

                    tmp = f"found track: {id_} with tags {tags}"
                    out += tmp + "\n"

            return web.Response(text=out)

        app.add_routes(routes)
        try:
            await asyncio.gather(client.run_until_disconnected(), web._run_app(app, port=9999))
        except (GracefulExit, KeyboardInterrupt):
            pass


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
