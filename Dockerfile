FROM python:alpine

WORKDIR /app

ADD requirements.txt .

RUN apk add --no-cache --virtual .build-deps gcc build-base libffi-dev libretls-dev cargo && \
    pip install -r requirements.txt && \
    apk del .build-deps && \
    rm -rf /root/.cache /root/.cargo && \
    chown -R nobody:nogroup /app

COPY --chown=nobody:nogroup . .

USER nobody

EXPOSE 9999
VOLUME /app/data
ENV TG_APP_ID=""
ENV TG_APP_HASH=""
ENV FUNKWHALE_APP_TOKEN=""
ENV FUNKWHALE_BASE_URL=""

ENTRYPOINT [ "python", "main.py" ]