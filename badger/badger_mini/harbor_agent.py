"""Harbor external agent that runs mini-swe-agent against a Terminal-Bench task container.

    harbor run -d terminal-bench@2.0 --agent-import-path badger_mini.harbor_agent:BadgerMiniAgent -i fix-git

The LLM client (litellm, via mini-swe-agent) runs on the host, so the API key never enters the
task container; only bash commands go in, through Harbor's ``environment.exec()``.

mini-swe-agent's loop is synchronous, Harbor's is async: the agent runs in a worker thread and
each command is scheduled back onto Harbor's event loop.

Settings (env vars; secrets come from ``op run --env-file=badger/.env.op``, tuning from ``badger/.env``):
  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL   endpoint (LLM_MODEL wins over harbor's -m)
  LLM_LITELLM_PROVIDER                   litellm provider prefix for LLM_MODEL (default: openai)
  LLM_MAX_TOKENS, LLM_TEMPERATURE, LLM_TOP_P
  AGENT_MAX_TURNS, AGENT_COMMAND_TIMEOUT_SEC
  BADGER_CONFIG                          agent yaml (default: config/terminal_bench.yaml)
"""

import asyncio
import os
import shlex
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")

from harbor.agents.base import BaseAgent  # noqa: E402
from harbor.environments.base import BaseEnvironment  # noqa: E402
from harbor.models.agent.context import AgentContext  # noqa: E402
from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.exceptions import Submitted  # noqa: E402
import litellm  # noqa: E402
from minisweagent.models import get_model  # noqa: E402
from minisweagent.models.litellm_model import LitellmModel  # noqa: E402
from pydantic import BaseModel  # noqa: E402

PACKAGE_DIR = Path(__file__).resolve().parent
# override=False: values injected by `op run` must win over anything in badger/.env
load_dotenv(PACKAGE_DIR.parent / ".env", override=False)

SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class HarborEnvironmentConfig(BaseModel):
    timeout: int = 180
    """Per-command timeout (s). Enforced in the container so partial output survives a timeout."""
    cwd: str = ""
    """Working directory; empty = the task image's WORKDIR."""
    env: dict[str, str] = {}


class HarborEnvironment:
    """mini-swe-agent Environment that executes commands in Harbor's task container."""

    def __init__(self, environment: BaseEnvironment, loop: asyncio.AbstractEventLoop, stop: threading.Event, **kwargs):
        self.config = HarborEnvironmentConfig(**kwargs)
        self._environment = environment
        self._loop = loop
        self._stop = stop

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        if self._stop.is_set():
            raise RuntimeError("Harbor stopped the agent (task timeout)")
        timeout = timeout or self.config.timeout
        # `timeout` kills the command inside the container but keeps what it printed so far;
        # Harbor's own timeout (the outer bound) would discard all output.
        inner = shlex.quote(action.get("command", ""))
        command = (
            f"if command -v timeout >/dev/null 2>&1; then timeout -k 5 {timeout} bash -c {inner}; "
            f"else bash -c {inner}; fi"
        )
        start = time.monotonic()
        future = asyncio.run_coroutine_threadsafe(
            self._environment.exec(
                command=command,
                cwd=cwd or self.config.cwd or None,
                env=self.config.env or None,
                timeout_sec=timeout + 30,
            ),
            self._loop,
        )
        try:
            result = future.result()
            output = {
                "output": (result.stdout or "") + (result.stderr or ""),
                "returncode": result.return_code,
                "exception_info": "",
            }
            if result.return_code in (124, 137) and time.monotonic() - start >= timeout:
                output["exception_info"] = (
                    f"The command was killed after the {timeout}s timeout; output so far is shown. "
                    "For long builds/training, run in the background with a log file "
                    "(nohup cmd > /tmp/job.log 2>&1 &) and check the log in later commands."
                )
        except Exception as e:
            output = {
                "output": "",
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict):
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == SUBMIT_MARKER and output["returncode"] == 0:
            submission = "".join(lines[1:])
            raise Submitted(
                {"role": "exit", "content": submission, "extra": {"exit_status": "Submitted", "submission": submission}}
            )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.config.model_dump() | kwargs

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }


class BadgerLitellmModel(LitellmModel):
    """LitellmModel that doesn't retry 400s: a wrong model id or bad param never fixes itself."""

    abort_exceptions = [*LitellmModel.abort_exceptions, litellm.exceptions.BadRequestError]


# Endpoint misconfiguration: fail the trial loudly instead of letting every task "finish" with no work.
CONFIG_ERRORS = (
    litellm.exceptions.AuthenticationError,
    litellm.exceptions.NotFoundError,
    litellm.exceptions.PermissionDeniedError,
)


