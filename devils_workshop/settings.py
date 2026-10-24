"""App-wide settings (outside any story) and the API key (PLAN.md §3).

Settings live in settings.yaml in the app's folder:
    Windows:  %APPDATA%\\DevilsWorkshop\\settings.yaml
    others:   ~/.config/devils-workshop/settings.yaml
(DW_SETTINGS_DIR overrides the folder; the tests use it.)

The API key is never stored in settings.yaml. It comes from the
OPENROUTER_API_KEY environment variable if set, otherwise from the system's
credential store (Windows Credential Manager) via the keyring library, or, on a
server without one, from keys.json in the same folder (see "API keys" below).
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from devils_workshop.project.files import read_text, write_text

KEYRING_SERVICE = "DevilsWorkshop"
KEYRING_USER = "openrouter"
ENV_KEY = "OPENROUTER_API_KEY"
VENICE_KEYRING_USER = "venice"
VENICE_ENV_KEY = "VENICE_API_KEY"
SERVICE_KEYRING_USER = "service:{slug}"  # an added service's key (Settings → Other services)
EXPLABS_ENV_KEY = "EXPLABS_API_KEY"  # Experiential Labs' key; stored like an added service's, as "service:explabs"
EXPLABS_SLUG = "explabs"

SETTINGS_VERSION = 3  # 2: scenes are written one after another unless you choose otherwise
                      # 3: the writer and the planner each have their own models, providers and options
DEFAULT_PRIMARY = "z-ai/glm-5.3"
DEFAULT_FALLBACK = "moonshotai/kimi-k3"


PROVIDER_MODES = ("auto", "price", "throughput", "latency", "mine")
WRITER, PLANNER = "writer", "planner"  # the two jobs a model is chosen for, each with its own main and fallback
ROLES = (WRITER, PLANNER)


@dataclass
class ProviderChoice:
    """Which OpenRouter providers serve one model (Settings → Models → Providers…). Providers are named by
    their OpenRouter slug, e.g. "deepinfra/fp4"."""
    mode: str = "auto"                 # auto (OpenRouter chooses) | price | throughput | latency | mine
    prefer: list[str] = field(default_factory=list)   # mine: try these first, in this order
    only: list[str] = field(default_factory=list)     # mine: never anything else
    never: list[str] = field(default_factory=list)    # any mode: never these
    allow_fallbacks: bool = True       # mine: others may be used if your choices fail


@dataclass
class RoleProviders:
    """A model's providers, chosen separately for the writer and the planner."""
    writer: ProviderChoice = field(default_factory=ProviderChoice)
    planner: ProviderChoice = field(default_factory=ProviderChoice)

    def of(self, role: str) -> ProviderChoice:
        return self.planner if role == PLANNER else self.writer

    def set(self, role: str, choice: ProviderChoice) -> None:
        setattr(self, PLANNER if role == PLANNER else WRITER, choice)


@dataclass
class ModelSettings:
    """What is kept for one model: its providers. How it writes (temperature...) is the job's (JobOptions)."""
    providers: RoleProviders = field(default_factory=RoleProviders)


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass
class JobOptions:
    """How the writer's or the planner's models are asked to write (Settings → Writer / Planner models →
    Options). Whatever a model doesn't support is left out of its requests."""
    temperature: float | None = None     # None: the model's own default
    top_p: float | None = None
    repetition: float | None = None      # frequency_penalty: discourages repeated words and phrases
    effort: str | None = None            # thinking effort (one of EFFORTS); None: the model's own default
    max_output_tokens: int = 16_000      # the longest reply, thinking included; only what is used is paid for
    size_limit_tokens: int = 60_000      # warn when one request is bigger than this
    params: dict = field(default_factory=dict)  # any other parameter, sent as it is (set in settings.yaml)

    @property
    def reasoning(self) -> dict | None:
        return {"effort": self.effort} if self.effort else None

    def request_params(self) -> dict:
        sampling = {k: v for k, v in (("temperature", self.temperature), ("top_p", self.top_p),
                                      ("frequency_penalty", self.repetition)) if v is not None}
        return {**self.params, **sampling}


