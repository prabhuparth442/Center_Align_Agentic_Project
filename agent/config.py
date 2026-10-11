"""Runtime settings, read from environment variables (see .env.example)."""
import os
from dataclasses import dataclass


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # Mock environment
    mail_url: str = "http://127.0.0.1:5001"
    finance_url: str = "http://127.0.0.1:5002"
    admin_token: str = "eval-token"
    mail_user: str = "demo_user"
    mail_password: str = "demo123"          # sandbox credentials of the mock app, not real ones
    finance_user: str = "finance_user"
    finance_password: str = "finance123"    # sandbox credentials of the mock app, not real ones

    # Models (any OpenAI-compatible endpoint)
    llm_base_url: str = ""
    llm_api_key: str = ""
    planner_model: str = ""
    executor_model: str = ""

    # Control limits
    max_steps: int = 40
    max_retries: int = 2
    max_replans: int = 2
    step_timeout_s: int = 10
    token_budget: int = 400_000
    approval_threshold_inr: int = 250_000

    run_dir: str = "runs"

    @classmethod
    def from_env(cls):
        env = os.environ.get
        defaults = cls()
        return cls(
            mail_url=env("MAIL_URL", defaults.mail_url),
            finance_url=env("FINANCE_URL", defaults.finance_url),
            admin_token=env("ADMIN_TOKEN", defaults.admin_token),
            mail_user=env("MAIL_USER", defaults.mail_user),
            mail_password=env("MAIL_PASSWORD", defaults.mail_password),
            finance_user=env("FINANCE_USER", defaults.finance_user),
            finance_password=env("FINANCE_PASSWORD", defaults.finance_password),
            llm_base_url=env("LLM_BASE_URL", defaults.llm_base_url),
            llm_api_key=env("LLM_API_KEY", defaults.llm_api_key),
            planner_model=env("PLANNER_MODEL", defaults.planner_model),
            executor_model=env("EXECUTOR_MODEL", defaults.executor_model),
            max_steps=_int("MAX_STEPS", defaults.max_steps),
            max_retries=_int("MAX_RETRIES", defaults.max_retries),
            max_replans=_int("MAX_REPLANS", defaults.max_replans),
            step_timeout_s=_int("STEP_TIMEOUT_S", defaults.step_timeout_s),
            token_budget=_int("TOKEN_BUDGET", defaults.token_budget),
            approval_threshold_inr=_int("APPROVAL_THRESHOLD_INR", defaults.approval_threshold_inr),
            run_dir=env("RUN_DIR", defaults.run_dir),
        )
