FROM python:3.11-bookworm

# ---------------- Install Node.js 18 ----------------
RUN apt-get update && apt-get install -y ca-certificates curl gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_18.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# ---------------- System dependencies ----------------
RUN apt-get update && apt-get install -y \
    python3-pip \
    python-is-python3 \
    blender \
    ffmpeg \
    git \
    curl \
    openjdk-17-jre \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------------- Copy project ----------------
COPY . .

# ---------------- AUTO FIX MCprep FOLDER ----------------
RUN if [ -d "MCprep_addon" ]; then mv MCprep_addon mcprep; fi && \
    if [ -d "MCPrep_addon" ]; then mv MCPrep_addon mcprep; fi && \
    if [ -d "MCPREP_addon" ]; then mv MCPREP_addon mcprep; fi

# ---------------- Python dependencies ----------------
RUN pip3 install --no-cache-dir -r requirements.txt

# ---------------- Node.js dependencies ----------------
RUN cd processor && npm install --production --no-audit --prefer-offline

# ---------------- Install MCprep into Blender ----------------
RUN mkdir -p /usr/share/blender/scripts/addons && \
    cp -r mcprep /usr/share/blender/scripts/addons/mcprep || true

# Download latest jmc2obj.jar
RUN curl -L -o /app/processor/jmc2obj.jar https://github.com/jmc2obj/j-mc-2-obj/releases/latest/download/jmc2obj.jar || echo "Warning: jmc2obj download failed"

# ---------------- Extra Python packages ----------------
RUN pip3 install --no-cache-dir pillow

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV NODE_ENV=production

CMD ["python3", "bot/bot.py"]
