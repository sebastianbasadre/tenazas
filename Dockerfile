FROM node:24-bookworm
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv ffmpeg curl nmap iputils-ping dnsutils docker.io poppler-utils && rm -rf /var/lib/apt/lists/*
ARG OPENCLAW_VERSION=2026.6.1
ARG HIMALAYA_VERSION=v1.2.0
RUN npm install -g "openclaw@${OPENCLAW_VERSION}"
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --break-system-packages -r /tmp/requirements.txt && rm /tmp/requirements.txt
RUN curl -sL "https://github.com/pimalaya/himalaya/releases/download/${HIMALAYA_VERSION}/himalaya.x86_64-linux.tgz" -o /tmp/himalaya.tgz && tar xzf /tmp/himalaya.tgz -C /usr/local/bin/ && chmod +x /usr/local/bin/himalaya && rm /tmp/himalaya.tgz
RUN useradd -m -u 1026 -s /bin/bash tenazas
RUN echo "precedence ::ffff:0:0/96 100" >> /etc/gai.conf
RUN usermod -aG docker tenazas
ENV PYTHONPATH=/home/tenazas/runtime
USER tenazas
WORKDIR /home/tenazas
CMD ["openclaw", "gateway"]
