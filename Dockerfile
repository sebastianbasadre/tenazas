FROM node:24-bookworm
RUN apt-get update && apt-get install -y python3 python3-pip python3-venv ffmpeg curl nmap iputils-ping dnsutils docker.io && rm -rf /var/lib/apt/lists/*
RUN npm install -g openclaw@latest
RUN pip3 install --break-system-packages requests torch --index-url https://download.pytorch.org/whl/cpu
RUN pip3 install --break-system-packages openai-whisper tqdm tiktoken numba more-itertools
RUN pip3 install --break-system-packages google-auth google-auth-oauthlib google-api-python-client
RUN pip3 install --break-system-packages prometheus_client pycryptodome tplinkrouterc6u python-miio pychromecast websocket-client
RUN curl -sL "https://github.com/pimalaya/himalaya/releases/latest/download/himalaya.x86_64-linux.tgz" -o /tmp/himalaya.tgz && tar xzf /tmp/himalaya.tgz -C /usr/local/bin/ && chmod +x /usr/local/bin/himalaya && rm /tmp/himalaya.tgz
RUN useradd -m -u 1026 -s /bin/bash tenazas
RUN echo "precedence ::ffff:0:0/96 100" >> /etc/gai.conf
RUN usermod -aG docker tenazas
ENV PYTHONPATH=/home/tenazas/runtime
USER tenazas
WORKDIR /home/tenazas
CMD ["openclaw", "gateway"]