# The recommended options, for the mid-size open models the app is mostly used
# with. The writer writes prose: 0.8 keeps sentences fresh without the drift and looseness these models show
# when warmer, and a little thinking holds the scene together (more flattens the voice and costs more; a model
# that can only switch thinking on or off keeps it on). Thinking can run to 15,000 tokens for one scene, so the
# longest reply is 32,000: one that hits the limit comes back empty and is sent again, and only what is used is
# paid for.
# No repetition penalty: it grows with every repeat, so over a long reply (thinking included) it pushes a model
# off its commonest words until the text falls apart (seen with a GLM-based model on Venice).
# The planner designs: 0.5 keeps its plans, briefs and reply formats reliable (these models break formats more
# often when warm), and medium thinking lets it check continuity and structure first; its requests carry the
# whole plan, so they are allowed to be bigger. Thinking is sent only to models that take it (see generate.py).
RECOMMENDED = {
    WRITER: JobOptions(temperature=0.8, top_p=0.95, effort="low", max_output_tokens=32_000,
                       size_limit_tokens=60_000),
    PLANNER: JobOptions(temperature=0.5, top_p=0.95, effort="medium", max_output_tokens=64_000,
                        size_limit_tokens=120_000),
}


def recommended(role: str) -> JobOptions:
    from copy import deepcopy
    return deepcopy(RECOMMENDED[PLANNER if role == PLANNER else WRITER])


@dataclass
class ProviderPrefs:
    allow: list[str] = field(default_factory=list)   # empty = any provider
    block: list[str] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    data_collection: str = "allow"                   # "allow" | "deny"
    require_parameters: bool = False


@dataclass
class RetrySettings:
    attempts_per_model: int = 3
    base_delay_s: float = 2.0
    max_delay_s: float = 60.0


MAX_AT_ONCE = 20  # requests at the same time (Speed); a provider that is full answers 429, and those retry

DEFAULT_PHONE_PORT = 8730


@dataclass
class PhoneSettings:
    enabled: bool = False      # also listen on the local network (Settings, "Phone and other devices")
    port: int = DEFAULT_PHONE_PORT
    password_hash: str = ""    # never the password itself (devils_workshop/phone.py)


LOCK_IDLE_CHOICES = (0, 1, 5, 15, 30, 60)  # minutes without use before the lock closes; 0: only when asked


@dataclass
class LockSettings:
    """The passcode lock on this PC's screens (Settings, "Passcode lock"; devils_workshop/ui/lock.py)."""
    code_hash: str = ""        # never the code itself; empty: no lock
    idle_minutes: int = 5      # one of LOCK_IDLE_CHOICES


@dataclass
class ServiceSettings:
    """An added OpenAI-compatible service (Settings → Other services). Its key, if any, is kept in the
    credential store, never here."""
    name: str                  # as the author calls it, e.g. "Featherless"
    slug: str                  # its part of the model names: "svc-<slug>:<model>"
    base_url: str              # e.g. https://api.featherless.ai/v1 or http://localhost:1234/v1


