FROM python:alpine

WORKDIR /app

RUN chown nobody:nogroup /app \
	&& apk add --no-cache --virtual .build-deps gcc build-base libffi-dev libretls-dev

ADD requirements.txt .
RUN pip install -r requirements.txt \
	&& apk del .build-deps

COPY --chown=nobody:nogroup . .
USER nobody

EXPOSE 9999
VOLUME /app/data
ENV TG_APP_ID=""
ENV TG_APP_HASH=""
ENV FUNKWHALE_APP_TOKEN=""
ENV FUNKWHALE_BASE_URL=""

ENTRYPOINT [ "python", "main.py" ]
