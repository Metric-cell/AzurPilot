# docker build -t hgjazhgj/alas:latest .
# docker run -v ${PWD}:/app/AzurLaneAutoScript -p 22267:22267 --name alas -it --rm hgjazhgj/alas

# 使用 Python 3.13 作为基础镜像
# Python 3.14 官方镜像尚未发布，Python 3.13 与 3.14 高度兼容
FROM python:3.13-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_INDEX_URL=
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=127.0.0.1,localhost

ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}
ENV http_proxy=${HTTP_PROXY}
ENV https_proxy=${HTTPS_PROXY}
ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV no_proxy=${NO_PROXY}
ENV NO_PROXY=${NO_PROXY}

WORKDIR /app/AzurLaneAutoScript

COPY requirements.txt /tmp/requirements.txt

# Configure apt proxy if needed
RUN if [ -n "$HTTP_PROXY" ]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\n' "$HTTP_PROXY" "$HTTPS_PROXY" > /etc/apt/apt.conf.d/99proxy; \
    fi

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        adb \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
        openssh-client \
        build-essential \
        pkg-config \
        libavformat-dev \
        libavcodec-dev \
        libavdevice-dev \
        libavutil-dev \
        libswscale-dev \
        libswresample-dev \
        libavfilter-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Configure git
RUN git config --global --add safe.directory '*' && \
    if [ -n "$HTTP_PROXY" ]; then \
        git config --global http.proxy "$HTTP_PROXY" && \
        git config --global https.proxy "$HTTPS_PROXY"; \
    fi

# Upgrade pip
RUN pip install --upgrade pip

# Install Cython (required for some packages)
RUN pip install Cython==0.29.37

# Install av (remove version constraint and --no-build-isolation flag)
# av 10.0.0 may have compatibility issues with Python 3.13
RUN pip install av

# Install remaining requirements
RUN pip install -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt && \
    rm -rf ~/.cache/pip

CMD ["python", "gui.py"]
