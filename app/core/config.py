import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings


class ModelEntry(BaseModel):
    id: str
    display_name: str


class LLMConfig(BaseModel):
    base_url: str = "http://localhost/v1"
    api_key: str = "none"
    default_model: str = "gptoss120b"
    timeout_seconds: int = 120
    max_tokens: int = 4096
    temperature: float = 0.2


class JavaConfig(BaseModel):
    repo_path: str = ""
    exclude_dirs: List[str] = ["target", ".git", "node_modules", ".idea"]
    max_file_size_kb: int = 500


class ConfluenceConfig(BaseModel):
    base_url: str = ""
    username: str = ""
    api_token: str = ""
    default_space: str = ""


class ContextConfig(BaseModel):
    max_tokens: int = 32000
    max_file_context_kb: int = 100


class UploadsConfig(BaseModel):
    directory: str = "./uploads"
    max_file_size_mb: int = 50
    cleanup_after_hours: int = 24


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False


class Settings(BaseModel):
    llm: LLMConfig = LLMConfig()
    models: List[ModelEntry] = []
    java: JavaConfig = JavaConfig()
    confluence: ConfluenceConfig = ConfluenceConfig()
    context: ContextConfig = ContextConfig()
    uploads: UploadsConfig = UploadsConfig()
    server: ServerConfig = ServerConfig()

    def apply_env_overrides(self) -> "Settings":
        if os.getenv("LLM_BASE_URL"):
            self.llm.base_url = os.getenv("LLM_BASE_URL")
        if os.getenv("LLM_API_KEY"):
            self.llm.api_key = os.getenv("LLM_API_KEY")
        if os.getenv("JAVA_REPO_PATH"):
            self.java.repo_path = os.getenv("JAVA_REPO_PATH")
        if os.getenv("CONFLUENCE_BASE_URL"):
            self.confluence.base_url = os.getenv("CONFLUENCE_BASE_URL")
        if os.getenv("CONFLUENCE_USERNAME"):
            self.confluence.username = os.getenv("CONFLUENCE_USERNAME")
        if os.getenv("CONFLUENCE_API_TOKEN"):
            self.confluence.api_token = os.getenv("CONFLUENCE_API_TOKEN")
        return self


def load_settings(config_path: str = "config.yaml") -> Settings:
    from dotenv import load_dotenv
    load_dotenv()

    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    settings = Settings(**data)
    settings.apply_env_overrides()

    # Ensure upload directory exists
    Path(settings.uploads.directory).mkdir(parents=True, exist_ok=True)

    return settings


settings: Settings = load_settings()
