from __future__ import annotations


from app.services.dev_shelf_base import *
class DevShelfModelsMixin:

    def get_model_config(self) -> DevShelfModelConfig:
        return self._model_config_response(self._load_model_config())


    def update_model_config(self, payload: DevShelfModelConfigUpdateRequest) -> DevShelfModelConfig:
        pi_settings = self._load_pi_settings()
        provider = self._normalized_gateway_value(
            payload.provider,
            PI_PROVIDER_RE,
            field_name="provider",
            default=self._pi_default_provider(pi_settings),
        )
        model = self._normalized_gateway_value(
            payload.model,
            PI_MODEL_RE,
            field_name="model",
            default=self._provider_default_model(provider or "openai-codex", pi_settings),
        )
        account = self._normalized_gateway_account(payload.account) if provider == "openai-codex" else None
        config = self._load_model_config()
        config["provider"] = provider
        config.setdefault("models", {})[provider] = model
        if provider == "openai-codex":
            config["account"] = account or self._default_gateway_account()

        self._write_model_config(config)
        return self._model_config_response(config)


    def list_available_models(self, provider: str | None = None) -> DevShelfModelList:
        pi_bin = "pi"
        cmd = f"{pi_bin} --list-models 2>&1"
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            output = result.stdout or ""
        except (subprocess.TimeoutExpired, OSError):
            return DevShelfModelList()

        models: list[DevShelfModelItem] = []
        for line in output.strip().split("\n"):
            if not line or line.startswith("provider") or line.startswith("---"):
                continue
            parts = [p for p in self.PI_MODEL_ROW_RE.split(line) if p]
            if len(parts) < 2:
                continue
            item = DevShelfModelItem(
                provider=parts[0].strip(),
                model=parts[1].strip(),
                context_window=parts[2].strip() if len(parts) > 2 else "-",
                max_output=parts[3].strip() if len(parts) > 3 else "-",
                thinking=parts[4].strip() if len(parts) > 4 else "-",
                images=parts[5].strip() if len(parts) > 5 else "-",
            )
            if not provider or item.provider == provider:
                models.append(item)
        return DevShelfModelList(models=models)


    def _load_model_config(self) -> dict[str, Any]:
        config = self._load_json(self.model_config_path) or {}
        if not isinstance(config.get("models"), dict):
            config["models"] = {}
        if "api_keys" in config:
            config.pop("api_keys", None)
            self._write_model_config(config)
        return config


    def _write_model_config(self, config: dict[str, Any]) -> None:
        self.workbench_config_dir.mkdir(parents=True, exist_ok=True)
        self.model_config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(self.workbench_config_dir, 0o700)
            os.chmod(self.model_config_path, 0o600)
        except OSError:
            pass


    def _model_config_response(self, config: dict[str, Any]) -> DevShelfModelConfig:
        pi_settings = self._load_pi_settings()
        provider = self._model_config_provider(config)
        model = self._model_config_model(config, provider, pi_settings)
        account = config.get("account") if isinstance(config.get("account"), str) else self._default_gateway_account()
        if provider != "openai-codex":
            account = None
        return DevShelfModelConfig(
            provider=provider,
            model=model,
            account=account,
            providers=self._model_config_providers(config),
        )


    def _model_config_provider(self, config: dict[str, Any]) -> str:
        provider = config.get("provider")
        if isinstance(provider, str) and provider in DEFAULT_GATEWAY_MODELS:
            return provider
        return self._pi_default_provider(self._load_pi_settings())


    def _model_config_model(self, config: dict[str, Any], provider: str, pi_settings: dict[str, Any] | None = None) -> str:
        models = config.get("models") if isinstance(config.get("models"), dict) else {}
        model = models.get(provider) if isinstance(models.get(provider), str) else None
        return model or self._provider_default_model(provider, pi_settings)


    def _model_config_providers(self, config: dict[str, Any]) -> list[DevShelfModelProvider]:
        pi_settings = self._load_pi_settings()
        accounts = self._gateway_accounts()
        return [
            DevShelfModelProvider(
                provider="openai-codex",
                label=PROVIDER_LABELS["openai-codex"],
                requires_account=True,
                auth_configured=self._pi_auth_configured("openai-codex"),
                auth_source=str(self.pi_auth_path) if self.pi_auth_path.is_file() else None,
                default_model=self._model_config_model(config, "openai-codex", pi_settings),
                default_account=config.get("account") if isinstance(config.get("account"), str) else self._default_gateway_account(),
                accounts=accounts,
            ),
            DevShelfModelProvider(
                provider="deepseek",
                label=PROVIDER_LABELS["deepseek"],
                requires_account=False,
                auth_configured=self._pi_auth_configured("deepseek"),
                auth_source=str(self.pi_auth_path) if self.pi_auth_path.is_file() else None,
                default_model=self._model_config_model(config, "deepseek", pi_settings),
                default_account=None,
                accounts=[],
            ),
        ]


    def _load_pi_settings(self) -> dict[str, Any]:
        settings = self._load_json(self.pi_settings_path) or {}
        return settings if isinstance(settings, dict) else {}


    def _load_pi_auth(self) -> dict[str, Any]:
        auth = self._load_json(self.pi_auth_path) or {}
        return auth if isinstance(auth, dict) else {}


    def _pi_default_provider(self, settings: dict[str, Any] | None = None) -> str:
        value = (settings or self._load_pi_settings()).get("defaultProvider")
        return value if isinstance(value, str) and value in DEFAULT_GATEWAY_MODELS else "openai-codex"


    def _provider_default_model(self, provider: str, settings: dict[str, Any] | None = None) -> str:
        settings = settings or self._load_pi_settings()
        if provider == self._pi_default_provider(settings):
            value = settings.get("defaultModel")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return DEFAULT_GATEWAY_MODELS.get(provider, "gpt-5.4")


    def _pi_auth_configured(self, provider: str) -> bool:
        auth = self._load_pi_auth()
        provider_auth = auth.get(provider)
        if not isinstance(provider_auth, dict):
            return False
        if provider == "deepseek":
            return provider_auth.get("type") == "api_key" and bool(provider_auth.get("key"))
        if provider == "openai-codex":
            return provider_auth.get("type") == "oauth" and bool(provider_auth.get("access") or provider_auth.get("refresh"))
        return bool(provider_auth)


    def _gateway_accounts(self) -> list[str]:
        accounts = ["default"] if (Path.home() / ".pi" / "agent").is_dir() else []
        account_base = Path.home() / ".pi"
        if account_base.is_dir():
            for path in sorted(account_base.glob("agent-codex-*")):
                if path.is_dir():
                    name = path.name.removeprefix("agent-codex-")
                    if PI_ACCOUNT_RE.fullmatch(name):
                        accounts.append(name)
        return sorted(dict.fromkeys(accounts))


    def _default_gateway_account(self) -> str | None:
        accounts = self._gateway_accounts()
        if "a" in accounts:
            return "a"
        return accounts[0] if accounts else None
