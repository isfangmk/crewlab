"""应用配置：缺字段直接失败，禁止默认拼凑。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量加载运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    app_name: str = Field(alias="APP_NAME")
    database_url: str = Field(alias="DATABASE_URL")
    # bootstrap 首个管理员的登录邮箱；密码用 AUTH_TOKEN
    auth_admin_email: str = Field(alias="AUTH_ADMIN_EMAIL")
    auth_token: str = Field(alias="AUTH_TOKEN")
    # 凭证库字段的加密密钥：与登录口令解耦。
    # 轮换 AUTH_TOKEN 不得让已存凭证永久不可解。
    credential_encryption_key: str = Field(alias="CREDENTIAL_ENCRYPTION_KEY")
    # 首次启动时写入「配置-模型」的种子；正式对话仍只读数据库配置
    seed_llm_provider: str = Field(alias="SEED_LLM_PROVIDER")
    seed_llm_base_url: str = Field(alias="SEED_LLM_BASE_URL")
    seed_llm_api_key: str = Field(alias="SEED_LLM_API_KEY")
    seed_llm_model: str = Field(alias="SEED_LLM_MODEL")
    # 种子 provider 的 litellm 模型名前缀（如 openai/）；空串表示不加前缀
    seed_llm_model_prefix: str = Field(alias="SEED_LLM_MODEL_PREFIX", default="")
    # 种子 provider 的厂商专属请求体（JSON 对象文本）
    seed_llm_extra_body_json: str = Field(alias="SEED_LLM_EXTRA_BODY_JSON", default="")
    embedding_base_url: str = Field(alias="EMBEDDING_BASE_URL")
    embedding_model: str = Field(alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(alias="EMBEDDING_DIM")
    host: str = Field(alias="HOST")
    port: int = Field(alias="PORT")
    log_level: str = Field(alias="LOG_LEVEL")
    # 可选 OTLP：未配置则不装 exporter，禁止假装已观测
    otlp_endpoint: str = Field(alias="OTLP_ENDPOINT", default="")
    otlp_service_name: str = Field(alias="OTLP_SERVICE_NAME", default="ai-workbench")
    # 沙盒：Docker 守护进程地址；留空则用本机默认（docker.from_env）
    sandbox_docker_host: str = Field(alias="SANDBOX_DOCKER_HOST", default="")
    # 沙盒镜像：只做 Python，够用且体积小
    sandbox_image: str = Field(
        alias="SANDBOX_IMAGE", default="python:3.12-slim"
    )
    # 沙盒默认是否联网：默认断网，防数据外泄
    sandbox_network_enabled: bool = Field(alias="SANDBOX_NETWORK_ENABLED", default=False)
    # 沙盒单次执行超时（秒）
    sandbox_timeout_seconds: int = Field(
        alias="SANDBOX_TIMEOUT_SECONDS", default=120
    )


@lru_cache
def get_settings() -> Settings:
    """读取并缓存配置；环境缺项由 pydantic 显式报错。"""

    # 必填字段由环境变量注入；静态检查看不到运行时 env
    return Settings()  # pyright: ignore[reportCallIssue]