@dataclass
class AppSettings:
    primary: str = DEFAULT_PRIMARY               # the writer's main model
    fallback: str | None = DEFAULT_FALLBACK      # the writer's fallback
    planner_primary: str = DEFAULT_PRIMARY       # the planner's main model
    planner_fallback: str | None = DEFAULT_FALLBACK
    writer_options: JobOptions = field(default_factory=lambda: recommended(WRITER))
    planner_options: JobOptions = field(default_factory=lambda: recommended(PLANNER))
    models: dict[str, ModelSettings] = field(default_factory=lambda: {
        DEFAULT_PRIMARY: ModelSettings(), DEFAULT_FALLBACK: ModelSettings()})
    providers: ProviderPrefs = field(default_factory=ProviderPrefs)
    retries: RetrySettings = field(default_factory=RetrySettings)
    refusal_patterns_file: str = "default"
    recent_projects: list[str] = field(default_factory=list)
    appearance: str = "light"  # light | dark | system (match Windows)
    at_once: int = 3             # auto-write: requests sent at the same time
    scenes_together: bool = False  # auto-write: a chapter's scenes at the same time (faster, less continuous)
    settings_version: int = SETTINGS_VERSION
    phone: PhoneSettings = field(default_factory=PhoneSettings)
    venice_system_prompt: bool = False  # let Venice add its own system prompt to every request
    services: list[ServiceSettings] = field(default_factory=list)  # other OpenAI-compatible services
    tray: bool = False         # keep running hidden, with an icon in the notification area (devils_workshop/tray.py)
    lock: LockSettings = field(default_factory=LockSettings)

    def model(self, model_id: str) -> ModelSettings:
        return self.models.get(model_id) or ModelSettings()

    def models_of(self, role: str) -> tuple[str, str | None]:
        """(main, fallback) for the writer or the planner."""
        if role == PLANNER:
            return self.planner_primary, self.planner_fallback
        return self.primary, self.fallback

    def options_of(self, role: str) -> JobOptions:
        """How the writer's or the planner's models are asked to write."""
        return self.planner_options if role == PLANNER else self.writer_options


def settings_dir() -> Path:
    if os.environ.get("DW_SETTINGS_DIR"):
        return Path(os.environ["DW_SETTINGS_DIR"])
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "DevilsWorkshop"
    return Path.home() / ".config" / "devils-workshop"


def settings_path() -> Path:
    return settings_dir() / "settings.yaml"


class SettingsError(Exception):
    """settings.yaml can't be read."""