def _is_config_error(e: Exception) -> bool:
    if isinstance(e, litellm.exceptions.ContextWindowExceededError):
        return False
    return isinstance(e, CONFIG_ERRORS) or isinstance(e, litellm.exceptions.BadRequestError)


class HarborSyncedAgent(DefaultAgent):
    """DefaultAgent that mirrors token usage into Harbor's AgentContext after every message.

    Harbor reads the context after run() returns *or is cancelled on timeout*, so it must be
    current at all times. Usage is taken from every stored response, including ones that ended
    in a FormatError (e.g. a reasoning overrun with no tool call) - those tokens are billed too.
    """

    def __init__(self, *args, harbor_context: AgentContext, stop: threading.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.harbor_context = harbor_context
        self._stop = stop
        self.n_input_tokens = 0
        self.n_output_tokens = 0
        self.n_cache_tokens = 0

    def query(self) -> dict:
        if self._stop.is_set():
            raise RuntimeError("Harbor stopped the agent (task timeout)")
        return super().query()

    def add_messages(self, *messages: dict) -> list[dict]:
        for message in messages:
            response = (message.get("extra") or {}).get("response")
            usage = (response.get("usage") if isinstance(response, dict) else None) or {}
            self.n_input_tokens += usage.get("prompt_tokens") or 0
            self.n_output_tokens += usage.get("completion_tokens") or 0
            self.n_cache_tokens += (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        added = super().add_messages(*messages)
        ctx = self.harbor_context
        ctx.n_input_tokens = self.n_input_tokens
        ctx.n_output_tokens = self.n_output_tokens
        ctx.n_cache_tokens = self.n_cache_tokens
        ctx.metadata = {
            "n_model_calls": self.n_calls,
            "n_messages": len(self.messages),
            "exit_status": (self.messages[-1].get("extra") or {}).get("exit_status", "") if self.messages else "",
            "trajectory_path": str(self.config.output_path),
        }
        return added


def _load_config() -> dict:
    path = Path(os.environ.get("BADGER_CONFIG") or PACKAGE_DIR / "config" / "terminal_bench.yaml")
    if not path.is_absolute() and not path.exists():
        path = PACKAGE_DIR / "config" / path
    return yaml.safe_load(path.read_text())


def _model_config(config: dict, harbor_model_name: str | None) -> dict:
    """Build the litellm model config from env vars; endpoint changes never need code changes."""
    model_id = os.environ.get("LLM_MODEL")
    if model_id:
        model_name = f"{os.environ.get('LLM_LITELLM_PROVIDER', 'openai')}/{model_id}"
    elif harbor_model_name:
        model_name = harbor_model_name
    else:
        raise ValueError("No model configured. Set LLM_MODEL (badger/.env.op) or pass -m to harbor run.")
    model_kwargs = dict(config.get("model_kwargs") or {})
    for env_key, kwarg, cast in [
        ("LLM_BASE_URL", "api_base", str),
        ("LLM_API_KEY", "api_key", str),
        ("LLM_MAX_TOKENS", "max_tokens", int),
        ("LLM_TEMPERATURE", "temperature", float),
        ("LLM_TOP_P", "top_p", float),
    ]:
        if os.environ.get(env_key):
            model_kwargs[kwarg] = cast(os.environ[env_key])
    return {**config, "model_name": model_name, "model_kwargs": model_kwargs}


class BadgerMiniAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "badger-mini-swe-agent"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        config = _load_config()
        agent_config = dict(config.get("agent") or {})
        if os.environ.get("AGENT_MAX_TURNS"):
            agent_config["step_limit"] = int(os.environ["AGENT_MAX_TURNS"])
        env_config = dict(config.get("environment") or {})
        if os.environ.get("AGENT_COMMAND_TIMEOUT_SEC"):
            env_config["timeout"] = int(os.environ["AGENT_COMMAND_TIMEOUT_SEC"])

        stop = threading.Event()
        model = get_model(config=_model_config(config.get("model") or {}, self.model_name))
        env = HarborEnvironment(environment, asyncio.get_running_loop(), stop, **env_config)
        agent = HarborSyncedAgent(
            model,
            env,
            harbor_context=context,
            stop=stop,
            output_path=self.logs_dir / "mini-swe-agent.trajectory.json",
            **agent_config,
        )
        try:
            result = await asyncio.to_thread(agent.run, instruction)
        except asyncio.CancelledError:
            stop.set()  # the worker thread exits at its next model call or command
            raise
        except Exception as e:
            if _is_config_error(e):
                raise
            # If run() raises, Harbor skips the verifier; return so the container's state still gets graded.
            self.logger.warning("mini-swe-agent crashed (%s: %s); returning so the task is still graded", type(e).__name__, e)
            return
        self.logger.info("mini-swe-agent finished: %s", result.get("exit_status"))
