FROM python:3.13.2-slim

# 固定已在 Phase 0 实机验证的 oasdiff 版本与官方发布包校验值。
ARG OASDIFF_VERSION=1.28.0
ARG OASDIFF_LINUX_AMD64_SHA256=e0ef076f2cf953d922addc04be9c3851cf3ec18f7678d2b94d44cea23dca51b5

# Set working directory
WORKDIR /app

# Set non-sensitive environment variables
ARG APP_ENV=production

ENV APP_ENV=${APP_ENV} \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# 切换 apt 源为阿里云镜像（加速国内服务器构建）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    libpq-dev \
    && pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install uv -i https://mirrors.aliyun.com/pypi/simple/ \
    && rm -rf /var/lib/apt/lists/*

# 从官方 release 安装 oasdiff，并在解包前验证 SHA256。
RUN curl -fsSL \
    "https://github.com/oasdiff/oasdiff/releases/download/v${OASDIFF_VERSION}/oasdiff_${OASDIFF_VERSION}_linux_amd64.tar.gz" \
    -o /tmp/oasdiff.tar.gz \
    && echo "${OASDIFF_LINUX_AMD64_SHA256}  /tmp/oasdiff.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/oasdiff.tar.gz -C /usr/local/bin oasdiff \
    && oasdiff --version \
    && rm -f /tmp/oasdiff.tar.gz

# 同时拷贝锁定文件，确保容器与本地安装完全相同的版本
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --index-url https://mirrors.aliyun.com/pypi/simple/

# Copy the application
COPY . .

# Make entrypoint script executable - do this before changing user
RUN chmod +x /app/scripts/docker-entrypoint.sh

# Create a non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Create log directory
RUN mkdir -p /app/logs

# Default port
EXPOSE 8000

# Log the environment we're using
RUN echo "Using ${APP_ENV} environment"

# Command to run the application
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
