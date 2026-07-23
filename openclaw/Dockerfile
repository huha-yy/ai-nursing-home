FROM ghcr.io/openclaw/openclaw:2026.4.8

USER root

# === 中国镜像加速 ===
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV NODE_PATH=/usr/local/lib/node_modules

# apt: 阿里云 mirror
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true

# npm: npmmirror.com (阿里云，国内速度最快)
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com

# pip: 阿里云 PyPI mirror
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com

# HuggingFace: hf-mirror.com（whisper 模型下载加速）
ENV HF_ENDPOINT=https://hf-mirror.com

# Install Playwright MCP (skip browser download — content pipeline doesn't need Chrome)
RUN npm install -g playwright @playwright/mcp

# Document conversion tools (markitdown for PDF/Word/PPT -> Markdown) +
# httpx for the cognee/workflow custom-skill handlers (P5/P13d) +
# python-dotenv for content-pipeline scripts +
# ffmpeg + openai-whisper for speech-to-text (Phase 2 integration) +
# nano-pdf for PDF editing (Phase 2 integration) +
# easyocr for image OCR (Phase 2, installed at runtime via pip) +
# python-pptx for PPT generation (Phase 2)
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip ffmpeg \
 && pip3 install --break-system-packages --no-cache-dir markitdown[all] httpx python-dotenv openai-whisper nano-pdf python-pptx \
 && apt-get clean && rm -rf /var/lib/apt/lists/* ~/.cache/pip

# Pre-download whisper base model (~75MB) so first transcription is instant
RUN su - node -c 'python3 -c "import whisper; whisper.load_model(\"base\")" 2>&1 || true'

# Patch Feishu extension: inject chatId + error messages
COPY openclaw/patches/feishu-reply-dispatcher.ts /app/extensions/feishu/src/reply-dispatcher.ts
COPY openclaw/patches/feishu-bot.ts /app/extensions/feishu/src/bot.ts
RUN chown node:node /app/extensions/feishu/src/reply-dispatcher.ts \
    /app/extensions/feishu/src/bot.ts

# Entrypoint wrapper
COPY openclaw/entrypoint/docker-entrypoint-wrapper.sh /usr/local/bin/docker-entrypoint-wrapper.sh
RUN chmod +x /usr/local/bin/docker-entrypoint-wrapper.sh

# P13d+: admin-mgmt custom skill (Agent Manager system management)
COPY openclaw/skills/admin-mgmt/ /opt/openclaw/skills/custom/admin-mgmt/
RUN chown -R node:node /opt/openclaw/skills/custom/admin-mgmt/

# P5: cognee custom skill
COPY openclaw/skills/cognee/ /opt/openclaw/skills/custom/cognee/
RUN chown -R node:node /opt/openclaw/skills/custom/cognee/

# P13d: workflow custom skill (agent→workflow, spec §7.2)
COPY openclaw/skills/workflow/ /opt/openclaw/skills/custom/workflow/
RUN chown -R node:node /opt/openclaw/skills/custom/workflow/

# Nursing MVP skills
COPY openclaw/skills/nursing-schedule/    /opt/openclaw/skills/custom/nursing-schedule/
COPY openclaw/skills/nursing-work-order/  /opt/openclaw/skills/custom/nursing-work-order/
COPY openclaw/skills/logistics-inventory/ /opt/openclaw/skills/custom/logistics-inventory/
COPY openclaw/skills/logistics-query/     /opt/openclaw/skills/custom/logistics-query/
COPY openclaw/skills/resident-query/      /opt/openclaw/skills/custom/resident-query/
COPY openclaw/skills/staff-query/         /opt/openclaw/skills/custom/staff-query/
COPY openclaw/skills/meal-query/          /opt/openclaw/skills/custom/meal-query/
COPY openclaw/skills/activity-query/      /opt/openclaw/skills/custom/activity-query/
COPY openclaw/skills/finance-query/       /opt/openclaw/skills/custom/finance-query/
COPY openclaw/skills/alert-query/         /opt/openclaw/skills/custom/alert-query/
COPY openclaw/skills/report-generate/     /opt/openclaw/skills/custom/report-generate/
COPY openclaw/skills/self-improving/    /opt/openclaw/skills/custom/self-improving/
COPY openclaw/skills/web-content-fetcher/ /opt/openclaw/skills/custom/web-content-fetcher/
COPY openclaw/skills/openai-whisper/    /opt/openclaw/skills/custom/openai-whisper/
COPY openclaw/skills/nano-pdf/          /opt/openclaw/skills/custom/nano-pdf/
# Phase 2 features
COPY openclaw/skills/vision-ocr/        /opt/openclaw/skills/custom/vision-ocr/
COPY openclaw/skills/ppt-master/        /opt/openclaw/skills/custom/ppt-master/
# GBrain knowledge base MCP shim
COPY openclaw/skills/gbrain-mcp/       /opt/openclaw/skills/custom/gbrain-mcp/
RUN chown -R node:node /opt/openclaw/skills/custom/admin-mgmt/ \
                       /opt/openclaw/skills/custom/cognee/ \
                       /opt/openclaw/skills/custom/workflow/ \
                       /opt/openclaw/skills/custom/nursing-schedule/ \
                       /opt/openclaw/skills/custom/nursing-work-order/ \
                       /opt/openclaw/skills/custom/logistics-inventory/ \
                       /opt/openclaw/skills/custom/logistics-query/ \
                       /opt/openclaw/skills/custom/resident-query/ \
                       /opt/openclaw/skills/custom/staff-query/ \
                       /opt/openclaw/skills/custom/meal-query/ \
                       /opt/openclaw/skills/custom/activity-query/ \
                       /opt/openclaw/skills/custom/finance-query/ \
                       /opt/openclaw/skills/custom/alert-query/ \
                       /opt/openclaw/skills/custom/report-generate/ \
                       /opt/openclaw/skills/custom/self-improving/ \
                       /opt/openclaw/skills/custom/web-content-fetcher/ \
                       /opt/openclaw/skills/custom/openai-whisper/ \
                       /opt/openclaw/skills/custom/nano-pdf/ \
                       /opt/openclaw/skills/custom/vision-ocr/ \
                       /opt/openclaw/skills/custom/ppt-master/ \
                       /opt/openclaw/skills/custom/gbrain-mcp/

# ComfyUI MCP server
COPY openclaw/comfy-mcp/ /opt/openclaw/comfy-mcp/
RUN chown -R node:node /opt/openclaw/comfy-mcp/

# Content pipeline: shared scripts and configs
COPY openclaw/scripts/ /opt/openclaw/scripts/
COPY openclaw/configs/ /opt/openclaw/configs/
COPY openclaw/.env.example /opt/openclaw/.env.example
# Pre-create .env symlink targets so entrypoint-wrapper.sh (running as node)
# can ln -sf without root — /opt/openclaw/ is root-owned at this point.
RUN touch /opt/openclaw/.env /opt/openclaw/skills/.env && \
    chown node:node /opt/openclaw/.env /opt/openclaw/skills/.env
RUN chown -R node:node /opt/openclaw/scripts/ /opt/openclaw/configs/ /opt/openclaw/.env.example

# P13d: workflow→agent task receiver sidecar (stdlib-only; python3 is already
# installed above for markitdown)
COPY openclaw/receiver/dato_task_receiver.py /usr/local/bin/dato_task_receiver.py

# Pre-configure OpenClaw for DeepSeek (runs on first agent boot)
COPY openclaw/entrypoint/setup-deepseek.sh /usr/local/bin/setup-deepseek.sh
RUN chmod +x /usr/local/bin/setup-deepseek.sh

USER node
ENTRYPOINT ["docker-entrypoint-wrapper.sh"]
CMD ["openclaw", "gateway", "--allow-unconfigured"]
