FROM        python:3.13.1-alpine@sha256:f9d772b2b40910ee8de2ac2b15ff740b5f26b37fc811f6ada28fce71a2542b0e

# renovate: datasource=repology depName=alpine_3_21/gcc versioning=loose
ARG         GCC_VERSION="14.2.0-r4"
# renovate: datasource=repology depName=alpine_3_21/build-base versioning=loose
ARG         BUILD_BASE_VERSION="0.5-r3"
# renovate: datasource=repology depName=alpine_3_21/libffi-dev versioning=loose
ARG         LIBFFI_VERSION="3.4.6-r0"
# renovate: datasource=repology depName=alpine_3_21/libretls-dev versioning=loose
ARG         LIBRETLS_VERSION="3.7.0-r2"
# renovate: datasource=repology depName=alpine_3_21/cargo versioning=loose
ARG         CARGO_VERSION="1.83.0-r0"

ARG         TARGETPLATFORM

WORKDIR     /app

ADD         requirements.txt .

RUN         --mount=type=cache,sharing=locked,target=/root/.cache,id=home-cache-$TARGETPLATFORM \
            --mount=type=cache,sharing=locked,target=/root/.cargo,id=home-cargo-$TARGETPLATFORM \
            apk add --no-cache \
              libgcc=${GCC_VERSION} \
            && \
            apk add --no-cache --virtual .build-deps \
              gcc=${GCC_VERSION} \
              build-base=${BUILD_BASE_VERSION} \
              libffi-dev=${LIBFFI_VERSION} \
              libretls-dev=${LIBRETLS_VERSION} \
              cargo=${CARGO_VERSION} \
            && \
            pip install -r requirements.txt && \
            apk del .build-deps && \
            chown -R nobody:nogroup /app

COPY        --chown=nobody:nogroup . .

USER        nobody

EXPOSE      9999
VOLUME      /app/data
ENV         TG_APP_ID=""
ENV         TG_APP_HASH=""
ENV         FUNKWHALE_APP_TOKEN=""
ENV         FUNKWHALE_BASE_URL=""

ENTRYPOINT [ "python", "main.py" ]
