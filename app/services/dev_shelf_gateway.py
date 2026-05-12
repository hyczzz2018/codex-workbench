from __future__ import annotations


from app.services.dev_shelf_base import *
class DevShelfGatewayMixin:

    def start_gateway(
        self,
        run_id: str,
        payload: DevShelfGatewayStartRequest,
    ) -> DevShelfGatewayControlResponse:
        run_dir = self._run_dir(run_id)
        active = self._active_gateway_launch(run_id)
        if active is not None:
            raise DevShelfGatewayConflict(f"Gateway is already running for run: {run_id}")

        self._ensure_gateway_runnable(run_dir)
        command = self._gateway_start_command(run_id, payload)
        launch_id = f"launch-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        started_at = self._utc_now()
        log_dir = run_dir / "artifacts" / "pi-agent-gateway-launches"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{launch_id}.log"
        process = self._spawn_gateway_process(command, log_path)
        launch = DevShelfGatewayLaunch(
            run_id=run_id,
            process=process,
            launch_id=launch_id,
            started_at=started_at,
            log_path=log_path,
            command=command,
        )
        self._gateway_launches[run_id] = launch

        if process.poll() is not None:
            self._gateway_launches.pop(run_id, None)
            detail = self._read_log_tail(log_path) or f"Gateway exited immediately with code {process.returncode}"
            raise DevShelfToolError(detail)

        return self._gateway_control_response(launch, status="started", message="Gateway 已启动。")


    def abort_gateway(
        self,
        run_id: str,
        payload: DevShelfGatewayAbortRequest | None = None,
    ) -> DevShelfGatewayControlResponse:
        self._run_dir(run_id)
        requested_session_id = payload.gateway_session_id if payload else None
        if requested_session_id and not SESSION_ID_RE.fullmatch(requested_session_id):
            raise DevShelfRunNotFound(f"Invalid Gateway session id: {requested_session_id}")

        active = self._active_gateway_launch(run_id)
        if active is None:
            return DevShelfGatewayControlResponse(
                run_id=run_id,
                status="not_running",
                gateway_session_id=requested_session_id,
                message="当前 Workbench 进程没有记录到正在运行的 Gateway。",
            )

        if requested_session_id and active.gateway_session_id and requested_session_id != active.gateway_session_id:
            raise DevShelfGatewayConflict(f"Gateway session is not running: {requested_session_id}")

        self._mark_latest_gateway_abort_requested(run_id, requested_session_id)
        returncode = self._stop_gateway_process(active.process)
        self._gateway_launches.pop(run_id, None)
        status = "aborted" if returncode is not None else "abort_requested"
        return self._gateway_control_response(
            active,
            status=status,
            returncode=returncode,
            message="Gateway 中止请求已发送。",
        )


    def _ensure_gateway_runnable(self, run_dir: Path) -> None:
        latest_packet = self._latest_packet(run_dir)
        content = latest_packet.content if latest_packet else None
        if not isinstance(content, dict):
            raise DevShelfGatewayConflict("当前 run 没有可执行的 execution packet。")
        if content.get("decision_type") != "run_manifest":
            raise DevShelfGatewayConflict("当前下一步不是生成任务，请先继续流程或处理待确认事项。")

        outputs = content.get("pending_outputs")
        if not isinstance(outputs, list) or not outputs:
            outputs = content.get("outputs_to_produce")
        if not any(isinstance(item, dict) for item in (outputs or [])):
            raise DevShelfGatewayConflict("当前 packet 没有待生成产物，不应启动 pi-agent。")

        latest_status = self._latest_gateway_session_status(run_dir)
        if (
            latest_status is not None
            and latest_status.status == "completed"
            and latest_status.packet_path
            and latest_packet.path
            and self._same_path(latest_status.packet_path, latest_packet.path)
        ):
            completed_execution = self._is_execution_gateway_result(latest_status, latest_packet)
            completed_with_outputs = self._gateway_status_has_produced_outputs(latest_status)
            if completed_execution or completed_with_outputs:
                raise DevShelfGatewayConflict(
                    "当前 execution packet 已完成执行，请等待流程生成下一份 packet 后再启动。"
                )

        workspace = content.get("workspace") if isinstance(content.get("workspace"), dict) else {}
        runtime = (
            content.get("agent_runtime_contract")
            if isinstance(content.get("agent_runtime_contract"), dict)
            else {}
        )
        cwd = workspace.get("project_path") or runtime.get("cwd")
        if not cwd:
            raise DevShelfGatewayConflict(
                "当前 run 没有项目路径，不能启动 pi-agent。请填写项目路径后重新创建 run。"
            )


    def _gateway_status_has_produced_outputs(self, status: DevShelfGatewaySessionStatus) -> bool:
        summary = status.artifact_result_summary
        if not isinstance(summary, dict):
            return True
        produced_count = summary.get("produced_count")
        if isinstance(produced_count, int):
            return produced_count > 0
        return True


    def _active_gateway_launch(self, run_id: str) -> DevShelfGatewayLaunch | None:
        launch = self._gateway_launches.get(run_id)
        if launch is None:
            return None
        if launch.process.poll() is None:
            return launch
        self._gateway_launches.pop(run_id, None)
        return None


    def _gateway_start_command(self, run_id: str, payload: DevShelfGatewayStartRequest) -> list[str]:
        script_path = self.tools_root / "scripts" / "dev_shelf_gateway.py"
        if not script_path.is_file():
            raise DevShelfToolError(f"dev-shelf gateway script not found: {script_path}")

        account = self._normalized_gateway_account(payload.account)
        provider = self._normalized_gateway_value(
            payload.provider,
            PI_PROVIDER_RE,
            field_name="provider",
            default="openai-codex",
        )
        model = self._normalized_gateway_value(
            payload.model,
            PI_MODEL_RE,
            field_name="model",
            default="gpt-5.4",
        )
        thinking = self._normalized_gateway_value(
            payload.thinking,
            PI_MODEL_RE,
            field_name="thinking",
            default=None,
        )

        command = [
            sys.executable,
            str(script_path),
            "--run-id",
            run_id,
            "--dev-shelf-root",
            str(self.root),
            "--provider",
            provider,
            "--model",
            model,
            "--request-timeout-seconds",
            str(self._normalized_gateway_seconds(payload.request_timeout_seconds, "request_timeout_seconds")),
            "--poll-interval-seconds",
            str(self._normalized_gateway_seconds(payload.poll_interval_seconds, "poll_interval_seconds")),
            "--post-prompt-grace-seconds",
            str(self._normalized_gateway_seconds(payload.post_prompt_grace_seconds, "post_prompt_grace_seconds")),
            "--pretty",
        ]
        if account:
            command.extend(["--account", account])
        if thinking:
            command.extend(["--thinking", thinking])
        if payload.no_session:
            command.append("--no-session")
        if payload.light_mode:
            command.extend(["--pi-arg=--no-tools", "--pi-arg=--no-context-files"])
        return command


    def _normalized_gateway_account(self, account: str | None) -> str | None:
        if account is None:
            return None
        value = account.strip().lower()
        if not value:
            return None
        if not PI_ACCOUNT_RE.fullmatch(value):
            raise DevShelfGatewayConflict(f"Invalid Gateway account: {account!r}")
        return value


    def _normalized_gateway_value(
        self,
        value: str | None,
        pattern: re.Pattern[str],
        *,
        field_name: str,
        default: str | None,
    ) -> str | None:
        raw = value if value is not None else default
        if raw is None:
            return None
        normalized = raw.strip()
        if not normalized:
            if default is None:
                return None
            normalized = default
        if not pattern.fullmatch(normalized):
            raise DevShelfGatewayConflict(f"Invalid Gateway {field_name}: {raw!r}")
        return normalized


    def _normalized_gateway_seconds(self, value: float, field_name: str) -> float:
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise DevShelfGatewayConflict(f"Invalid Gateway {field_name}: {value!r}") from exc
        if seconds <= 0 or seconds > GATEWAY_START_MAX_TIMEOUT_SECONDS:
            raise DevShelfGatewayConflict(
                f"Gateway {field_name} must be between 0 and {int(GATEWAY_START_MAX_TIMEOUT_SECONDS)} seconds"
            )
        return seconds


    def _spawn_gateway_process(
        self,
        command: list[str],
        log_path: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        process_env = None
        if env:
            process_env = os.environ.copy()
            process_env.update(env)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[codex-workbench] start {' '.join(self._redacted_command(command))}\n")
            log_file.flush()
            return subprocess.Popen(
                command,
                cwd=self.tools_root,
                env=process_env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

    @staticmethod


    def _redacted_command(command: list[str]) -> list[str]:
        redacted = list(command)
        for flag in ("--api-key",):
            if flag in redacted:
                index = redacted.index(flag)
                if index + 1 < len(redacted):
                    redacted[index + 1] = "<redacted>"
        return redacted


    def _stop_gateway_process(self, process: subprocess.Popen[str]) -> int | None:
        if process.poll() is not None:
            return process.returncode
        try:
            process.send_signal(signal.SIGINT)
        except OSError:
            return process.poll()

        try:
            return process.wait(timeout=GATEWAY_PROCESS_ABORT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

        try:
            process.terminate()
            return process.wait(timeout=GATEWAY_PROCESS_ABORT_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                return process.wait(timeout=GATEWAY_PROCESS_ABORT_GRACE_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                return process.poll()


    def _mark_latest_gateway_abort_requested(self, run_id: str, session_id: str | None) -> None:
        if not session_id:
            return
        try:
            run_dir = self._run_dir(run_id)
            session_dir = self._gateway_session_dir(run_dir, session_id)
            metadata_path = session_dir / "session-metadata.json"
            metadata = self._load_json(metadata_path)
            if not isinstance(metadata, dict):
                return
            metadata.update(
                {
                    "abort_requested": True,
                    "abort_requested_at": self._utc_now(),
                    "abort_requested_by": "codex-workbench",
                }
            )
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (DevShelfRunNotFound, OSError):
            return


    def _gateway_control_response(
        self,
        launch: DevShelfGatewayLaunch,
        *,
        status: str,
        returncode: int | None = None,
        message: str | None = None,
    ) -> DevShelfGatewayControlResponse:
        return DevShelfGatewayControlResponse(
            run_id=launch.run_id,
            status=status,
            pid=launch.process.pid,
            returncode=returncode if returncode is not None else launch.process.poll(),
            launch_id=launch.launch_id,
            started_at=launch.started_at,
            finished_at=self._utc_now() if launch.process.poll() is not None else None,
            log_path=str(launch.log_path),
            command=launch.command,
            gateway_session_id=launch.gateway_session_id,
            message=message,
        )


    def _read_log_tail(self, log_path: Path, limit: int = 4000) -> str | None:
        try:
            content = log_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return content[-limit:].strip() if content else None
