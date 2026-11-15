"""应用配置：缺字段直接失败，禁止默认拼凑。"""

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 同时灌进 os.environ：pydantic-settings 只服务自己的字段，
# LiteLLM 这类直接读环境变量的库（如 LITELLM_LOCAL_MODEL_COST_MAP）也吃同一份配置。
load_dotenv()


class Settings(BaseSettings):
    """从环境变量加载运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    app_name: str = Field(alias="APP_NAME")
    database_url: str = Field(alias="DATABASE_URL")
    # 向量存储与业务数据库分离，可独立切换实现：
    #   auto    = 按 DATABASE_URL 推导（PG → pgvector，SQLite → sqlite 向量文件）
    #   pgvector= 复用业务 PG 库建 pgvector 向量表（要求 DATABASE_URL 是 postgresql）
    #   sqlite  = 独立 SQLite 向量文件（路径见 VECTOR_SQLITE_PATH），应用侧余弦
    #   none    = 显式停用：依赖向量的操作全部显式失败
    # 后续接专用向量库（Milvus / Qdrant 等）时在 app.vector_store 新增实现
    vector_backend: str = Field(alias="VECTOR_BACKEND", default="auto")
    # sqlite 向量库文件路径；与业务库分开，便于单独备份/迁移
    vector_sqlite_path: str = Field(
        alias="VECTOR_SQLITE_PATH", default="./data/vectors.db"
    )
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
    # LiteLLM 用本地价格表：置 True 不拉 GitHub（拉取超时会让进程首个回合白等 10+ 秒）。
    # 字段进 Settings（extra=forbid 要求 .env 的键都有归属）；litellm 本身从
    # os.environ 读，靠文件头的 load_dotenv 灌入。
    litellm_local_model_cost_map: bool = Field(
        alias="LITELLM_LOCAL_MODEL_COST_MAP", default=False
    )
    embedding_base_url: str = Field(alias="EMBEDDING_BASE_URL")
    embedding_model: str = Field(alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(alias="EMBEDDING_DIM")
    # 嵌入服务鉴权密钥；内网无鉴权服务留空（不带 Authorization 头）
    embedding_api_key: str = Field(alias="EMBEDDING_API_KEY", default="")
    # 上传原件的落盘目录（按文档 id 存原始字节，供「下载源文件」）
    knowledge_upload_dir: str = Field(
        alias="KNOWLEDGE_UPLOAD_DIR", default="./data/uploads"
    )
    # 内置知识库语料目录：目录内 .md/.txt 改动自动同步为内置（vault）文档，
    # 文件是唯一事实源，应用内 vault 文档只读。语料在仓库根目录，随 git 管理。
    knowledge_vault_dir: str = Field(
        alias="KNOWLEDGE_VAULT_DIR", default="../内置知识库"
    )
    host: str = Field(alias="HOST")
    port: int = Field(alias="PORT")
    # 对外可达的后端基址（OAuth 回调等需要浏览器访问的场景）；
    # 留空则回退 http://{host}:{port}
    public_base_url: str = Field(alias="PUBLIC_BASE_URL", default="")
    log_level: str = Field(alias="LOG_LEVEL")
    # 可选 OTLP：未配置则不装 exporter，禁止假装已观测
    otlp_endpoint: str = Field(alias="OTLP_ENDPOINT", default="")
    otlp_service_name: str = Field(alias="OTLP_SERVICE_NAME", default="ai-workbench")
    # 沙盒：Docker 守护进程地址；留空则用本机默认（docker.from_env）
    sandbox_docker_host: str = Field(alias="SANDBOX_DOCKER_HOST", default="")
    # 代码执行器实现，与向量存储一样按配置切换：
    #   docker = llm-sandbox 容器隔离（生产口径，需 Docker 守护进程）
    #   local  = 本地不安全执行：直接用当前解释器跑用户代码，无隔离，
    #            仅供可信开发环境（无 Docker、单人本机）使用；显式选择才生效
    sandbox_executor: str = Field(alias="SANDBOX_EXECUTOR", default="docker")
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
