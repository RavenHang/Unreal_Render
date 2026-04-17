FROM ghcr.io/epicgames/unreal-engine:dev-slim-5.7.4

USER root
RUN apt-get update && \
    apt-get install -y dos2unix && \
    rm -rf /var/lib/apt/lists/*

USER ue4
WORKDIR /workspace

COPY --chown=ue4:ue4 ./run_python_render.sh /workspace/run_python_render.sh

RUN dos2unix /workspace/run_python_render.sh && \
    chmod +x /workspace/run_python_render.sh

ENTRYPOINT ["/bin/bash", "/workspace/run_python_render.sh"]