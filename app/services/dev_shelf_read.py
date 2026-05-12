from __future__ import annotations


from app.services.dev_shelf_base import *
from app.services.dev_shelf_base import _GatewayStreamSession, _GatewayStreamSubscription, _STREAM_CLOSED
class DevShelfReadMixin:

    def list_runs(self) -> list[DevShelfRunSummary]:
        if not self.runs_dir.is_dir():
            return []

        runs: list[DevShelfRunSummary] = []
        for run_state_path in sorted(self.runs_dir.glob("*/run-state.json")):
            state = self._load_json(run_state_path)
            if not isinstance(state, dict):
                continue
            runs.append(self._summary_from_state(state))

        return sorted(
            runs,
            key=lambda item: item.updated_at or "",
            reverse=True,
        )


    def get_run(self, run_id: str) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)

        artifacts = [
            self._artifact_from_state_item(run_dir, item)
            for item in state.get("artifacts", [])
            if isinstance(item, dict) and item.get("artifact_id")
        ]
        summary = self._summary_from_state(state)
        latest_packet = self._latest_packet(run_dir)
        pending_gates = self._pending_human_gates(state)
        return DevShelfRunDetail(
            **summary.model_dump(),
            task_type_status=state.get("task_type_status"),
            artifacts=artifacts,
            pending_human_gates=pending_gates,
            router=self._router_result(state, latest_packet, pending_gates),
            latest_packet=latest_packet,
            action_policy=self._action_policy(state, latest_packet, run_id),
        )


    def list_project_directories(self, path: str | None = None) -> DevShelfDirectoryList:
        current_path = self._resolve_project_directory(path, must_exist=True)
        if not current_path.is_dir():
            raise DevShelfProjectConflict(f"项目路径不是目录：{current_path}")

        items: list[DevShelfDirectoryEntry] = []
        try:
            children = list(current_path.iterdir())
        except OSError as exc:
            raise DevShelfProjectConflict(f"目录不可读取：{current_path}") from exc

        for child in sorted(children, key=lambda item: item.name.lower()):
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if not child.is_dir() or not self._is_under_projects_root(resolved):
                continue
            items.append(DevShelfDirectoryEntry(name=child.name, path=str(resolved)))

        parent_path: str | None = None
        if current_path != self.projects_root:
            parent = current_path.parent.resolve()
            if self._is_under_projects_root(parent):
                parent_path = str(parent)

        return DevShelfDirectoryList(
            root_path=str(self.projects_root),
            current_path=str(current_path),
            parent_path=parent_path,
            items=items,
        )


    def get_gateway_status(self, run_id: str, session_id: str | None = None) -> DevShelfGatewaySessionStatus:
        run_dir = self._run_dir(run_id)
        if session_id is None:
            launch_status = self._gateway_launch_status(run_dir, run_id)
            if launch_status is not None:
                return launch_status
        session_dir = self._gateway_session_dir(run_dir, session_id)
        return self._gateway_status_from_session(session_dir)


    def get_gateway_events(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        cursor: int | str | None = None,
        limit: int | str | None = None,
    ) -> DevShelfGatewayRuntimeEvents:
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        status = self._gateway_status_from_session(session_dir)
        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfRunNotFound(f"Gateway runtime events not found: {run_id}")
        page = self._read_gateway_runtime_events(runtime_events_path, cursor=cursor, limit=limit)
        return DevShelfGatewayRuntimeEvents(
            run_id=status.run_id,
            gateway_session_id=status.gateway_session_id,
            session_dir=status.session_dir,
            runtime_events_path=status.runtime_events_path,
            **page,
        )


    def iter_gateway_stream_events(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        cursor: int | str | None = None,
        limit: int | str | None = None,
        last_event_id: str | None = None,
        poll_interval_seconds: float = GATEWAY_STREAM_POLL_INTERVAL_SECONDS,
    ):
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        status = self._gateway_status_from_session(session_dir)
        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfRunNotFound(f"Gateway runtime events not found: {run_id}")

        current_cursor = self._gateway_stream_start_cursor(cursor, last_event_id)
        actual_limit = self._normalize_gateway_limit(limit)

        subscriber: _GatewayStreamSubscription | None = None
        sent_event_ids: set[str] = set()
        if status.status not in STREAM_TERMINAL_STATUSES:
            subscriber = self._gateway_stream_session(
                run_id=run_id,
                session_dir=session_dir,
                status=status,
                runtime_events_path=runtime_events_path,
                poll_interval_seconds=poll_interval_seconds,
            ).subscribe()
        yield f"retry: {SSE_RETRY_MS}\n\n"

        # Backfill from durable history first. Live events that arrive during this
        # pass are already queued by the hub subscription above and are de-duped
        # by cursor/event id below.
        last_keep_alive_at = time.monotonic()
        try:
            while True:
                page = self._read_gateway_runtime_events(
                    runtime_events_path,
                    cursor=current_cursor,
                    limit=actual_limit,
                )
                normalized_page = self._normalize_gateway_stream_page(
                    {
                        "schema_version": "1.0",
                        "run_id": status.run_id,
                        "gateway_session_id": status.gateway_session_id,
                        "session_dir": status.session_dir,
                        "runtime_events_path": status.runtime_events_path,
                        **page,
                    }
                )
                for event in normalized_page.get("events", []):
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("event_id") or "")
                    if event_id:
                        sent_event_ids.add(event_id)
                    event_cursor = event.get("cursor")
                    if isinstance(event_cursor, int):
                        current_cursor = max(current_cursor, event_cursor)
                    yield self._sse_encode_event(event)
                if not page.get("has_more"):
                    break

            status = self._gateway_status_from_session(session_dir)
            if status.status in STREAM_TERMINAL_STATUSES or subscriber is None:
                return

            while True:
                try:
                    item = subscriber.events.get(timeout=GATEWAY_STREAM_KEEP_ALIVE_SECONDS)
                except queue.Empty:
                    status = self._gateway_status_from_session(session_dir)
                    if status.status in STREAM_TERMINAL_STATUSES:
                        return
                    yield ": keep-alive\n\n"
                    last_keep_alive_at = time.monotonic()
                    continue
                if item is _STREAM_CLOSED:
                    return
                if not isinstance(item, dict):
                    continue
                event_id = str(item.get("event_id") or "")
                if event_id and event_id in sent_event_ids:
                    continue
                event_cursor = item.get("cursor")
                if isinstance(event_cursor, int) and event_cursor <= current_cursor:
                    if event_id:
                        sent_event_ids.add(event_id)
                    continue
                if event_id:
                    sent_event_ids.add(event_id)
                if isinstance(event_cursor, int):
                    current_cursor = max(current_cursor, event_cursor)
                yield self._sse_encode_event(item)
                if self._is_terminal_gateway_stream_event(item):
                    return
                last_keep_alive_at = time.monotonic()
        finally:
            if subscriber is not None:
                self._gateway_stream_unsubscribe(
                    run_id=run_id,
                    session_id=status.gateway_session_id or session_dir.name,
                    subscriber=subscriber,
                )


    def _gateway_stream_session(
        self,
        *,
        run_id: str,
        session_dir: Path,
        status: DevShelfGatewaySessionStatus,
        runtime_events_path: Path,
        poll_interval_seconds: float,
    ) -> _GatewayStreamSession:
        session_id = status.gateway_session_id or session_dir.name
        key = (run_id, session_id)
        with self._gateway_stream_sessions_lock:
            session = self._gateway_stream_sessions.get(key)
            if (
                session is None
                or session.closed
                or session.runtime_events_path != runtime_events_path
            ):
                session = _GatewayStreamSession(
                    service=self,
                    run_id=run_id,
                    session_dir=session_dir,
                    session_id=session_id,
                    runtime_events_path=runtime_events_path,
                    poll_interval_seconds=poll_interval_seconds,
                )
                self._gateway_stream_sessions[key] = session
            return session


    def _gateway_stream_unsubscribe(
        self,
        *,
        run_id: str,
        session_id: str,
        subscriber: _GatewayStreamSubscription,
    ) -> None:
        key = (run_id, session_id)
        with self._gateway_stream_sessions_lock:
            session = self._gateway_stream_sessions.get(key)
        if session is not None:
            session.unsubscribe(subscriber)


    def _is_terminal_gateway_stream_event(self, event: dict[str, Any]) -> bool:
        if event.get("event_type") != "status":
            return False
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return False
        return payload.get("status") in {"completed", "aborted"}


    def get_gateway_transcript(
        self,
        run_id: str,
        session_id: str | None = None,
    ) -> DevShelfGatewayTranscript:
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        status = self._gateway_status_from_session(session_dir)
        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfRunNotFound(f"Gateway runtime events not found: {run_id}")
        messages, event_count = self._read_gateway_transcript(runtime_events_path)
        return DevShelfGatewayTranscript(
            run_id=status.run_id,
            gateway_session_id=status.gateway_session_id,
            session_dir=status.session_dir,
            runtime_events_path=status.runtime_events_path,
            message_count=len(messages),
            event_count=event_count,
            messages=messages,
        )


    def get_gateway_result(self, run_id: str, session_id: str | None = None) -> DevShelfGatewayArtifactPayload:
        return self._gateway_artifact_payload(
            run_id,
            session_id,
            metadata_field="gateway_result_json",
            default_name="gateway-result.json",
        )


    def get_gateway_candidates(self, run_id: str, session_id: str | None = None) -> DevShelfGatewayArtifactPayload:
        return self._gateway_artifact_payload(
            run_id,
            session_id,
            metadata_field="gateway_event_candidates_json",
            default_name="gateway-event-candidates.json",
            preview_candidates=True,
        )


    def _summary_from_state(self, state: dict[str, Any]) -> DevShelfRunSummary:
        artifacts = state.get("artifacts", [])
        return DevShelfRunSummary(
            run_id=str(state.get("run_id") or ""),
            project_name=state.get("project_name"),
            request_summary=state.get("request_summary"),
            current_stage=state.get("current_stage"),
            status=state.get("status"),
            task_type=state.get("task_type"),
            updated_at=self._updated_at(state),
            artifact_count=len(artifacts) if isinstance(artifacts, list) else 0,
        )


    def _artifact_from_state_item(self, run_dir: Path, item: dict[str, Any]) -> DevShelfArtifact:
        content, content_format, truncated, error = self._preview_artifact_content(
            run_dir,
            item.get("path"),
        )
        feedback_paths = self._artifact_feedback_paths(item)
        return DevShelfArtifact(
            artifact_id=str(item.get("artifact_id") or ""),
            title=str(item.get("title") or item.get("artifact_id") or ""),
            status=str(item.get("status") or "missing"),
            path=item.get("path"),
            produced_by=item.get("produced_by"),
            updated_at=item.get("updated_at"),
            content=content,
            content_format=content_format,
            content_truncated=truncated,
            content_error=error,
            previewable=bool(content),
            current_revision_id=item.get("current_revision_id"),
            revision_count=len(item.get("revisions") or []),
            feedback_count=len(feedback_paths),
            latest_feedback_path=feedback_paths[-1] if feedback_paths else None,
        )


    def _artifact_feedback_paths(self, item: dict[str, Any]) -> list[str]:
        paths: list[str] = []
        for record in item.get("feedback_records") or []:
            if not isinstance(record, dict):
                continue
            path = record.get("path")
            if isinstance(path, str) and path not in paths:
                paths.append(path)
        for path in item.get("revision_feedback_paths") or []:
            if isinstance(path, str) and path not in paths:
                paths.append(path)
        return paths


    def _preview_artifact_content(
        self,
        run_dir: Path,
        raw_path: Any,
    ) -> tuple[str | None, str | None, bool, str | None]:
        if not raw_path:
            return None, None, False, None

        path, error = self._resolve_artifact_path(run_dir, str(raw_path))
        if error or path is None:
            return None, "unsupported", False, error

        content_format = self._content_format(path)
        if not path.is_file():
            return None, content_format, False, "产物文件不存在或不是普通文件。"

        try:
            raw = path.read_bytes()
        except OSError:
            return None, content_format, False, "产物文件读取失败。"

        truncated = len(raw) > ARTIFACT_PREVIEW_LIMIT
        raw = raw[:ARTIFACT_PREVIEW_LIMIT]
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, "unsupported", False, "当前产物不是 UTF-8 文本，暂不支持预览。"

        return content, content_format, truncated, None


    def _resolve_artifact_path(self, run_dir: Path, raw_path: str) -> tuple[Path | None, str | None]:
        del run_dir
        source = Path(raw_path).expanduser()
        candidate = source if source.is_absolute() else self.root / source
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            return None, "产物路径不在 dev-shelf 根目录内，已拒绝预览。"
        return resolved, None


    def _content_format(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "markdown"
        if suffix == ".json":
            return "json"
        return "text"


    def _latest_packet(self, run_dir: Path) -> DevShelfExecutionPacket | None:
        packets_dir = run_dir / "packets"
        if not packets_dir.is_dir():
            return None

        candidates: list[tuple[int, Path]] = []
        for path in packets_dir.glob("*-execution-packet.json"):
            match = PACKET_RE.fullmatch(path.name)
            if match:
                candidates.append((int(match.group("sequence")), path))
        if not candidates:
            return None

        sequence, packet_path = sorted(candidates)[-1]
        content = self._load_json(packet_path)
        if not isinstance(content, dict):
            return None

        markdown_path = packet_path.with_suffix(".md")
        markdown = self._read_text(markdown_path) if markdown_path.exists() else None
        return DevShelfExecutionPacket(
            sequence=sequence,
            path=str(packet_path),
            markdown_path=str(markdown_path) if markdown_path.exists() else None,
            decision_type=content.get("decision_type"),
            target=content.get("target"),
            ready=content.get("ready"),
            content=content,
            markdown=markdown,
        )


    def _run_dir(self, run_id: str) -> Path:
        if not RUN_ID_RE.fullmatch(run_id):
            raise DevShelfRunNotFound(f"Invalid run id: {run_id}")

        run_dir = (self.runs_dir / run_id).resolve()
        if run_dir.parent != self.runs_dir.resolve() or not run_dir.is_dir():
            raise DevShelfRunNotFound(f"Run not found: {run_id}")
        return run_dir


    def _gateway_root(self, run_dir: Path) -> Path:
        return run_dir / "artifacts" / "pi-agent-gateway"


    def _gateway_session_dirs(self, run_dir: Path) -> list[Path]:
        gateway_root = self._gateway_root(run_dir)
        if not gateway_root.is_dir():
            return []
        return sorted(
            [
                path
                for path in gateway_root.iterdir()
                if path.is_dir() and (path / "session-metadata.json").is_file()
            ],
            key=lambda path: path.name,
        )


    def _gateway_session_sort_key(self, session_dir: Path) -> tuple[str, float, str]:
        metadata = self._load_json(session_dir / "session-metadata.json") or {}
        timestamp = str(metadata.get("finished_at") or metadata.get("started_at") or "")
        try:
            mtime = (session_dir / "session-metadata.json").stat().st_mtime
        except OSError:
            mtime = 0.0
        return timestamp, mtime, session_dir.name


    def _gateway_session_dir(self, run_dir: Path, session_id: str | None) -> Path:
        gateway_root = self._gateway_root(run_dir)
        if session_id:
            if not SESSION_ID_RE.fullmatch(session_id):
                raise DevShelfRunNotFound(f"Invalid Gateway session id: {session_id}")
            session_dir = (gateway_root / session_id).resolve()
            if session_dir.parent != gateway_root.resolve() or not (session_dir / "session-metadata.json").is_file():
                raise DevShelfRunNotFound(f"Gateway session not found: {session_id}")
            return session_dir

        sessions = self._gateway_session_dirs(run_dir)
        if not sessions:
            raise DevShelfRunNotFound(f"Gateway session not found: {run_dir.name}")
        return max(sessions, key=self._gateway_session_sort_key)


    def _gateway_path_or_none(self, path: Path) -> str | None:
        return str(path) if path.exists() else None


    def _gateway_status_from_session(self, session_dir: Path) -> DevShelfGatewaySessionStatus:
        metadata_path = session_dir / "session-metadata.json"
        metadata = self._load_json(metadata_path)
        if not isinstance(metadata, dict):
            raise DevShelfRunNotFound(f"Gateway session metadata not found: {session_dir.name}")

        runtime_events_path = session_dir / "runtime-events.jsonl"
        gateway_result_json = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_result_json"),
            default_name="gateway-result.json",
        )
        gateway_result_markdown = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_result_markdown"),
            default_name="gateway-result.md",
        )
        candidates_json = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_event_candidates_json"),
            default_name="gateway-event-candidates.json",
        )
        candidates_markdown = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_event_candidates_markdown"),
            default_name="gateway-event-candidates.md",
        )
        event_count = metadata.get("event_count")
        if not isinstance(event_count, int):
            event_count = self._count_jsonl_lines(runtime_events_path)

        return DevShelfGatewaySessionStatus(
            run_id=metadata.get("run_id"),
            gateway_session_id=metadata.get("gateway_session_id") or session_dir.name,
            status=metadata.get("status"),
            started_at=metadata.get("started_at"),
            finished_at=metadata.get("finished_at"),
            provider=metadata.get("provider"),
            model=metadata.get("model"),
            thinking=metadata.get("thinking"),
            pi_account=metadata.get("pi_account"),
            pi_account_dir=metadata.get("pi_account_dir"),
            pi_session_id=metadata.get("pi_session_id"),
            packet_path=metadata.get("packet_path"),
            packet_target=metadata.get("packet_target"),
            session_dir=str(session_dir),
            metadata_path=str(metadata_path),
            runtime_events_path=self._gateway_path_or_none(runtime_events_path),
            runtime_event_schema_version=metadata.get("runtime_event_schema_version"),
            event_count=event_count,
            gateway_result_json=self._gateway_path_or_none(gateway_result_json),
            gateway_result_markdown=self._gateway_path_or_none(gateway_result_markdown),
            gateway_event_candidates_json=self._gateway_path_or_none(candidates_json),
            gateway_event_candidates_markdown=self._gateway_path_or_none(candidates_markdown),
            artifact_result_summary=metadata.get("artifact_result_summary"),
            event_candidate_summary=metadata.get("event_candidate_summary"),
            abort_requested=bool(metadata.get("abort_requested", False)),
            error=metadata.get("error"),
        )


    def _gateway_launch_status(self, run_dir: Path, run_id: str) -> DevShelfGatewaySessionStatus | None:
        launch = self._gateway_launches.get(run_id)
        if launch is None:
            return None

        latest_status = self._latest_gateway_session_status(run_dir)
        if latest_status is not None and self._gateway_session_matches_launch(latest_status, launch):
            launch.gateway_session_id = latest_status.gateway_session_id
            if latest_status.status in {"completed", "failed"} and launch.process.poll() is not None:
                self._gateway_launches.pop(run_id, None)
            return latest_status

        returncode = launch.process.poll()
        if returncode is None:
            return DevShelfGatewaySessionStatus(
                run_id=run_id,
                status="starting",
                started_at=launch.started_at,
                log_path=str(launch.log_path),
                event_count=0,
            )

        self._gateway_launches.pop(run_id, None)
        detail = self._read_log_tail(launch.log_path) or f"Gateway exited with code {returncode}"
        return DevShelfGatewaySessionStatus(
            run_id=run_id,
            status="failed",
            started_at=launch.started_at,
            finished_at=self._utc_now(),
            log_path=str(launch.log_path),
            event_count=0,
            error=detail,
        )


    def _latest_gateway_session_status(self, run_dir: Path) -> DevShelfGatewaySessionStatus | None:
        sessions = self._gateway_session_dirs(run_dir)
        if not sessions:
            return None
        return self._gateway_status_from_session(max(sessions, key=self._gateway_session_sort_key))


    def _gateway_session_matches_launch(
        self,
        status: DevShelfGatewaySessionStatus,
        launch: DevShelfGatewayLaunch,
    ) -> bool:
        session_started_at = self._parse_utc(status.started_at)
        launch_started_at = self._parse_utc(launch.started_at)
        if session_started_at is None or launch_started_at is None:
            return False
        return session_started_at >= launch_started_at


    def _gateway_metadata_path(self, session_dir: Path, raw_path: Any, *, default_name: str) -> Path:
        candidate = Path(str(raw_path)) if raw_path else session_dir / default_name
        if not candidate.is_absolute():
            candidate = session_dir / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(session_dir.resolve())
        except (OSError, ValueError) as exc:
            raise DevShelfRunNotFound(f"Gateway artifact path is outside session dir: {candidate}") from exc
        return resolved


    def _normalize_gateway_cursor(self, cursor: int | str | None) -> int:
        if cursor is None or cursor == "":
            return 0
        try:
            value = int(cursor)
        except (TypeError, ValueError) as exc:
            raise DevShelfRunNotFound(f"Invalid runtime event cursor: {cursor}") from exc
        if value < 0:
            raise DevShelfRunNotFound("Runtime event cursor must be >= 0")
        return value


    def _normalize_gateway_limit(self, limit: int | str | None) -> int:
        if limit is None or limit == "":
            return GATEWAY_EVENT_LIMIT_DEFAULT
        try:
            value = int(limit)
        except (TypeError, ValueError) as exc:
            raise DevShelfRunNotFound(f"Invalid runtime event limit: {limit}") from exc
        if value <= 0:
            raise DevShelfRunNotFound("Runtime event limit must be > 0")
        return min(value, GATEWAY_EVENT_LIMIT_MAX)


    def _gateway_stream_start_cursor(self, cursor: int | str | None, last_event_id: str | None) -> int:
        if last_event_id:
            parsed = self._cursor_from_sse_event_id(last_event_id)
            if parsed is not None:
                return parsed
        return self._normalize_gateway_cursor(cursor)


    def _read_gateway_runtime_events(
        self,
        runtime_events_path: Path,
        *,
        cursor: int | str | None,
        limit: int | str | None,
    ) -> dict[str, Any]:
        start_after = self._normalize_gateway_cursor(cursor)
        actual_limit = self._normalize_gateway_limit(limit)
        events: list[dict[str, Any]] = []
        has_more = False
        next_cursor = start_after
        total_seen = 0

        file_offset = 0
        with runtime_events_path.open("r", encoding="utf-8") as fh:
            line_number = 0
            while True:
                raw_line = fh.readline()
                if raw_line == "":
                    break
                line_number += 1
                line = raw_line.strip()
                if not line:
                    continue
                total_seen += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DevShelfRunNotFound(f"Invalid Gateway runtime event at line {line_number}") from exc
                if not isinstance(event, dict):
                    raise DevShelfRunNotFound(f"Gateway runtime event at line {line_number} is not an object")
                sequence = event.get("sequence")
                if not isinstance(sequence, int):
                    sequence = line_number
                    event = {**event, "sequence": sequence}
                if sequence <= start_after:
                    continue
                if len(events) >= actual_limit:
                    has_more = True
                    break
                events.append(event)
                next_cursor = max(next_cursor, sequence)
            file_offset = fh.tell()

        return {
            "cursor": start_after,
            "next_cursor": next_cursor,
            "limit": actual_limit,
            "has_more": has_more,
            "event_count": len(events),
            "total_seen": total_seen,
            "file_offset": file_offset,
            "events": events,
        }


    def _read_gateway_runtime_events_from_offset(
        self,
        runtime_events_path: Path,
        *,
        cursor: int | str | None,
        limit: int | str | None,
        offset: int,
    ) -> dict[str, Any]:
        start_after = self._normalize_gateway_cursor(cursor)
        actual_limit = self._normalize_gateway_limit(limit)
        events: list[dict[str, Any]] = []
        has_more = False
        next_cursor = start_after
        file_offset = max(offset, 0)

        with runtime_events_path.open("r", encoding="utf-8") as fh:
            fh.seek(file_offset)
            while True:
                line_offset = fh.tell()
                raw_line = fh.readline()
                if raw_line == "":
                    file_offset = fh.tell()
                    break
                if not raw_line.endswith("\n"):
                    file_offset = line_offset
                    break
                line = raw_line.strip()
                if not line:
                    file_offset = fh.tell()
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    file_offset = line_offset
                    break
                if not isinstance(event, dict):
                    raise DevShelfRunNotFound("Gateway runtime event must be an object")
                sequence = event.get("sequence")
                if not isinstance(sequence, int):
                    sequence = next_cursor + 1
                    event = {**event, "sequence": sequence}
                if sequence <= start_after:
                    file_offset = fh.tell()
                    continue
                if len(events) >= actual_limit:
                    has_more = True
                    file_offset = line_offset
                    break
                events.append(event)
                next_cursor = max(next_cursor, sequence)
                file_offset = fh.tell()

        return {
            "cursor": start_after,
            "next_cursor": next_cursor,
            "limit": actual_limit,
            "has_more": has_more,
            "event_count": len(events),
            "total_seen": None,
            "file_offset": file_offset,
            "events": events,
        }


    def _normalize_gateway_stream_page(self, page: dict[str, Any]) -> dict[str, Any]:
        normalizer = self._load_dev_shelf_stream_normalizer()
        if normalizer is not None:
            return normalizer(page)
        return self._fallback_normalize_gateway_stream_page(page)


    def _load_dev_shelf_stream_normalizer(self):
        inserted = False
        tools_root = str(self.tools_root)
        if tools_root not in sys.path:
            sys.path.insert(0, tools_root)
            inserted = True
        try:
            from dev_shelf_gateway.workbench_stream_event import normalize_runtime_events_page

            return normalize_runtime_events_page
        except (ImportError, ModuleNotFoundError):
            return None
        finally:
            if inserted:
                try:
                    sys.path.remove(tools_root)
                except ValueError:
                    pass


    def _fallback_normalize_gateway_stream_page(self, page: dict[str, Any]) -> dict[str, Any]:
        runtime_events = page.get("events")
        if not isinstance(runtime_events, list):
            runtime_events = []
        events: list[dict[str, Any]] = []
        for runtime_event in runtime_events:
            if isinstance(runtime_event, dict):
                events.extend(self._fallback_normalize_gateway_stream_event(runtime_event))
        return {
            "schema_version": "1.0",
            "workbench_stream_event_schema_version": "1.0",
            "run_id": page.get("run_id"),
            "gateway_session_id": page.get("gateway_session_id"),
            "session_dir": page.get("session_dir"),
            "runtime_events_path": page.get("runtime_events_path"),
            "cursor": page.get("cursor"),
            "next_cursor": page.get("next_cursor"),
            "limit": page.get("limit"),
            "has_more": page.get("has_more"),
            "runtime_event_count": page.get("event_count"),
            "event_count": len(events),
            "events": events,
        }


    def _fallback_normalize_gateway_stream_event(self, runtime_event: dict[str, Any]) -> list[dict[str, Any]]:
        raw = runtime_event.get("raw") if isinstance(runtime_event.get("raw"), dict) else {}
        raw_type = str(raw.get("type") or raw.get("event") or raw.get("kind") or "")
        events: list[dict[str, Any]] = []

        def add(event_type: str, payload: dict[str, Any]) -> None:
            events.append(self._gateway_stream_event(runtime_event, raw, event_type, payload, len(events)))

        if runtime_event.get("kind") == "stderr" and isinstance(raw.get("line"), str):
            add("error", {"message": raw["line"], "source": "stderr"})
            return events

        runtime_error = self._runtime_error_payload(raw)
        if runtime_error is not None:
            add("error", runtime_error)
            return events

        running_service = self._fallback_running_service_payload(raw)
        if running_service is not None:
            add("running_service", running_service)
            return events

        if raw_type == "response":
            command = raw.get("command") if isinstance(raw.get("command"), str) else None
            if raw.get("success") is False:
                add(
                    "error",
                    {
                        "message": raw.get("error") or f"Gateway command failed: {command or 'unknown'}",
                        "command": command,
                        "source": "response",
                    },
                )
            elif command == "prompt":
                add("status", {"status": "prompt_accepted", "command": command})
            return events

        if raw_type in {"agent_start", "agent_end", "turn_start", "turn_end", "lifecycle_abort"}:
            status_by_type = {
                "agent_start": "started",
                "agent_end": "completed",
                "turn_start": "turn_started",
                "turn_end": "turn_completed",
                "lifecycle_abort": "aborted",
            }
            add("status", {"status": status_by_type[raw_type], "raw_status": raw_type})
            return events

        if raw_type in {"artifact_candidate", "gateway_event_candidate"}:
            add("artifact_candidate", self._fallback_gateway_artifact_candidate_payload(raw))
            return events

        delta = self._runtime_assistant_delta(runtime_event, raw)
        if delta:
            add("assistant_delta", {"delta": delta})

        if raw_type in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
            self._fallback_normalize_gateway_tool_event(raw, add)

        return events


    def _fallback_gateway_artifact_candidate_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
        source_output = raw.get("source_output") if isinstance(raw.get("source_output"), dict) else {}
        artifact_id = (
            self._string_value(raw.get("artifact_id"))
            or self._string_value(event.get("artifact_id"))
            or self._string_value(source_output.get("artifact_id"))
        )
        path = (
            self._string_value(raw.get("path"))
            or self._string_value(event.get("path"))
            or self._first_declared_output_path(source_output)
        )
        status = (
            self._string_value(raw.get("status") or raw.get("artifact_status"))
            or self._string_value(event.get("artifact_status") or event.get("status"))
            or self._string_value(source_output.get("status_on_produce"))
        )
        title = (
            self._string_value(raw.get("title"))
            or self._string_value(event.get("title"))
            or self._string_value(source_output.get("title"))
        )
        return {
            "artifact_id": artifact_id or None,
            "title": title or None,
            "path": path or None,
            "status": status or None,
            "candidate_id": self._string_value(raw.get("candidate_id")) or None,
            "produced_by": self._string_value(raw.get("produced_by") or event.get("produced_by")) or None,
            "review_required": bool(raw.get("review_required") or source_output.get("review_required")),
        }


    def _fallback_running_service_payload(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        raw_type = str(raw.get("type") or raw.get("event") or raw.get("kind") or "")
        source = raw
        if raw_type not in {"running_service", "service_started", "service_report", "dev_server_started"}:
            for key in ("service", "running_service", "service_report"):
                nested = raw.get(key)
                if isinstance(nested, dict):
                    source = nested
                    break
            else:
                return None

        service_name = self._first_string_value(source, "service_name", "name", "label") or "运行服务"
        url = self._first_string_value(source, "url", "browser_url", "local_url", "base_url")
        port = self._port_value(source.get("port"))
        if port is None and url:
            match = re.search(r":(\d{2,5})(?:/|$)", url)
            if match:
                port = self._port_value(match.group(1))
        command = self._first_string_value(source, "command", "cmd", "start_command")
        if not any([url, port, command]):
            return None
        return {
            "service_name": service_name,
            "kind": self._first_string_value(source, "service_kind", "kind") or None,
            "url": url or None,
            "port": port,
            "command": command or None,
            "cwd": self._first_string_value(source, "cwd", "command_cwd", "working_directory") or None,
            "log_path": self._first_string_value(source, "log_path", "log") or None,
            "source": self._first_string_value(raw, "source") or raw_type or "runtime_event",
        }


    def _fallback_normalize_gateway_tool_event(self, raw: dict[str, Any], add) -> None:
        raw_type = str(raw.get("type") or "")
        tool_name = raw.get("toolName") if isinstance(raw.get("toolName"), str) else None
        tool_call_id = raw.get("toolCallId") if isinstance(raw.get("toolCallId"), str) else None
        if raw_type == "tool_execution_start":
            args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
            add(
                "tool_call",
                {
                    "phase": "started",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": self._summarize_gateway_tool_args(args),
                },
            )
            return
        result_key = "partialResult" if raw_type == "tool_execution_update" else "result"
        result = raw.get(result_key) if isinstance(raw.get(result_key), dict) else {}
        text = self._gateway_content_text(result.get("content"))
        add(
            "tool_result",
            {
                "phase": "updated" if raw_type == "tool_execution_update" else "completed",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "is_error": bool(raw.get("isError")),
                "text": text,
                "text_truncated": False,
            },
        )
        match = re.match(r"^Successfully wrote (?P<bytes>\d+) bytes to (?P<path>.+)$", text.strip())
        if raw_type == "tool_execution_end" and tool_name in {"write", "edit"} and match and not raw.get("isError"):
            add(
                "file_write",
                {
                    "path": match.group("path").strip(),
                    "bytes": int(match.group("bytes")),
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                },
            )


    def _gateway_stream_event(
        self,
        runtime_event: dict[str, Any],
        raw: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        index: int,
    ) -> dict[str, Any]:
        sequence = runtime_event.get("sequence")
        gateway_session_id = runtime_event.get("gateway_session_id")
        return {
            "schema_version": "1.0",
            "event_id": f"{gateway_session_id or 'session'}:{sequence or 0}:{event_type}:{index}",
            "event_type": event_type,
            "cursor": sequence,
            "runtime_sequence": sequence,
            "ts": runtime_event.get("ts"),
            "run_id": runtime_event.get("run_id"),
            "gateway_session_id": gateway_session_id,
            "pi_session_id": runtime_event.get("pi_session_id"),
            "payload": payload,
            "source": {
                "stream": runtime_event.get("stream"),
                "kind": runtime_event.get("kind"),
                "raw_type": raw.get("type"),
            },
        }


    def _read_gateway_transcript(self, runtime_events_path: Path) -> tuple[list[DevShelfGatewayTranscriptMessage], int]:
        messages: list[DevShelfGatewayTranscriptMessage] = []
        assistant_text = ""
        assistant_start: int | None = None
        assistant_end: int | None = None
        assistant_ts: str | None = None
        seen_tool_messages: set[str] = set()
        event_count = 0

        def flush_assistant() -> None:
            nonlocal assistant_text, assistant_start, assistant_end, assistant_ts
            text = assistant_text.strip()
            if text:
                messages.append(
                    DevShelfGatewayTranscriptMessage(
                        role="assistant",
                        kind="message",
                        text=text,
                        sequence_start=assistant_start,
                        sequence_end=assistant_end,
                        ts=assistant_ts,
                    )
                )
            assistant_text = ""
            assistant_start = None
            assistant_end = None
            assistant_ts = None

        with runtime_events_path.open("r", encoding="utf-8") as fh:
            for line_number, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                event_count += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DevShelfRunNotFound(f"Invalid Gateway runtime event at line {line_number}") from exc
                if not isinstance(event, dict):
                    raise DevShelfRunNotFound(f"Gateway runtime event at line {line_number} is not an object")

                sequence = event.get("sequence")
                if not isinstance(sequence, int):
                    sequence = line_number
                ts = event.get("ts") if isinstance(event.get("ts"), str) else None
                raw = event.get("raw") if isinstance(event.get("raw"), dict) else {}

                error_text = self._runtime_error_summary(raw)
                if error_text:
                    flush_assistant()
                    messages.append(
                        DevShelfGatewayTranscriptMessage(
                            role="error",
                            kind="error",
                            text=error_text,
                            sequence_start=sequence,
                            sequence_end=sequence,
                            ts=ts,
                        )
                    )
                    continue

                running_service = self._fallback_running_service_payload(raw)
                if running_service is not None:
                    flush_assistant()
                    messages.append(
                        DevShelfGatewayTranscriptMessage(
                            role="assistant",
                            kind="running_service",
                            text=self._running_service_transcript_text(running_service),
                            sequence_start=sequence,
                            sequence_end=sequence,
                            ts=ts,
                        )
                    )
                    continue

                snapshot = self._runtime_assistant_snapshot(raw)
                delta = self._runtime_assistant_delta(event, raw)
                if snapshot is not None or delta:
                    if assistant_start is None:
                        assistant_start = sequence
                        assistant_ts = ts
                    assistant_end = sequence
                    if snapshot is not None:
                        assistant_text = snapshot
                    else:
                        assistant_text += delta
                    continue

                tool_text = self._runtime_tool_summary(raw)
                if tool_text:
                    flush_assistant()
                    key = f"{event.get('kind')}:{tool_text}"
                    if key not in seen_tool_messages:
                        seen_tool_messages.add(key)
                        messages.append(
                            DevShelfGatewayTranscriptMessage(
                                role="tool",
                                kind="tool",
                                text=tool_text,
                                sequence_start=sequence,
                                sequence_end=sequence,
                                ts=ts,
                            )
                        )
                    continue

                status_text = self._runtime_status_summary(raw)
                if status_text:
                    flush_assistant()
                    messages.append(
                        DevShelfGatewayTranscriptMessage(
                            role="system",
                            kind="status",
                            text=status_text,
                            sequence_start=sequence,
                            sequence_end=sequence,
                            ts=ts,
                        )
                    )
                    continue

                if event.get("kind") == "stderr" and isinstance(raw.get("line"), str):
                    flush_assistant()
                    messages.append(
                        DevShelfGatewayTranscriptMessage(
                            role="error",
                            kind="stderr",
                            text=raw["line"],
                            sequence_start=sequence,
                            sequence_end=sequence,
                            ts=ts,
                        )
                    )

        flush_assistant()
        return messages[-200:], event_count


    def _runtime_assistant_delta(self, event: dict[str, Any], raw: dict[str, Any]) -> str:
        if event.get("kind") == "text" and isinstance(raw.get("delta"), str):
            return raw["delta"]
        assistant_event = raw.get("assistantMessageEvent")
        if isinstance(assistant_event, dict) and assistant_event.get("type") == "text_delta":
            partial = assistant_event.get("partial")
            if isinstance(partial, dict) and partial.get("role") not in {None, "assistant"}:
                return ""
            delta = assistant_event.get("delta")
            return delta if isinstance(delta, str) else ""
        return ""


    def _runtime_error_payload(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        message = self._runtime_error_summary(raw)
        if not message:
            return None
        stop_reason = raw.get("stopReason") if isinstance(raw.get("stopReason"), str) else None
        return {
            "message": message,
            "stop_reason": stop_reason,
            "source": "assistant",
        }


    def _runtime_error_summary(self, raw: dict[str, Any]) -> str:
        message = raw.get("errorMessage") if isinstance(raw.get("errorMessage"), str) else None
        stop_reason = raw.get("stopReason") if isinstance(raw.get("stopReason"), str) else None
        if not message and stop_reason == "error":
            message = "Assistant response stopped with error."
        return message or ""


    def _running_service_transcript_text(self, service: dict[str, Any]) -> str:
        lines = [f"{service.get('service_name') or '运行服务'} 已启动。"]
        if service.get("url"):
            lines.append(f"地址：[{service['url']}]({service['url']})")
        if service.get("port"):
            lines.append(f"端口：{service['port']}")
        if service.get("command"):
            lines.append(f"命令：`{service['command']}`")
        if service.get("cwd"):
            lines.append(f"目录：`{service['cwd']}`")
        return "\n\n".join(lines)


    def _runtime_assistant_snapshot(self, raw: dict[str, Any]) -> str | None:
        message = raw.get("message")
        assistant_event = raw.get("assistantMessageEvent")
        if not isinstance(message, dict) and isinstance(assistant_event, dict):
            message = assistant_event.get("partial")
        if not isinstance(message, dict):
            return None
        if message.get("role") not in {None, "assistant"}:
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        return "\n\n".join(text_parts) if text_parts else None


    def _summarize_gateway_tool_args(self, args: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in args.items():
            if key in {"content", "input"} and isinstance(value, str):
                summary[f"{key}_char_count"] = len(value)
            elif isinstance(value, str):
                summary[key] = value[:300]
            elif isinstance(value, (int, float, bool)) or value is None:
                summary[key] = value
            elif isinstance(value, list):
                summary[key] = {"item_count": len(value)}
            elif isinstance(value, dict):
                summary[key] = {"keys": sorted(str(item_key) for item_key in value.keys())}
        return summary


    def _cursor_from_sse_event_id(self, event_id: str) -> int | None:
        raw = event_id.strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        for part in raw.split(":"):
            if part.isdigit():
                return int(part)
        return None


    def _sse_encode_event(self, event: dict[str, Any]) -> str:
        event_id = str(event.get("event_id") or event.get("cursor") or "")
        event_type = str(event.get("event_type") or "message")
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event_type}")
        lines.extend(f"data: {line}" for line in payload.splitlines() or [""])
        lines.append("")
        lines.append("")
        return "\n".join(lines)


    def _runtime_tool_summary(self, raw: dict[str, Any]) -> str | None:
        if raw.get("command"):
            success = raw.get("success")
            suffix = "失败" if success is False else "完成" if success is True else ""
            return f"{raw.get('command')}{suffix}"

        message = raw.get("message")
        assistant_event = raw.get("assistantMessageEvent")
        if not isinstance(message, dict) and isinstance(assistant_event, dict):
            message = assistant_event.get("partial")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return None
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "toolCall":
                name = item.get("name") or item.get("toolName") or "tool"
                return f"工具调用：{name}"
            if item.get("type") == "toolResult":
                name = item.get("toolName") or item.get("name") or "tool"
                is_error = item.get("isError") is True
                return f"工具结果：{name}{' 失败' if is_error else ''}"
        return None


    def _runtime_status_summary(self, raw: dict[str, Any]) -> str | None:
        event_type = raw.get("type")
        if event_type == "agent_start":
            return "Agent 已启动"
        if event_type == "turn_start":
            return "开始处理本轮任务"
        if event_type == "turn_end":
            return "本轮任务处理完成"
        if event_type == "lifecycle_abort":
            return "运行已中止"
        return None


    def _gateway_artifact_payload(
        self,
        run_id: str,
        session_id: str | None,
        *,
        metadata_field: str,
        default_name: str,
        preview_candidates: bool = False,
    ) -> DevShelfGatewayArtifactPayload:
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        metadata = self._load_json(session_dir / "session-metadata.json") or {}
        path = self._gateway_metadata_path(session_dir, metadata.get(metadata_field), default_name=default_name)
        payload = self._load_json(path) if path.is_file() else None
        status = self._gateway_status_from_session(session_dir)
        if preview_candidates and isinstance(payload, dict):
            if status.status != "completed":
                payload = {
                    **payload,
                    "candidates": [],
                    "preview_artifacts": [],
                    "summary": {
                        **(payload.get("summary") if isinstance(payload.get("summary"), dict) else {}),
                        "candidate_count": 0,
                        "review_required_candidate_count": 0,
                    },
                    "skipped_reason": f"gateway_status_{status.status}",
                }
            elif not self._gateway_status_matches_latest_packet(run_dir, status):
                payload = {
                    **payload,
                    "candidates": [],
                    "preview_artifacts": [],
                    "summary": {
                        **(payload.get("summary") if isinstance(payload.get("summary"), dict) else {}),
                        "candidate_count": 0,
                        "review_required_candidate_count": 0,
                    },
                    "skipped_reason": "gateway_packet_stale",
                }
            else:
                payload = self._with_candidate_artifact_previews(run_dir, payload)
        return DevShelfGatewayArtifactPayload(
            run_id=status.run_id,
            gateway_session_id=status.gateway_session_id,
            path=str(path) if path.exists() else None,
            payload=payload,
        )


    def _gateway_status_matches_latest_packet(self, run_dir: Path, status: DevShelfGatewaySessionStatus) -> bool:
        latest_packet = self._latest_packet(run_dir)
        if latest_packet is None or not latest_packet.path:
            return False
        if not status.packet_path:
            return False
        return self._same_path(status.packet_path, latest_packet.path)


    def _gateway_candidate_path(self, session_dir: Path) -> Path:
        metadata = self._load_json(session_dir / "session-metadata.json") or {}
        path = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_event_candidates_json"),
            default_name="gateway-event-candidates.json",
        )
        if not path.is_file():
            raise DevShelfRunNotFound(f"Gateway candidate file not found: {path}")
        return path


    def _gateway_candidate_from_file(self, candidate_path: Path, candidate_id: str) -> dict[str, Any]:
        payload = self._load_json(candidate_path)
        if not isinstance(payload, dict):
            raise DevShelfGatewayConflict("Gateway candidate file is invalid.")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise DevShelfGatewayConflict("Gateway candidate file has no candidates.")
        for item in candidates:
            if isinstance(item, dict) and item.get("candidate_id") == candidate_id:
                return item
        raise DevShelfRunNotFound(f"Gateway candidate not found: {candidate_id}")


    def _with_candidate_artifact_previews(self, run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        generated_at = payload.get("generated_at")
        previews: list[dict[str, Any]] = []
        for candidate in payload.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            preview = self._candidate_artifact_preview(run_dir, candidate, generated_at=generated_at)
            if preview is not None:
                previews.append(preview)
        enriched["preview_artifacts"] = previews
        return enriched


    def _candidate_artifact_preview(
        self,
        run_dir: Path,
        candidate: dict[str, Any],
        *,
        generated_at: Any,
    ) -> dict[str, Any] | None:
        event = candidate.get("event") if isinstance(candidate.get("event"), dict) else {}
        source_output = candidate.get("source_output") if isinstance(candidate.get("source_output"), dict) else {}
        artifact_id = (
            candidate.get("artifact_id")
            or event.get("artifact_id")
            or source_output.get("artifact_id")
            or candidate.get("candidate_id")
        )
        if not artifact_id:
            return None

        raw_path = event.get("path") or self._first_declared_output_path(source_output)
        content, content_format, truncated, error = self._preview_artifact_content(run_dir, raw_path)
        status = event.get("artifact_status") or source_output.get("status_on_produce") or "draft"
        return {
            "artifact_id": str(artifact_id),
            "title": str(event.get("title") or source_output.get("title") or artifact_id),
            "status": str(status),
            "path": str(raw_path) if raw_path else None,
            "produced_by": event.get("produced_by"),
            "updated_at": generated_at if isinstance(generated_at, str) else None,
            "content": content,
            "content_format": content_format,
            "content_truncated": truncated,
            "content_error": error,
            "review_required": bool(candidate.get("review_required") or source_output.get("review_required")),
            "candidate_id": candidate.get("candidate_id"),
            "source": "gateway_candidate",
        }


    def _first_declared_output_path(self, source_output: dict[str, Any]) -> str | None:
        declared_paths = source_output.get("declared_paths")
        if not isinstance(declared_paths, list):
            return None
        for item in declared_paths:
            if isinstance(item, dict) and item.get("path"):
                return str(item["path"])
        return None
