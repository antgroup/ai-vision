# ── Stage 1: 构建阶段 ───────────────────────────────────────────────────────
FROM python:3.12-bookworm AS builder

RUN pip install --no-cache-dir uv==0.8.17

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends python3-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /src

COPY aivision-py-be/ /src/aivision-py-be/

WORKDIR /src/aivision-py-be
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip compile -i https://mirrors.aliyun.com/pypi/simple/ pyproject.toml -o requirements.txt && \
    uv pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# ── Stage 2: 运行阶段 ───────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm

# 设置阿里云镜像源并安装必要工具
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    supervisor sudo dumb-init tzdata procps curl netcat-openbsd wget vim iputils-ping telnet net-tools \
    libpam-modules && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 创建 admin 和 log 用户
RUN groupadd -g 1002 admin && \
    useradd -r -u 1002 -g admin -s /bin/bash -m admin && \
    groupadd -g 1003 log && \
    useradd -r -u 1003 -g log -s /bin/bash log && \
    mkdir -p /home/admin/aivision && \
    mkdir -p /home/admin/logs/aivision && \
    mkdir -p /var/log/supervisor && \
    mkdir -p /var/log/aivision && \
    chown -R log:log /var/log/aivision

# 配置 su 权限和 admin 用户组
RUN chmod u+s /bin/su && \
    usermod -aG root admin

# 配置 PAM 允许 root 无密码 su 到任何用户
RUN mkdir -p /etc/pam.d && \
    echo "auth        sufficient  pam_rootok.so" > /etc/pam.d/su && \
    echo "auth        sufficient  pam_permit.so" >> /etc/pam.d/su && \
    echo "account     required    pam_permit.so" >> /etc/pam.d/su && \
    echo "session     required    pam_unix.so" >> /etc/pam.d/su && \
    echo "session     optional    pam_keyinit.so force revoke" >> /etc/pam.d/su

# 创建 bash_logout 文件
RUN touch /root/.bash_logout && chmod a+rw /root/.bash_logout && \
    touch /home/admin/.bash_logout && chmod a+r /home/admin/.bash_logout && chown admin:admin /home/admin/.bash_logout

COPY --from=builder --chown=admin:admin /opt/venv /opt/venv
COPY --from=builder --chown=admin:admin /src/aivision-py-be /home/admin/aivision

RUN mkdir -p /home/admin/aivision/staticfiles && \
    chown -R admin:admin /home/admin/aivision/staticfiles

COPY scripts/docker/bin/ /home/admin/bin/
COPY scripts/docker/supervisord.conf /etc/supervisord.conf
COPY scripts/docker/supervisord.d/ /etc/supervisord.d/
COPY scripts/docker/init_source.sh /init_source.sh
COPY scripts/docker/sc /usr/local/bin/sc
COPY scripts/docker/sudoers /etc/sudoers.d/admin

RUN chmod +x /home/admin/bin/*.sh && \
    chmod +x /usr/local/bin/sc && \
    chmod 440 /etc/sudoers.d/admin && \
    mkdir -p /home/admin/conf && \
    chown -R admin:admin /home/admin && \
    echo "export LANG=zh_CN.UTF8" >> /etc/profile && \
    echo "source /init_source.sh" >> /etc/rc.local

ENV PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=aivision_backend.settings \
    PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["/bin/sh", "-c", "mkdir -p /home/admin/bin && env | sed 's/^/export /' > /home/admin/bin/cur.env && exec tail -f /dev/null"]
CMD []