def load_settings(path: Path | None = None) -> AppSettings:
    """Read settings.yaml, creating it with defaults the first time."""
    path = path or settings_path()
    if not path.is_file():
        settings = AppSettings()
        save_settings(settings, path)
        return settings
    try:
        data = yaml.safe_load(read_text(path)[0]) or {}
    except yaml.YAMLError as exc:
        raise SettingsError(f"{path} isn't valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} must contain 'key: value' settings")
    return _from_dict(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or settings_path()
    data = asdict(settings)
    header = ("# Devil's Workshop settings. Edit with care, or use the Settings screen.\n"
              "# Your API key is NOT stored here.\n")
    write_text(path, header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _from_dict(data: dict) -> AppSettings:
    settings = AppSettings(models={})
    settings.primary = str(data.get("primary") or DEFAULT_PRIMARY)
    fallback = data.get("fallback", DEFAULT_FALLBACK)
    settings.fallback = str(fallback) if fallback else None
    if "planner_primary" in data:
        settings.planner_primary = str(data.get("planner_primary") or settings.primary)
        planner_fallback = data.get("planner_fallback")
        settings.planner_fallback = str(planner_fallback) if planner_fallback else None
    else:  # from before v0.17: the planner used the writer's models
        settings.planner_primary, settings.planner_fallback = settings.primary, settings.fallback
    for model_id, raw in (data.get("models") or {}).items():
        raw = raw or {}
        choice = raw.get("providers") or {}
        if any(role in choice for role in ROLES):
            providers = RoleProviders(writer=_provider_choice(choice.get(WRITER)),
                                      planner=_provider_choice(choice.get(PLANNER)))
        else:  # from before v0.17: one choice for the model, now the writer's and the planner's alike
            providers = RoleProviders(writer=_provider_choice(choice), planner=_provider_choice(choice))
        settings.models[str(model_id)] = ModelSettings(providers=providers)  # older per-model options: unused
    settings.writer_options = _job_options(data.get("writer_options"), WRITER)
    settings.planner_options = _job_options(data.get("planner_options"), PLANNER)
    providers = data.get("providers") or {}
    settings.providers = ProviderPrefs(
        allow=list(providers.get("allow") or []),
        block=list(providers.get("block") or []),
        order=list(providers.get("order") or []),
        data_collection=str(providers.get("data_collection", "allow")),
        require_parameters=bool(providers.get("require_parameters", False)),
    )
    retries = data.get("retries") or {}
    settings.retries = RetrySettings(
        attempts_per_model=int(retries.get("attempts_per_model", 3)),
        base_delay_s=float(retries.get("base_delay_s", 2.0)),
        max_delay_s=float(retries.get("max_delay_s", 60.0)),
    )
    settings.refusal_patterns_file = str(data.get("refusal_patterns_file", "default"))
    settings.recent_projects = [str(p) for p in data.get("recent_projects") or []]
    appearance = str(data.get("appearance", "light"))
    settings.appearance = appearance if appearance in ("light", "dark", "system") else "light"
    try:
        settings.at_once = min(max(int(data.get("at_once", 3)), 1), MAX_AT_ONCE)
    except (TypeError, ValueError):
        settings.at_once = 3
    settings.scenes_together = bool(data.get("scenes_together", False))
    if int(data.get("settings_version") or 1) < 2:
        settings.scenes_together = False  # v0.13: scenes one after another by default, so each follows the last
    settings.venice_system_prompt = bool(data.get("venice_system_prompt", False))
    for raw in data.get("services") or []:
        if not isinstance(raw, dict):
            continue
        slug, url = str(raw.get("slug") or "").strip(), str(raw.get("base_url") or "").strip()
        if slug and url and slug != EXPLABS_SLUG and all(s.slug != slug for s in settings.services):
            settings.services.append(ServiceSettings(name=str(raw.get("name") or slug), slug=slug, base_url=url))
    settings.tray = bool(data.get("tray", False))
    lock = data.get("lock") if isinstance(data.get("lock"), dict) else {}
    try:
        idle = int(lock.get("idle_minutes", 5))
    except (TypeError, ValueError):
        idle = 5
    settings.lock = LockSettings(code_hash=str(lock.get("code_hash") or ""),
                                 idle_minutes=idle if idle in LOCK_IDLE_CHOICES else 5)
    phone = data.get("phone") or {}
    try:
        port = int(phone.get("port", DEFAULT_PHONE_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PHONE_PORT
    settings.phone = PhoneSettings(
        enabled=bool(phone.get("enabled", False)),
        port=port if 1024 <= port <= 65535 else DEFAULT_PHONE_PORT,
        password_hash=str(phone.get("password_hash") or ""),
    )
    return settings


def _job_options(raw, role: str) -> JobOptions:
    """A job's options from the file; anything missing or unreadable is the recommended value."""
    options = recommended(role)
    if not isinstance(raw, dict):
        return options

    def number(key: str, low: float, high: float):
        if key not in raw:
            return getattr(options, key)
        try:
            return None if raw[key] is None else min(max(float(raw[key]), low), high)
        except (TypeError, ValueError):
            return getattr(options, key)

    def whole(key: str, low: int) -> int:
        try:
            return max(int(raw.get(key, getattr(options, key))), low)
        except (TypeError, ValueError):
            return getattr(options, key)

    effort = raw.get("effort", options.effort)
    return JobOptions(temperature=number("temperature", 0, 2), top_p=number("top_p", 0, 1),
                      repetition=number("repetition", -2, 2),
                      effort=str(effort) if effort in EFFORTS else None if effort is None else options.effort,
                      max_output_tokens=whole("max_output_tokens", 256),
                      size_limit_tokens=whole("size_limit_tokens", 1000),
                      params=dict(raw.get("params") or {}) if isinstance(raw.get("params"), dict) else {})


def _provider_choice(raw) -> ProviderChoice:
    raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get("mode", "auto"))
    return ProviderChoice(
        mode=mode if mode in PROVIDER_MODES else "auto",
        prefer=[str(p) for p in raw.get("prefer") or []],
        only=[str(p) for p in raw.get("only") or []],
        never=[str(p) for p in raw.get("never") or []],
        allow_fallbacks=bool(raw.get("allow_fallbacks", True)),
    )


# --- API keys ----------------------------------------------------------------------
# Kept in the system's credential store (Windows Credential Manager). A server without a desktop (a Linux
# VPS, see docs/cloud-setup.md) has none, so there they go in keys.json in the app's folder, which only you
# can read (as an environment file or ~/.ssh would be).

KEYS_FILE = "keys.json"


def _credential_store():
    """The system's credential store, or None where there is none."""
    try:
        import keyring
        from keyring.backends import fail
        store = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - no keyring at all
        return None
    if isinstance(store, fail.Keyring) or (type(store).__name__ == "ChainerBackend"
                                           and not getattr(store, "backends", None)):
        return None
    return store


def key_place() -> str:
    """Where a saved key is kept, in words for the Settings screen."""
    if _credential_store() is None:
        return "a file in the app's folder that only you can read"
    return "Windows Credential Manager" if sys.platform == "win32" else "the system's keyring"


def _keys_path() -> Path:
    return settings_dir() / KEYS_FILE


def _read_keys() -> dict:
    try:
        import json
        data = json.loads(_keys_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_keys(keys: dict) -> None:
    import json
    path = _keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # only you can read it
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(keys, out)
    os.chmod(path, 0o600)


def _get_key(user: str) -> str | None:
    store = _credential_store()
    try:
        stored = store.get_password(KEYRING_SERVICE, user) if store else _read_keys().get(user)
    except Exception:  # noqa: BLE001 - a locked or broken store: no key
        return None
    return str(stored).strip() if stored else None


def _set_key(user: str, value: str) -> None:
    store = _credential_store()
    if store is not None:
        store.set_password(KEYRING_SERVICE, user, value.strip())
        return
    _write_keys({**_read_keys(), user: value.strip()})


def _delete_key(user: str) -> None:
    store = _credential_store()
    if store is None:
        keys = _read_keys()
        if keys.pop(user, None) is not None:
            _write_keys(keys)
        return
    from keyring.errors import PasswordDeleteError
    try:
        store.delete_password(KEYRING_SERVICE, user)
    except PasswordDeleteError:
        pass  # nothing stored


def get_api_key() -> str | None:
    """The environment variable wins; otherwise the credential store. Never logged."""
    return os.environ.get(ENV_KEY, "").strip() or _get_key(KEYRING_USER)


def set_api_key(value: str) -> None:
    _set_key(KEYRING_USER, value)


def delete_api_key() -> None:
    _delete_key(KEYRING_USER)


def get_venice_key() -> str | None:
    """Venice AI's key: the VENICE_API_KEY environment variable, else the credential store. Never logged."""
    return os.environ.get(VENICE_ENV_KEY, "").strip() or _get_key(VENICE_KEYRING_USER)


def set_venice_key(value: str) -> None:
    _set_key(VENICE_KEYRING_USER, value)


def delete_venice_key() -> None:
    _delete_key(VENICE_KEYRING_USER)


def venice_key_source() -> str:
    if os.environ.get(VENICE_ENV_KEY, "").strip():
        return "environment"
    return "stored" if get_venice_key() else "none"


def get_service_key(slug: str) -> str | None:
    """An added service's key from the credential store (None for a service that needs none). Never logged."""
    return _get_key(SERVICE_KEYRING_USER.format(slug=slug))


def set_service_key(slug: str, value: str) -> None:
    _set_key(SERVICE_KEYRING_USER.format(slug=slug), value)


def delete_service_key(slug: str) -> None:
    _delete_key(SERVICE_KEYRING_USER.format(slug=slug))


def get_explabs_key() -> str | None:
    """Experiential Labs' key: the EXPLABS_API_KEY environment variable, else the credential store. Never logged."""
    return os.environ.get(EXPLABS_ENV_KEY, "").strip() or get_service_key(EXPLABS_SLUG)


def explabs_key_source() -> str:
    if os.environ.get(EXPLABS_ENV_KEY, "").strip():
        return "environment"
    return "stored" if get_service_key(EXPLABS_SLUG) else "none"


def api_key_source() -> str:
    """'environment', 'stored' or 'none' (for the Settings screen; never the key itself)."""
    if os.environ.get(ENV_KEY, "").strip():
        return "environment"
    return "stored" if get_api_key() else "none"
