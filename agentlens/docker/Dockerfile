FROM python:3.12-bookworm

ARG PIP_INDEX_URL=https://pypi.org/simple

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends sudo tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir supervisor

# 添加用户
RUN useradd -m -s /bin/bash log \
    && useradd -m -s /bin/bash admin && usermod -aG sudo admin

# 设置时区
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && echo 'Asia/Shanghai' > /etc/timezone

# 创建应用、日志和平台配置挂载目录
RUN mkdir -p /home/admin/agentlens /home/admin/logs/agentlens /home/admin/conf

WORKDIR /home/admin/agentlens

# 拷贝代码
COPY pyproject.toml README.md LEGAL.md ./
COPY src/ ./src
COPY env/ ./env
COPY obschema/ ./obschema

# 安装项目依赖
RUN pip3 install --no-cache-dir -e .

# 流水线启动脚本
RUN mkdir -p /home/admin/bin
COPY ./cd/*.sh /home/admin/bin/
RUN chmod a+x /home/admin/bin/*.sh

# supervisor 配置
RUN mkdir -p /etc/supervisor/conf.d /var/log/supervisor
COPY ./cd/supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY ./cd/supervisor/app.ini /etc/supervisor/conf.d/app.ini
COPY ./cd/supervisor/sc /usr/local/bin/sc
COPY ./cd/supervisor/sudoers /etc/sudoers
RUN chmod +x /usr/local/bin/sc

# 设置别名
RUN echo 'alias ll="ls -l"' >> /etc/bashrc \
    && echo 'alias g="cd /home/admin/logs/agentlens"' >> /etc/bashrc

# 修改文件属性
RUN chown -R admin:admin /home/admin
RUN chmod -R 744 /home/admin/bin

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
