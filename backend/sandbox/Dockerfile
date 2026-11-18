# 沙盒执行镜像：在 python:3.12-slim 上预装常见依赖（固定版本见 requirements.txt）。
# 沙盒运行时默认断网，依赖必须在构建期装好；构建由 scripts/build-sandbox-image.sh 完成。
FROM python:3.12-slim

# 构建期用国内 PyPI 镜像加速；仅影响构建，不写进运行环境。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /tmp/sandbox-requirements.txt
RUN pip install --no-cache-dir -r /tmp/sandbox-requirements.txt \
    && rm -f /tmp/sandbox-requirements.txt

# 主进程由 sandbox_service 以 sleep 覆盖，这里只是给直接 run 时的兜底。
CMD ["python3"]
