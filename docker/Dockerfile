# docker/Dockerfile
ARG VESKTOP_VERSION=1.6.5

FROM debian:bookworm-slim
ARG VESKTOP_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        wget \
        xvfb \
        dbus-x11 \
        pulseaudio \
        pulseaudio-utils \
        fonts-liberation \
        jq \
        libgbm1 \
        socat \
    && wget -q -O /tmp/vesktop.deb \
        "https://github.com/Vencord/Vesktop/releases/download/v${VESKTOP_VERSION}/vesktop_${VESKTOP_VERSION}_amd64.deb" \
    && apt-get install -y --no-install-recommends /tmp/vesktop.deb \
    && rm -f /tmp/vesktop.deb \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash vesktop
# Pre-create ~/.config/vesktop owned by vesktop: Docker creates missing parent
# dirs as root when a named volume is mounted onto a path that doesn't yet
# exist in the image, which would otherwise leave ~/.config root-owned and
# block pulseaudio/vesktop from writing their own files there.
RUN mkdir -p /home/vesktop/.config/vesktop && chown -R vesktop:vesktop /home/vesktop/.config
# Xvfb refuses to create /tmp/.X11-unix itself unless running as root
# (euid != 0 check in libxtrans), so pre-create it here as root.
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
USER vesktop
WORKDIR /home/vesktop

COPY --chown=vesktop:vesktop scripts/vesktop-entrypoint.sh /home/vesktop/vesktop-entrypoint.sh
RUN chmod +x /home/vesktop/vesktop-entrypoint.sh

ENTRYPOINT ["/home/vesktop/vesktop-entrypoint.sh"]
